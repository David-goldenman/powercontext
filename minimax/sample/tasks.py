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

    min_priority is an inclusive lower bound on task priority and is combined
    with the status filter; None keeps the baseline behaviour. Only ints in
    1..5 are accepted (bools are rejected). The value is validated before any
    row is inspected, so invalid input raises ValueError even for empty tasks.
    """
    if min_priority is not None:
        if isinstance(min_priority, bool) or not isinstance(min_priority, int):
            raise ValueError(
                f'min_priority must be an int between 1 and 5, got {min_priority!r}'
            )
        if not 1 <= min_priority <= 5:
            raise ValueError(
                f'min_priority must be between 1 and 5, got {min_priority!r}'
            )
    selected = [
        dict(task)
        for task in tasks
        if (status is None or task['status'] == status)
        and (min_priority is None or task['priority'] >= min_priority)
    ]
    return sorted(selected, key=lambda task: (-task['priority'], task['id']))
