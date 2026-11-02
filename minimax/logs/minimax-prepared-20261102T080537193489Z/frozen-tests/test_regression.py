"""Baseline contract; an omitted filter, mutation, or changed ordering breaks these."""
import unittest
from sample.tasks import INITIAL_TASKS, list_tasks


class RegressionTests(unittest.TestCase):
    def test_default_priority_then_id_order(self):
        self.assertEqual([t['id'] for t in list_tasks(INITIAL_TASKS)], [2, 3, 1, 4])

    def test_status_filter_preserves_order(self):
        self.assertEqual([t['id'] for t in list_tasks(INITIAL_TASKS, status='open')], [2, 1, 4])

    def test_empty_and_no_status_match(self):
        self.assertEqual(list_tasks([]), [])
        self.assertEqual(list_tasks(INITIAL_TASKS, status='archived'), [])

    def test_input_is_not_mutated_and_results_are_copies(self):
        rows = [dict(t) for t in INITIAL_TASKS]
        result = list_tasks(rows)
        result[0]['title'] = 'changed'
        self.assertEqual(rows, INITIAL_TASKS)

    def test_none_filter_preserves_baseline(self):
        self.assertEqual(list_tasks(INITIAL_TASKS, min_priority=None), list_tasks(INITIAL_TASKS))
