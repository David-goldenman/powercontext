"""Offline adapter boundary tests: no HTTP request or model result is mocked as real."""
import unittest
import json
from pathlib import Path
import tempfile
from minimax_change import (ROOT, assert_frozen_tests, decode_proposal, freeze_tests,
                           public_content, record_status, test_environment, validate_test_run)


class AdapterTests(unittest.TestCase):
    def test_discards_reasoning_blocks(self):
        self.assertEqual(public_content('<think>private</think>{"path":"sample/tasks.py","content":"pass\\n"}'),
                         '{"path":"sample/tasks.py","content":"pass\\n"}')

    def test_rejects_unclosed_reasoning(self):
        with self.assertRaises(ValueError):
            public_content('<think>private and truncated')

    def test_rejects_other_paths_and_invalid_python(self):
        for content in ('{"path":"tests/test_target.py","content":"pass"}',
                        '{"path":"sample/tasks.py","content":"def broken("}'):
            with self.subTest(content=content), self.assertRaises((ValueError, SyntaxError)):
                decode_proposal(content)

    def test_accepts_public_python_proposal(self):
        # A parser fixture, not a purported MiniMax-generated implementation.
        self.assertEqual(decode_proposal('{"path":"sample/tasks.py","content":"# parser fixture\\n"}'),
                         '# parser fixture\n')

    def test_test_environment_contains_no_model_credentials(self):
        environment = test_environment()
        self.assertNotIn('MINIMAX_API_KEY', environment)
        self.assertNotIn('OPENAI_API_KEY', environment)
        self.assertEqual(environment['PYTHONNOUSERSITE'], '1')

    def test_zero_tests_and_skips_cannot_be_verified(self):
        for output in ('Ran 0 tests in 0.001s\n\nOK',
                       'Ran 5 tests in 0.001s\n\nOK (skipped=5)'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                validate_test_run(output, exit_code=0, expected_count=5, expected_failures=0)

    def test_frozen_test_source_change_is_detected(self):
        (ROOT / 'runtime').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='adapter-fixture-', dir=ROOT / 'runtime') as folder:
            directory = Path(folder)
            freeze_tests(directory)
            assert_frozen_tests(directory)
            (directory / 'frozen-tests' / 'test_target.py').write_text('# changed fixture\n')
            with self.assertRaises(ValueError):
                assert_frozen_tests(directory)

    def test_sanitized_receipt_survives_rejection(self):
        (ROOT / 'runtime').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='adapter-fixture-', dir=ROOT / 'runtime') as folder:
            metadata = {'api_called': False, 'fixture': True}
            record_status(Path(folder), metadata, 'PROPOSAL_REJECTED', phase='parse', error_type='ValueError')
            saved = json.loads((Path(folder) / 'status.json').read_text())
            self.assertEqual(saved['status'], 'PROPOSAL_REJECTED')
            self.assertFalse(saved['api_called'])
