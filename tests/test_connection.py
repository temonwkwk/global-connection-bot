import sqlite3
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connection import ConnectionStore, canonical_pair
from bot import local_date, paginate_items


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.store = ConnectionStore(self.db)

    def test_connection_date_uses_configured_local_timezone(self):
        moment = datetime(2026, 9, 11, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(local_date(moment), date(2026, 9, 12))

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

    def test_koneksi_pages_hold_five_items_and_report_total(self):
        pages = paginate_items(list(range(12)), 5)
        self.assertEqual(pages, [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11]])

    def test_koneksi_empty_list_has_no_pages(self):
        self.assertEqual(paginate_items([], 5), [])

    def test_pair_visibility_setting_defaults_visible_and_persists(self):
        self.assertFalse(self.store.pair_hidden(1, 2))
        self.store.set_pair_hidden(1, 2, True)
        self.assertTrue(self.store.pair_hidden(2, 1))
        self.store.set_pair_hidden(2, 1, False)
        self.assertFalse(self.store.pair_hidden(1, 2))

    def test_reset_pair_also_clears_hidden_setting(self):
        self.store.record_connection(1, 2, date(2026, 9, 11), 1, 2, 3)
        self.store.set_pair_hidden(1, 2, True)
        self.store.reset_pair(1, 2)
        self.assertFalse(self.store.pair_hidden(1, 2))

    def test_reset_global_data_clears_hidden_settings(self):
        self.store.set_pair_hidden(1, 2, True)
        self.store.set_pair_hidden(3, 4, True)
        self.store.reset_all()
        self.assertFalse(self.store.pair_hidden(1, 2))
        self.assertFalse(self.store.pair_hidden(3, 4))

    def test_leaderboard_indexes_are_created(self):
        indexes = {row["name"] for row in self.db.execute("PRAGMA index_list(pairs)")}
        self.assertIn("idx_pairs_current_connections", indexes)
        interaction_indexes = {row["name"] for row in self.db.execute("PRAGMA index_list(interactions)")}
        self.assertIn("idx_interactions_day", interaction_indexes)

    def test_new_user_gets_initial_balance_and_profile_exposes_it(self):
        self.assertEqual(self.store.get_balance(42), 10_000)
        self.assertEqual(self.store.profile_stats(42)["balance"], 10_000)

    def test_balance_change_has_no_negative_balance_and_records_ledger(self):
        self.assertEqual(self.store.change_balance(42, -250, "test_bet", date(2026, 9, 11)), 9_750)
        self.assertIsNone(self.store.change_balance(42, -10_000, "too_much", date(2026, 9, 11)))
        self.assertEqual(self.store.get_balance(42), 9_750)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM currency_ledger").fetchone()[0], 1)

    def test_seven_day_streak_rewards_both_members_once(self):
        for day in range(1, 8):
            self.store.record_connection(1, 2, date(2026, 9, day), 1, 2, day)
        self.assertEqual(self.store.get_balance(1), 10_100)
        self.assertEqual(self.store.get_balance(2), 10_100)
        self.store.record_connection(1, 2, date(2026, 9, 7), 1, 2, 999)
        self.assertEqual(self.store.get_balance(1), 10_100)
        self.assertEqual(self.store.get_balance(2), 10_100)

    def test_fourteen_day_streak_adds_second_milestone_reward(self):
        for day in range(1, 15):
            self.store.record_connection(1, 2, date(2026, 9, day), 1, 2, day)
        self.assertEqual(self.store.get_balance(1), 10_200)
        self.assertEqual(self.store.get_balance(2), 10_200)

    def test_profile_stats_include_connections_and_unique_partners(self):
        self.store.record_connection(1, 2, date(2026, 9, 10), 10, 20, 30)
        self.store.record_connection(1, 2, date(2026, 9, 11), 10, 20, 31)
        self.store.record_connection(1, 3, date(2026, 9, 11), 10, 20, 32)
        stats = self.store.profile_stats(1)
        self.assertEqual(stats["current_connections"], 3)
        self.assertEqual(stats["lifetime_connections"], 3)
        self.assertEqual(stats["unique_partners"], 2)
        self.assertEqual(stats["best_streak"], 2)

    def test_server_leaderboard_counts_only_that_server(self):
        self.store.record_connection(1, 2, date(2026, 9, 11), 10, 20, 30)
        self.store.record_connection(1, 2, date(2026, 9, 12), 11, 21, 31)
        rows = self.store.server_leaderboard(10)
        self.assertEqual(rows[0]["connections"], 1)
        self.assertEqual((rows[0]["user_a"], rows[0]["user_b"]), (1, 2))

    def test_global_leaderboard_aggregates_connections_across_servers(self):
        self.store.record_connection(1, 2, date(2026, 9, 11), 10, 20, 30)
        self.store.record_connection(1, 2, date(2026, 9, 12), 11, 21, 31)
        rows = self.store.global_leaderboard()
        self.assertEqual(rows[0]["connections"], 2)
        self.assertEqual((rows[0]["user_a"], rows[0]["user_b"]), (1, 2))

    def test_hidden_pair_is_excluded_from_server_leaderboard(self):
        self.store.record_connection(1, 2, date(2026, 9, 11), 10, 20, 30)
        self.store.set_pair_hidden(1, 2, True)
        self.assertEqual(self.store.server_leaderboard(10), [])

    def test_server_leaderboard_excludes_opted_out_user(self):
        self.store.record_connection(1, 2, date(2026, 9, 11), 10, 20, 30)
        self.store.set_privacy(1, leaderboard_opt_out=True)
        self.assertEqual(self.store.server_leaderboard(10), [])

        self.store.set_privacy(1, opt_out=True, leaderboard_opt_out=True)
        self.assertTrue(self.store.is_opted_out(1))
        self.assertTrue(self.store.leaderboard_opted_out(1))
        self.assertFalse(self.store.record_connection(1, 2, date(2026, 9, 11), 10, 20, 30))


if __name__ == "__main__":
    unittest.main()
