"""Newly authored task-list baseline. Future MiniMax task: implement min_priority."""
BASELINE_VERSION = 'task-list-baseline-v1'

INITIAL_TASKS = [
    {'id': 1, 'title': 'Write notes', 'status': 'open', 'priority': 2},
    {'id': 2, 'title': 'Fix login', 'status': 'open', 'priority': 4},
    {'id': 3, 'title': 'Ship release', 'status': 'done', 'priority': 4},
    {'id': 4, 'title': 'Tidy backlog', 'status': 'open', 'priority': 1},
]


def list_tasks(tasks, *, status=None, min_priority=None):
    """Return copies, optionally by status, sorted by priority DESC then id ASC.

    min_priority is reserved for the next development task and is not implemented.
    This explicit error is the missing new feature, not a historical defect.
    """
    if min_priority is not None:
        raise NotImplementedError('min_priority is a future MiniMax implementation task')
    selected = [dict(task) for task in tasks if status is None or task['status'] == status]
    return sorted(selected, key=lambda task: (-task['priority'], task['id']))
