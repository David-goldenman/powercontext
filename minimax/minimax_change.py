"""Future MiniMax proposal followed by a separate, explicit reviewed-code apply step."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from context_cli import ROOT, build_context, import_sources, local_path, task_state

TEST_FILES = ('test_regression.py', 'test_target.py')


def public_content(content: str) -> str:
    cleaned = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE)
    if re.search(r'</?think\b', cleaned, flags=re.IGNORECASE):
        raise ValueError('incomplete reasoning block; response was not saved')
    return cleaned.strip()


def decode_proposal(content: str) -> str:
    proposal = json.loads(public_content(content))
    if set(proposal) != {'path', 'content'} or proposal['path'] != 'sample/tasks.py':
        raise ValueError('proposal may only replace sample/tasks.py')
    code = proposal['content']
    if not isinstance(code, str) or not code.strip():
        raise ValueError('proposal must contain Python source')
    compile(code, 'sample/tasks.py', 'exec')
    return code


def test_environment() -> dict[str, str]:
    # Deliberate allowlist: no inherited API keys, proxy credentials, or user config.
    temporary = ROOT / '_work' / 'tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    environment = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                   'PYTHONPATH': str(ROOT), 'TMPDIR': str(temporary), 'TEMP': str(temporary), 'TMP': str(temporary)}
    if os.name == 'nt' and 'SystemRoot' in os.environ:
        environment['SystemRoot'] = os.environ['SystemRoot']
    return environment


def validate_test_run(output: str, *, exit_code: int, expected_count: int, expected_failures: int) -> dict:
    found = re.search(r'^Ran (\d+) tests? in ', output, flags=re.MULTILINE)
    if not found or int(found[1]) != expected_count or re.search(r'\bskipped=|\.\.\. skipped', output):
        raise ValueError('test discovery count changed or tests were skipped')
    if expected_failures:
        if exit_code != 1 or f'FAILED (failures={expected_failures})' not in output:
            raise ValueError('expected only the declared target-feature assertion failures')
    elif exit_code != 0 or not re.search(r'^OK$', output, flags=re.MULTILINE):
        raise ValueError('tests did not all pass')
    return {'tests_run': int(found[1]), 'failures': expected_failures, 'errors': 0, 'skipped': 0}


def freeze_tests(directory: Path):
    folder = directory / 'frozen-tests'
    folder.mkdir()
    for name in TEST_FILES:
        (folder / name).write_text((ROOT / 'tests' / name).read_text(encoding='utf-8'), encoding='utf-8')


def assert_frozen_tests(directory: Path):
    for name in TEST_FILES:
        if (directory / 'frozen-tests' / name).read_text(encoding='utf-8') != (ROOT / 'tests' / name).read_text(encoding='utf-8'):
            raise ValueError('frozen test source changed: ' + name)


def record_status(directory: Path, metadata: dict, status: str, **fields):
    metadata.update(status=status, **fields)
    (directory / 'status.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')


def run_test(pattern: str, directory: Path, phase: str) -> dict:
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v']
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            env=test_environment(), timeout=60)
    output = result.stdout + result.stderr
    (directory / (phase + '.txt')).write_text(output, encoding='utf-8')
    return {'command': command, 'exit_code': result.returncode, 'code_snapshot':
            'before.py' if phase.startswith('before') else 'after.py', 'output': output}


def safe_result(run: dict) -> dict:
    return {key: value for key, value in run.items() if key != 'output'}


def persist_failure(task: str, store: str, directory: Path, metadata: dict, error: Exception):
    record_status(directory, metadata, 'FAILED', error_type=type(error).__name__)
    try:
        prior = asyncio.run(task_state(task, store)) or {}
        asyncio.run(task_state(task, store, {'completed': prior.get('completed', []),
            'todo': ['Review ' + directory.relative_to(ROOT).as_posix() + ' at phase ' + metadata.get('phase', 'unknown')],
            'verification': ['Failure type: ' + type(error).__name__, 'No success claimed']}))
    except Exception as state_error:
        record_status(directory, metadata, 'FAILED', state_write_error_type=type(state_error).__name__)


def apply_reviewed(directory: Path, task: str, store: str) -> int:
    metadata = json.loads((directory / 'status.json').read_text(encoding='utf-8'))
    if metadata.get('status') != 'PROPOSAL_AWAITING_REVIEW' or metadata.get('task') != task:
        raise ValueError('select an unapplied real proposal for this task')
    try:
        record_status(directory, metadata, 'APPLY_REVIEWED_STARTED', phase='reviewed_apply')
        assert_frozen_tests(directory)
        if local_path('sample/tasks.py').read_text(encoding='utf-8') != (directory / 'before.py').read_text(encoding='utf-8'):
            raise ValueError('business baseline changed since proposal; obtain a fresh context')
        code = (directory / 'after.py').read_text(encoding='utf-8')
        compile(code, 'sample/tasks.py', 'exec')
        # This is reviewed local code, not a sandbox. The parent must inspect it
        # before invoking this distinct mode. The model response is never auto-run.
        local_path('sample/tasks.py').write_text(code, encoding='utf-8')
        record_status(directory, metadata, 'REVIEWED_CODE_APPLIED', phase='tests', code_applied=True)
        runs = [run_test('test_regression.py', directory, 'after-regression'),
                run_test('test_target.py', directory, 'after-target')]
        metadata['after'] = [safe_result(run) for run in runs]
        assert_frozen_tests(directory)
        for run in runs:
            validate_test_run(run['output'], exit_code=run['exit_code'], expected_count=5, expected_failures=0)
        record_status(directory, metadata, 'TESTS_PASSED', phase='reimport')
        import_sources(task, 'sample/manifest-v2.json')
        record_status(directory, metadata, 'TESTS_PASSED', phase='save_state')
        asyncio.run(task_state(task, store, {'completed': ['Reviewed real MiniMax proposal applied; fixed tests passed'],
            'todo': [], 'verification': [json.dumps(safe_result(run)) for run in runs]}))
        record_status(directory, metadata, 'VERIFIED', phase='complete')
        return 0
    except Exception as error:
        persist_failure(task, store, directory, metadata, error)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', default='priority-filter')
    parser.add_argument('--store', default='runtime/task-state.sqlite3')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true', help='make ONE real API request; save proposal and STOP for code review')
    mode.add_argument('--apply-reviewed', metavar='PROPOSAL_DIRECTORY', help='apply a proposal after parent code review; no API call')
    args = parser.parse_args()
    if args.apply_reviewed:
        return apply_reviewed(local_path(args.apply_reviewed), args.task, args.store)
    context = build_context(args.task, args.store)
    prompt = ('Implement the active feature specification using the supplied engineering context. '
              'Modify only list_tasks in sample/tasks.py; preserve other definitions and public behavior. '
              'Do not edit tests. Return only JSON with exactly path="sample/tasks.py" and '
              'content=complete Python source. No explanation or private reasoning.\n\n' + json.dumps(context, ensure_ascii=False))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = ROOT / 'logs' / ('minimax-' + ('real-' if args.execute else 'prepared-') + stamp)
    directory.mkdir(parents=True)
    (directory / 'context.json').write_text(json.dumps(context, indent=2, ensure_ascii=False), encoding='utf-8')
    (directory / 'prompt.txt').write_text(prompt, encoding='utf-8')
    metadata = {'api_called': False, 'request_attempted': False, 'api_response_received': False,
                'task': args.task, 'timestamp_utc': stamp}
    record_status(directory, metadata, 'PREPARED_ONLY', phase='prepare')
    if not args.execute:
        print(json.dumps({'prepared': directory.relative_to(ROOT).as_posix(), 'api_called': False}))
        return 0
    try:
        record_status(directory, metadata, 'PREFLIGHT', phase='preflight')
        if os.environ.get('MINIMAX_QUOTA_CONFIRMED') != '1':
            raise ValueError('Confirm usable existing MiniMax quota before execution')
        key, model = os.environ.get('MINIMAX_API_KEY'), os.environ.get('MINIMAX_MODEL')
        endpoint = os.environ.get('MINIMAX_API_URL', 'https://api.minimax.cn/v1/chat/completions')
        if endpoint not in {'https://api.minimax.cn/v1/chat/completions', 'https://api.minimax.io/v1/chat/completions'}:
            raise ValueError('use an official MiniMax API endpoint')
        if not key or not model:
            raise ValueError('MINIMAX_API_KEY and MINIMAX_MODEL are required')
        baseline = local_path('sample/tasks.py').read_text(encoding='utf-8')
        (directory / 'before.py').write_text(baseline, encoding='utf-8')
        freeze_tests(directory)
        runs = [run_test('test_regression.py', directory, 'before-regression'),
                run_test('test_target.py', directory, 'before-target')]
        validate_test_run(runs[0]['output'], exit_code=runs[0]['exit_code'], expected_count=5, expected_failures=0)
        validate_test_run(runs[1]['output'], exit_code=runs[1]['exit_code'], expected_count=5, expected_failures=5)
        if 'New min_priority feature is not implemented' not in runs[1]['output']:
            raise ValueError('baseline target failure must be due to missing new feature')
        metadata.update(before=[safe_result(run) for run in runs], model_requested=model, endpoint=endpoint)
        payload = {'model': model, 'messages': [
            {'role': 'system', 'content': 'You are a software developer. Return the requested public code JSON only.'},
            {'role': 'user', 'content': prompt}], 'max_completion_tokens': 16384, 'reasoning_split': True}
        request = Request(endpoint, data=json.dumps(payload).encode('utf-8'),
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        record_status(directory, metadata, 'REQUEST_STARTED', phase='request', api_called=True, request_attempted=True)
        with urlopen(request, timeout=240) as response:
            record_status(directory, metadata, 'RESPONSE_RECEIVED', api_response_received=True, phase='decode')
            raw = json.load(response)
        usage = raw.get('usage') or {}
        metadata.update(model_returned=raw.get('model'), response_id=raw.get('id'),
                        usage={k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens') if k in usage})
        record_status(directory, metadata, 'RESPONSE_DECODED', phase='proposal')
        choice = raw['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('incomplete model response')
        code = decode_proposal(choice['message']['content'])
        (directory / 'after.py').write_text(code, encoding='utf-8')
        (directory / 'change.diff').write_text(''.join(difflib.unified_diff(
            baseline.splitlines(True), code.splitlines(True), 'before/sample/tasks.py', 'after/sample/tasks.py')), encoding='utf-8')
        assert_frozen_tests(directory)
        record_status(directory, metadata, 'PROPOSAL_AWAITING_REVIEW', phase='review', code_applied=False)
        asyncio.run(task_state(args.task, args.store, {'completed': ['Real MiniMax proposal received; business code unchanged'],
            'todo': ['Parent must review after.py and change.diff before separate --apply-reviewed'],
            'verification': ['Baseline regression passed; target remains RED; no generated code executed']}))
        print(json.dumps({'status': metadata['status'], 'proposal': directory.relative_to(ROOT).as_posix(), 'code_applied': False}))
        return 0
    except Exception as error:
        persist_failure(args.task, args.store, directory, metadata, error)
        raise


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except HTTPError as error:
        print(f'MiniMax HTTP error {error.code}; no error body or credentials logged', file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f'{type(error).__name__}: operation failed; inspect sanitized status.json (no secrets logged)', file=sys.stderr)
        raise SystemExit(1)
