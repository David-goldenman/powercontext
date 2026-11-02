"""Frozen future-feature contract: run this identical file before and after MiniMax."""
import unittest
from sample.tasks import INITIAL_TASKS, list_tasks


class TargetTests(unittest.TestCase):
    def call_feature(self, rows, **kwargs):
        try:
            return list_tasks(rows, **kwargs)
        except NotImplementedError:
            self.fail('New min_priority feature is not implemented in the business baseline')

    def test_inclusive_threshold(self):
        rows = self.call_feature(INITIAL_TASKS, min_priority=3)
        self.assertEqual([t['id'] for t in rows], [2, 3])

    def test_combines_status_and_priority(self):
        rows = self.call_feature(INITIAL_TASKS, status='open', min_priority=4)
        self.assertEqual([t['id'] for t in rows], [2])

    def test_valid_boundaries(self):
        self.assertEqual([t['id'] for t in self.call_feature(INITIAL_TASKS, min_priority=1)], [2, 3, 1, 4])
        self.assertEqual(self.call_feature(INITIAL_TASKS, min_priority=5), [])

    def test_valid_filter_on_empty_input(self):
        self.assertEqual(self.call_feature([], min_priority=2), [])

    def test_invalid_values_rejected_even_on_empty_input(self):
        for value in (0, 6, -1, True, False, 2.5, '3'):
            with self.assertRaises(ValueError, msg=f'invalid min_priority={value!r}'):
                self.call_feature([], min_priority=value)
