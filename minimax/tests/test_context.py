"""Integration checks use live seekdb and the genuine PowerContext SQLite backend."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


class ContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.task = 'foundation-' + uuid.uuid4().hex[:12]
        cls.store = ROOT / 'runtime' / (cls.task + '.sqlite3')

    def cli(self, *args, success=True, env=None):
        result = subprocess.run([sys.executable, 'context_cli.py', '--store', str(self.store),
                                 *args], cwd=ROOT, capture_output=True, text=True, env=env)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def test_database_sources_spec_reimport_and_removed_content(self):
        first = self.cli('import', '--task', self.task, '--manifest', 'sample/manifest-v1.json')
        self.assertEqual(first['documents'], 4)
        ctx = self.cli('context', '--task', self.task)
        self.assertEqual(ctx['storage']['database'], 'minimax_sdd')
        self.assertIn('v1: initial feature contract', ctx['sources'][0]['content'])
        self.assertIsNone(ctx['state'])
        self.assertEqual({s['kind'] for s in ctx['sources']}, {'spec', 'code', 'test'})
        for source in ctx['sources']:
            self.assertTrue((ROOT / source['path']).is_file())
            self.assertEqual(source['line_start'], 1)
            self.assertGreater(source['line_end'], 0)
        self.cli('import', '--task', self.task, '--manifest', 'sample/manifest-v2.json')
        updated = self.cli('context', '--task', self.task)
        text = '\n'.join(s['content'] for s in updated['sources'])
        self.assertIn('v2: validate before scanning rows', text)
        self.assertNotIn('v1: initial feature contract', text)
        self.assertEqual(updated['sources'][0]['path'], 'sample/spec-v2.md')

    def test_powercontext_write_then_new_process_uses_state(self):
        task = self.task + '-resume'
        self.cli('import', '--task', task, '--manifest', 'sample/manifest-v2.json')
        self.assertIsNone(self.cli('context', '--task', task)['state'])
        saved = self.cli('save-state', '--task', task, '--completed', 'baseline regression passed',
                         '--todo', 'MiniMax must implement min_priority', '--verification', 'target RED: 5 failures')
        self.assertTrue(saved['scope_id'].startswith('scp_'))
        resumed = self.cli('context', '--task', task)
        self.assertNotEqual(saved['process_id'], resumed['process_id'])
        self.assertEqual(resumed['state']['completed'], ['baseline regression passed'])
        self.assertEqual(resumed['state']['todo'], ['MiniMax must implement min_priority'])
        self.assertIn('MiniMax must implement min_priority', resumed['next_action'])
        self.assertIn('target RED: 5 failures', resumed['state']['verification'])

    def test_missing_source_is_rejected(self):
        task = self.task + '-missing'
        path = ROOT / 'runtime' / (task + '.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'sources': [{'kind': 'code', 'path': 'sample/absent.py'}]}))
        failed = self.cli('import', '--task', task, '--manifest', str(path), success=False)
        self.assertIn('absent.py', failed.stderr)

    def test_database_allowlist_rejects_other_schema(self):
        env = dict(os.environ, SEEKDB_DATABASE='other_project')
        failed = self.cli('import', '--task', self.task, '--manifest', 'sample/manifest-v1.json', env=env, success=False)
        self.assertIn('minimax_sdd', failed.stderr)

    def test_stale_source_is_rejected_then_reimported(self):
        task = self.task + '-stale'
        source = ROOT / 'runtime' / (task + '.md')
        manifest = ROOT / 'runtime' / (task + '.json')
        source.parent.mkdir(exist_ok=True)
        source.write_text('Original specification\n')
        manifest.write_text(json.dumps({'sources': [{'kind': 'spec', 'path': source.relative_to(ROOT).as_posix()}]}))
        self.cli('import', '--task', task, '--manifest', str(manifest))
        source.write_text('Updated specification\n')
        failed = self.cli('context', '--task', task, success=False)
        self.assertIn('stale source', failed.stderr)
        self.cli('import', '--task', task, '--manifest', str(manifest))
        self.assertEqual(self.cli('context', '--task', task)['sources'][0]['content'], 'Updated specification\n')

    def test_state_update_retains_binding_and_latest_progress(self):
        task = self.task + '-update'
        self.cli('import', '--task', task, '--manifest', 'sample/manifest-v2.json')
        first = self.cli('save-state', '--task', task, '--todo', 'first step')
        second = self.cli('save-state', '--task', task, '--completed', 'first step', '--todo', 'next step')
        context = self.cli('context', '--task', task)
        self.assertEqual(first['scope_id'], second['scope_id'])
        self.assertEqual(second['scope_version'], first['scope_version'] + 1)
        self.assertEqual(context['next_action'], 'next step')

    def test_powercontext_creates_fresh_nested_store_parent(self):
        task = self.task + '-nested'
        self.cli('import', '--task', task, '--manifest', 'sample/manifest-v2.json')
        store = 'runtime/' + task + '/fresh/nested/state.sqlite3'
        context = self.cli('--store', store, 'context', '--task', task)
        self.assertIsNone(context['state'])
        self.assertTrue((ROOT / store).is_file())
