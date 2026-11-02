# Task listing specification — v1: initial feature contract

Business baseline: this small application was authored for this submission.
It lists tasks with id, title, status and integer priority 1..5.

## Existing behavior that must remain
- SDD-BASE-1: list_tasks supports exact status filtering; None returns all statuses.
- SDD-BASE-2: order by priority descending, then integer id ascending.
- SDD-BASE-3: return copied dictionaries without mutating input; empty input returns [].

## Future MiniMax change
- SDD-NEW-1: implement optional min_priority inclusive lower bound, combined with status.
- SDD-NEW-2: None preserves existing results; accept only int 1..5, excluding bool.
- SDD-NEW-3: reject invalid values with ValueError, including on empty input.
- SDD-NEW-4: valid min_priority with no matching tasks returns [].

Modify sample/tasks.py::list_tasks only. Keep tests/test_target.py and
tests/test_regression.py unchanged during the actual model change.
