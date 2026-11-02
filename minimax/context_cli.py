"""Exact task-source retrieval in seekdb and genuine PowerContext Scope persistence."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from typing import Any

import pymysql

ROOT = Path(__file__).resolve().parent
DATABASE = 'minimax_sdd'
SOURCE_QUERY = ('SELECT kind,path,symbol,line_start,line_end,content FROM sdd_documents '
                'WHERE task_id=%s ORDER BY position')


def local_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('source/store path must stay inside minimax module')
    return path


@contextmanager
def database(*, initialize: bool = False):
    configured = os.environ.get('SEEKDB_DATABASE', DATABASE)
    if configured != DATABASE:
        raise ValueError('Only the dedicated minimax_sdd database may be used')
    connection = pymysql.connect(
        host=os.environ.get('SEEKDB_HOST', '127.0.0.1'),
        port=int(os.environ.get('SEEKDB_PORT', '2896')),
        user=os.environ.get('SEEKDB_USER', 'root'),
        password=os.environ.get('SEEKDB_PASSWORD', ''),
        charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=30, write_timeout=30,
    )
    try:
        with connection.cursor() as cursor:
            if initialize:
                cursor.execute('CREATE DATABASE IF NOT EXISTS minimax_sdd')
            cursor.execute('USE minimax_sdd')
            if initialize:
                cursor.execute('''CREATE TABLE IF NOT EXISTS sdd_documents (
                    task_id VARCHAR(128) NOT NULL, position INT NOT NULL,
                    kind VARCHAR(16) NOT NULL, path VARCHAR(512) NOT NULL,
                    symbol VARCHAR(256) NOT NULL, line_start INT NOT NULL,
                    line_end INT NOT NULL, content LONGTEXT NOT NULL,
                    PRIMARY KEY(task_id, position))''')
        connection.commit()
        yield connection
    finally:
        connection.close()


def import_sources(task: str, manifest: str) -> dict[str, Any]:
    entries = json.loads(local_path(manifest).read_text(encoding='utf-8'))['sources']
    rows = []
    seen = set()
    for position, entry in enumerate(entries):
        path = local_path(entry['path'])
        relative = path.relative_to(ROOT).as_posix()
        if entry['kind'] not in {'spec', 'code', 'test'} or relative in seen:
            raise ValueError('source kinds must be spec/code/test and paths unique')
        seen.add(relative)
        content = path.read_text(encoding='utf-8')
        if not content.strip():
            raise ValueError('source must contain real text: ' + relative)
        rows.append((task, position, entry['kind'], relative, entry.get('symbol', ''),
                     1, len(content.splitlines()), content))
    if not rows:
        raise ValueError('manifest must contain sources')
    with database(initialize=True) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM sdd_documents WHERE task_id=%s', (task,))
                cursor.executemany('INSERT INTO sdd_documents VALUES (%s,%s,%s,%s,%s,%s,%s,%s)', rows)
                cursor.execute('SELECT VERSION() AS version')
                version = cursor.fetchone()['version']
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {'task': task, 'documents': len(rows), 'database': DATABASE, 'server_version': version}


def query_sources(task: str) -> list[dict[str, Any]]:
    with database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(SOURCE_QUERY, (task,))
            rows = list(cursor.fetchall())
    if not rows:
        raise ValueError('task has no indexed documents; import its manifest first')
    for source in rows:
        content = local_path(source['path']).read_text(encoding='utf-8')
        if content != source['content']:
            raise ValueError('stale source; reimport before context: ' + source['path'])
    return rows


async def task_state(task: str, store: str, progress: dict[str, Any] | None = None):
    # Imports and storage are the genuine upstream PowerContext implementation.
    from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
    from powercontext.builtin.persistence.tables import BUILTIN_TABLES
    from powercontext.builtin.scope import ScopeApplication, ScopeBindingKey, ScopeDraft, ScopeMutation

    path = local_path(store)
    async with SQLiteProfile.open(SQLiteConfig(url='sqlite+aiosqlite:///' + path.as_posix()),
                                  tables=BUILTIN_TABLES) as profile:
        scopes = ScopeApplication(profile.database)
        key = ScopeBindingKey(integration='minimax-sdd', kind='task', external_id=task)
        binding = await scopes.binding(key)
        current = None if binding is None else await scopes.get(binding.scope_id)
        if progress is not None:
            summary = json.dumps(progress, ensure_ascii=False)
            if current is None:
                current = await scopes.create(ScopeDraft(title=task, summary=summary,
                                                        idempotency_key='minimax-sdd:' + task))
                await scopes.bind(key, current.scope_id)
            else:
                current = await scopes.update(current.scope_id, ScopeMutation(
                    expected_version=current.version, title=task, summary=summary))
        if current is None:
            return None
        return {'scope_id': current.scope_id, 'scope_version': current.version,
                **json.loads(current.summary)}


def build_context(task: str, store: str) -> dict[str, Any]:
    sources = query_sources(task)
    state = asyncio.run(task_state(task, store))
    todo = state['todo'] if state else ['Implement min_priority using MiniMax, then run unchanged tests']
    return {'task': task, 'process_id': os.getpid(), 'sources': sources, 'state': state,
            'next_action': '; '.join(todo),
            'storage': {'database': DATABASE, 'query': SOURCE_QUERY, 'parameters': [task],
                        'rows_returned': len(sources), 'powercontext_backend': 'SQLiteProfile/ScopeApplication'}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', default='runtime/task-state.sqlite3')
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('import', 'context', 'save-state'):
        command = subs.add_parser(name)
        command.add_argument('--task', required=True)
        if name == 'import':
            command.add_argument('--manifest', required=True)
        if name == 'save-state':
            command.add_argument('--completed', action='append', default=[])
            command.add_argument('--todo', action='append', default=[])
            command.add_argument('--verification', action='append', default=[])
    args = parser.parse_args()
    if not args.task.strip() or len(args.task) > 120:
        parser.error('task must be non-empty and at most 120 characters')
    try:
        if args.command == 'import':
            result = import_sources(args.task, args.manifest)
        elif args.command == 'context':
            result = build_context(args.task, args.store)
        else:
            result = asyncio.run(task_state(args.task, args.store, {
                'completed': args.completed, 'todo': args.todo, 'verification': args.verification,
                'saved_by_process': os.getpid()}))
            result['process_id'] = os.getpid()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as error:
        # Do not dump connection configuration, env, or API secrets.
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
