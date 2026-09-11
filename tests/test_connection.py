import sqlite3
import unittest
from datetime import date
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connection import ConnectionStore, canonical_pair


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.store = ConnectionStore(self.db)

    def test_pair_is_global_and_order_independent(self):
        self.assertEqual(canonical_pair(20, 10), (10, 20))
        self.store.record_connection(1, 2, date(2026, 9, 11), 100, 200, 300)
        self.assertEqual(self.store.get_pair(2, 1)["current_connections"], 1)
        self.assertEqual(self.store.get_pair(1, 2)["lifetime_connections"], 1)

    def test_same_pair_only_counts_once_per_day(self):
        self.assertTrue(self.store.record_connection(1, 2, date(2026, 9, 11), 1, 2, 3))
        self.assertFalse(self.store.record_connection(2, 1, date(2026, 9, 11), 4, 5, 6))
        self.assertEqual(self.store.get_pair(1, 2)["current_connections"], 1)

    def test_consecutive_days_update_streak(self):
        self.store.record_connection(1, 2, date(2026, 9, 10), 1, 2, 3)
        self.store.record_connection(1, 2, date(2026, 9, 11), 1, 2, 3)
        self.assertEqual(self.store.get_pair(1, 2)["streak"], 2)

    def test_three_missed_days_decay_current_but_not_lifetime(self):
        self.store.record_connection(1, 2, date(2026, 9, 1), 1, 2, 3)
        self.store.record_connection(1, 2, date(2026, 9, 2), 1, 2, 3)
        self.store.record_connection(1, 2, date(2026, 9, 3), 1, 2, 3)
        self.store.apply_decay(date(2026, 9, 6))
        pair = self.store.get_pair(1, 2)
        self.assertEqual(pair["current_connections"], 1)
        self.assertEqual(pair["lifetime_connections"], 3)
        self.assertEqual(pair["streak"], 0)
        self.assertEqual(len(self.store.decay_history(1, 2)), 1)

    def test_decay_is_not_applied_twice_for_same_gap(self):
        self.store.record_connection(1, 2, date(2026, 9, 1), 1, 2, 3)
        self.store.apply_decay(date(2026, 9, 4))
        self.store.apply_decay(date(2026, 9, 5))
        self.assertEqual(self.store.get_pair(1, 2)["current_connections"], 0)
        self.assertEqual(len(self.store.decay_history(1, 2)), 1)


if __name__ == "__main__":
    unittest.main()
