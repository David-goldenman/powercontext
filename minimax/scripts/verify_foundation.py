"""Reproduce foundation evidence without using any model API or credential."""
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from minimax_change import validate_test_run


def git_revision(directory):
    command = ['git', 'rev-parse', 'HEAD']
    marker = directory / '.git'
    if sys.platform == 'linux' and marker.is_file():
        # A Windows-created worktree has a drive-qualified gitdir; map it for WSL
        # without rewriting that shared worktree's metadata.
        match = re.match(r'gitdir: ([A-Za-z]):/(.*)', marker.read_text().strip())
        if match:
            mapped = '/mnt/' + match[1].lower() + '/' + match[2]
            command = ['git', '--git-dir=' + mapped, '--work-tree=' + str(directory), 'rev-parse', 'HEAD']
    result = subprocess.run(command, cwd=directory, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Cannot identify code version: ' + result.stderr)
    return result.stdout.strip()


def main():
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = ROOT / 'logs' / ('foundation-' + stamp)
    directory.mkdir(parents=True)
    commands = []
    repository = git_revision(ROOT)
    from powercontext.builtin.scope import ScopeApplication
    import inspect
    sdk_source = Path(inspect.getfile(ScopeApplication)).resolve()
    upstream = sdk_source.parents[4]
    sdk_commit = git_revision(upstream)
    if sdk_commit != '1be152d62a4c6df59709f15b184c6b93085fb76f':
        raise RuntimeError('Use the documented pinned PowerContext version')
    versions = {'python': sys.version, 'python_executable': sys.executable,
                'repository_base_commit': repository, 'code_version': 'task-list-baseline-v1 (uncommitted source snapshot)',
                'powercontext_source': str(sdk_source), 'powercontext_commit': sdk_commit,
                'packages': {name: importlib.metadata.version(name) for name in (
                    'PyMySQL', 'pydantic', 'SQLAlchemy', 'aiosqlite', 'rfc8785', 'typing-extensions', 'sqlite-vec',
                    'annotated-types', 'pydantic-core', 'typing-inspection', 'greenlet', 'PyYAML', 'packaging',
                    'numpy', 'opentelemetry-api', 'opentelemetry-sdk', 'opentelemetry-semantic-conventions')}}
    (directory / 'versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'sample/tasks.py', directory / 'baseline_tasks.py')

    def run(name, arguments, expected=0, expected_tests=None, expected_failures=0):
        command = [sys.executable, *arguments]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        output = result.stdout + result.stderr
        (directory / (name + '.txt')).write_text(output, encoding='utf-8')
        commands.append({'name': name, 'command': command, 'cwd': str(ROOT),
                         'exit_code': result.returncode, 'expected_exit_code': expected,
                         'code_version': versions['code_version'], 'timestamp_utc': datetime.now(timezone.utc).isoformat()})
        if expected_tests is not None:
            commands[-1].update(validate_test_run(output, exit_code=result.returncode,
                                                expected_count=expected_tests, expected_failures=expected_failures))
        (directory / 'commands.json').write_text(json.dumps(commands, indent=2), encoding='utf-8')
        if result.returncode != expected:
            raise RuntimeError(f'{name}: unexpected exit {result.returncode}; see {directory}')
        return output

    run('regression-green', ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_regression.py', '-v'], expected_tests=5)
    target = run('target-red', ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_target.py', '-v'], expected=1, expected_tests=5, expected_failures=5)
    if 'FAILED (failures=5)' not in target or 'New min_priority feature is not implemented' not in target:
        raise RuntimeError('target RED must be exactly five feature assertion failures, without environment errors')
    run('adapter-offline-green', ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_adapter.py', '-v'], expected_tests=8)
    run('context-integration-green', ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_context.py', '-v'], expected_tests=7)
    full = run('full-suite-expected-red', ['-m', 'unittest', 'discover', '-s', 'tests', '-v'], expected=1, expected_tests=25, expected_failures=5)
    if 'FAILED (failures=5)' not in full:
        raise RuntimeError('full suite must fail only the five deliberately pending feature cases')
    task = 'priority-filter-' + uuid.uuid4().hex[:10]
    store = 'runtime/' + task + '.sqlite3'
    common = ['context_cli.py', '--store', store]
    run('db-import-v1', [*common, 'import', '--task', task, '--manifest', 'sample/manifest-v1.json'])
    first = json.loads(run('db-context-v1-empty-state', [*common, 'context', '--task', task]))
    assert first['state'] is None
    run('db-import-v2', [*common, 'import', '--task', task, '--manifest', 'sample/manifest-v2.json'])
    second = json.loads(run('db-context-v2', [*common, 'context', '--task', task]))
    assert first['sources'][0]['path'] == 'sample/spec-v1.md'
    assert second['sources'][0]['path'] == 'sample/spec-v2.md'
    saved = json.loads(run('powercontext-save', [*common, 'save-state', '--task', task,
        '--completed', 'Baseline regression: 5 passed; foundation integration: 7 passed; adapter: 8 passed',
        '--todo', 'Use MiniMax to implement min_priority after user API and quota are available',
        '--verification', 'target-red: exit 1, five failures caused by explicit NotImplementedError']))
    resumed = json.loads(run('powercontext-resume-new-process', [*common, 'context', '--task', task]))
    assert saved['process_id'] != resumed['process_id']
    assert saved['scope_id'] == resumed['state']['scope_id']
    assert 'Use MiniMax' in resumed['next_action']
    run('minimax-prepare-no-api', ['minimax_change.py', '--task', task, '--store', store])
    results = {command['name']: command for command in commands}
    result = {'status': 'FOUNDATION_VERIFIED_MODEL_PENDING', 'api_called': False,
              'regression_passed': results['regression-green']['tests_run'],
              'context_integration_passed': results['context-integration-green']['tests_run'],
              'adapter_offline_passed': results['adapter-offline-green']['tests_run'],
              'target_feature_failures': results['target-red']['failures'],
              'full_suite_exit': results['full-suite-expected-red']['exit_code'],
              'environment_errors': results['full-suite-expected-red']['errors'],
              'task': task, 'store': store, 'scope_id': saved['scope_id'],
              'writer_process': saved['process_id'], 'reader_process': resumed['process_id'],
              'pending': ['MiniMax actual participation', 'Real model code diff', 'Same target tests GREEN after model change']}
    (directory / 'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': directory.relative_to(ROOT).as_posix(), **result}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
