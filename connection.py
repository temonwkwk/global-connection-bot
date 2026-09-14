from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, timedelta


log = logging.getLogger("connection.store")

INITIAL_BALANCE = 10_000
STREAK_REWARD_MILESTONE = 7


def canonical_pair(user_a: int, user_b: int) -> tuple[int, int]:
    if user_a == user_b:
        raise ValueError("A connection requires two different members")
    return tuple(sorted((int(user_a), int(user_b))))


class ConnectionStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS pairs (
            user_a INTEGER NOT NULL, user_b INTEGER NOT NULL,
            current_connections INTEGER NOT NULL DEFAULT 0,
            lifetime_connections INTEGER NOT NULL DEFAULT 0,
            streak INTEGER NOT NULL DEFAULT 0,
            last_connection_date TEXT,
            last_decay_date TEXT,
            PRIMARY KEY (user_a, user_b)
        );
        CREATE TABLE IF NOT EXISTS daily_connections (
            user_a INTEGER NOT NULL, user_b INTEGER NOT NULL, connection_date TEXT NOT NULL,
            server_id INTEGER, channel_id INTEGER,
            PRIMARY KEY (user_a, user_b, connection_date)
        );
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_a INTEGER NOT NULL, user_b INTEGER NOT NULL,
            event_type TEXT NOT NULL, amount INTEGER NOT NULL, before_value INTEGER,
            after_value INTEGER, event_date TEXT NOT NULL, metadata TEXT
        );
        CREATE TABLE IF NOT EXISTS interactions (
            author_id INTEGER NOT NULL, target_id INTEGER NOT NULL, day TEXT NOT NULL,
            guild_id INTEGER, channel_id INTEGER, message_id INTEGER,
            PRIMARY KEY (author_id, target_id, day)
        );
        CREATE TABLE IF NOT EXISTS hidden_pairs (
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            PRIMARY KEY (user_a, user_b)
        );
        CREATE TABLE IF NOT EXISTS user_privacy (
            user_id INTEGER PRIMARY KEY,
            opt_out INTEGER NOT NULL DEFAULT 0,
            leaderboard_opt_out INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 10000,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS currency_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            before_balance INTEGER NOT NULL,
            after_balance INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            metadata TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_wallets_balance
            ON wallets(balance DESC);
        CREATE INDEX IF NOT EXISTS idx_currency_ledger_user
            ON currency_ledger(user_id, id);
        CREATE INDEX IF NOT EXISTS idx_pairs_current_connections
            ON pairs(current_connections DESC);
        CREATE INDEX IF NOT EXISTS idx_interactions_day
            ON interactions(day);
        """)
        self.db.commit()

    def get_pair(self, user_a, user_b):
        a, b = canonical_pair(user_a, user_b)
        row = self.db.execute("SELECT * FROM pairs WHERE user_a=? AND user_b=?", (a, b)).fetchone()
        return dict(row) if row else {"user_a": a, "user_b": b, "current_connections": 0,
            "lifetime_connections": 0, "streak": 0, "last_connection_date": None, "last_decay_date": None}

    def record_connection(self, user_a, user_b, connection_date: date, server_id=None, channel_id=None, metadata=None):
        a, b = canonical_pair(user_a, user_b)
        if self.is_opted_out(a) or self.is_opted_out(b):
            return False
        day = connection_date.isoformat()
        if self.db.execute("SELECT 1 FROM daily_connections WHERE user_a=? AND user_b=? AND connection_date=?", (a,b,day)).fetchone():
            return False
        pair = self.get_pair(a, b)
        previous = date.fromisoformat(pair["last_connection_date"]) if pair["last_connection_date"] else None
        streak = pair["streak"] + 1 if previous == connection_date - timedelta(days=1) else 1
        current = pair["current_connections"] + 1
        lifetime = pair["lifetime_connections"] + 1
        self.db.execute("INSERT INTO pairs VALUES (?,?,?,?,?,?,?)", (a,b,current,lifetime,streak,day,None)) if not pair["last_connection_date"] else self.db.execute("UPDATE pairs SET current_connections=?, lifetime_connections=?, streak=?, last_connection_date=?, last_decay_date=NULL WHERE user_a=? AND user_b=?", (current,lifetime,streak,day,a,b))
        self.db.execute("INSERT INTO daily_connections VALUES (?,?,?,?,?)", (a,b,day,server_id,channel_id))
        self.db.execute("INSERT INTO ledger(user_a,user_b,event_type,amount,before_value,after_value,event_date,metadata) VALUES(?,?,?,?,?,?,?,?)", (a,b,"daily_connection",1,pair["current_connections"],current,day,json.dumps(metadata or {})))
        if streak >= STREAK_REWARD_MILESTONE and streak % STREAK_REWARD_MILESTONE == 0:
            already_paid = self.db.execute(
                """SELECT 1 FROM currency_ledger
                   WHERE event_type='streak_reward'
                     AND metadata LIKE ? AND metadata LIKE ?""",
                (f'%"pair": "{a}:{b}"%', f'%"streak": {streak}%'),
            ).fetchone()
            if already_paid is None:
                reward = 100
                for user_id in (a, b):
                    before = self.get_balance(user_id)
                    after = before + reward
                    self.db.execute("UPDATE wallets SET balance=? WHERE user_id=?", (after, user_id))
                    self.db.execute(
                        """INSERT INTO currency_ledger
                           (user_id,event_type,amount,before_balance,after_balance,event_date,metadata)
                           VALUES(?,?,?,?,?,?,?)""",
                        (user_id, "streak_reward", reward, before, after, day,
                         json.dumps({"pair": f"{a}:{b}", "streak": streak})),
                    )
                log.info("currency_streak_reward pair=%s streak=%s amount_each=%s", f"{a}:{b}", streak, reward)
        self.db.commit()
        log.info("connection_ledger_event event_type=daily_connection user_a=%s user_b=%s server_id=%s date=%s before=%s after=%s", a, b, server_id, day, pair["current_connections"], current)
        return True

    def apply_decay(self, today: date):
        for pair in self.db.execute("SELECT * FROM pairs WHERE last_connection_date IS NOT NULL").fetchall():
            last = date.fromisoformat(pair["last_connection_date"])
            missed = (today - last).days
            if missed < 3 or pair["last_decay_date"] is not None:
                continue
            before = pair["current_connections"]
            after = before // 2
            self.db.execute("UPDATE pairs SET current_connections=?, streak=0, last_decay_date=? WHERE user_a=? AND user_b=?", (after,today.isoformat(),pair["user_a"],pair["user_b"]))
            self.db.execute("INSERT INTO ledger(user_a,user_b,event_type,amount,before_value,after_value,event_date,metadata) VALUES(?,?,?,?,?,?,?,?)", (pair["user_a"],pair["user_b"],"decay",after-before,before,after,today.isoformat(),json.dumps({"missed_days":missed})))
            log.info("connection_ledger_event event_type=decay user_a=%s user_b=%s date=%s before=%s after=%s missed_days=%s", pair["user_a"], pair["user_b"], today, before, after, missed)
        self.db.commit()

    def reset_pair(self, user_a, user_b):
        a, b = canonical_pair(user_a, user_b)
        self.db.execute("DELETE FROM interactions WHERE (author_id=? AND target_id=?) OR (author_id=? AND target_id=?)", (a,b,b,a))
        self.db.execute("DELETE FROM daily_connections WHERE user_a=? AND user_b=?", (a,b))
        self.db.execute("DELETE FROM ledger WHERE user_a=? AND user_b=?", (a,b))
        self.db.execute("DELETE FROM hidden_pairs WHERE user_a=? AND user_b=?", (a,b))
        self.db.execute("DELETE FROM pairs WHERE user_a=? AND user_b=?", (a,b))
        self.db.commit()

    def reset_all(self):
        self.db.execute("DELETE FROM interactions")
        self.db.execute("DELETE FROM daily_connections")
        self.db.execute("DELETE FROM ledger")
        self.db.execute("DELETE FROM hidden_pairs")
        self.db.execute("DELETE FROM user_privacy")
        self.db.execute("DELETE FROM pairs")
        self.db.commit()

    def decay_history(self, user_a, user_b):
        a, b = canonical_pair(user_a, user_b)
        return [dict(row) for row in self.db.execute("SELECT * FROM ledger WHERE user_a=? AND user_b=? AND event_type='decay' ORDER BY id", (a, b))]

    def connections_for(self, user_id):
        rows=self.db.execute("SELECT * FROM pairs WHERE user_a=? OR user_b=? ORDER BY current_connections DESC",(user_id,user_id)).fetchall()
        return [dict(row) for row in rows]

    def get_balance(self, user_id: int) -> int:
        self.db.execute(
            "INSERT OR IGNORE INTO wallets(user_id, balance) VALUES (?, ?)",
            (int(user_id), INITIAL_BALANCE),
        )
        self.db.commit()
        return self.db.execute(
            "SELECT balance FROM wallets WHERE user_id=?", (int(user_id),)
        ).fetchone()[0]

    def change_balance(self, user_id: int, amount: int, event_type: str,
                       event_date: date, metadata=None) -> int | None:
        user_id = int(user_id)
        amount = int(amount)
        current = self.get_balance(user_id)
        after = current + amount
        if after < 0:
            return None
        self.db.execute("UPDATE wallets SET balance=? WHERE user_id=?", (after, user_id))
        self.db.execute(
            """INSERT INTO currency_ledger
               (user_id,event_type,amount,before_balance,after_balance,event_date,metadata)
               VALUES(?,?,?,?,?,?,?)""",
            (user_id, event_type, amount, current, after, event_date.isoformat(),
             json.dumps(metadata or {})),
        )
        self.db.commit()
        return after

    def profile_stats(self, user_id: int) -> dict:
        rows = self.connections_for(user_id)
        partners = {
            row["user_b"] if row["user_a"] == user_id else row["user_a"]
            for row in rows
        }
        return {
            "current_connections": sum(row["current_connections"] for row in rows),
            "lifetime_connections": sum(row["lifetime_connections"] for row in rows),
            "unique_partners": len(partners),
            "best_streak": max((row["streak"] for row in rows), default=0),
            "balance": self.get_balance(user_id),
        }

    def server_leaderboard(self, server_id: int, limit: int = 10):
        rows = self.db.execute("""
            SELECT user_a, user_b, COUNT(*) AS connections
            FROM daily_connections
            WHERE server_id=?
              AND NOT EXISTS (
                  SELECT 1 FROM hidden_pairs h
                  WHERE h.user_a = daily_connections.user_a
                    AND h.user_b = daily_connections.user_b
              )
              AND NOT EXISTS (
                  SELECT 1 FROM user_privacy u
                  WHERE u.user_id IN (user_a, user_b)
                    AND u.leaderboard_opt_out=1
              )
            GROUP BY user_a, user_b
            ORDER BY connections DESC, user_a, user_b
            LIMIT ?
        """, (server_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def global_leaderboard(self, limit: int = 10):
        rows = self.db.execute("""
            SELECT user_a, user_b, COUNT(*) AS connections
            FROM daily_connections
            WHERE NOT EXISTS (
                SELECT 1 FROM hidden_pairs h
                WHERE h.user_a = daily_connections.user_a
                  AND h.user_b = daily_connections.user_b
            )
              AND NOT EXISTS (
                SELECT 1 FROM user_privacy u
                WHERE u.user_id IN (user_a, user_b)
                  AND u.leaderboard_opt_out=1
            )
            GROUP BY user_a, user_b
            ORDER BY connections DESC, user_a, user_b
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

    def set_privacy(self, user_id: int, *, opt_out: bool | None = None,
                    leaderboard_opt_out: bool | None = None) -> None:
        current = self.db.execute(
            "SELECT opt_out, leaderboard_opt_out FROM user_privacy WHERE user_id=?",
            (user_id,),
        ).fetchone()
        values = {
            "opt_out": bool(current[0]) if current else False,
            "leaderboard_opt_out": bool(current[1]) if current else False,
        }
        if opt_out is not None:
            values["opt_out"] = opt_out
        if leaderboard_opt_out is not None:
            values["leaderboard_opt_out"] = leaderboard_opt_out
        if not values["opt_out"] and not values["leaderboard_opt_out"]:
            self.db.execute("DELETE FROM user_privacy WHERE user_id=?", (user_id,))
        else:
            self.db.execute("""
                INSERT INTO user_privacy(user_id, opt_out, leaderboard_opt_out)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    opt_out=excluded.opt_out,
                    leaderboard_opt_out=excluded.leaderboard_opt_out
            """, (user_id, int(values["opt_out"]), int(values["leaderboard_opt_out"])))
        self.db.commit()

    def is_opted_out(self, user_id: int) -> bool:
        row = self.db.execute("SELECT opt_out FROM user_privacy WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row[0])

    def leaderboard_opted_out(self, user_id: int) -> bool:
        row = self.db.execute("SELECT leaderboard_opt_out FROM user_privacy WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row[0])

    def set_pair_hidden(self, user_a: int, user_b: int, hidden: bool) -> None:
        a, b = canonical_pair(user_a, user_b)
        if hidden:
            self.db.execute("INSERT OR IGNORE INTO hidden_pairs(user_a, user_b) VALUES(?, ?)", (a, b))
        else:
            self.db.execute("DELETE FROM hidden_pairs WHERE user_a=? AND user_b=?", (a, b))
        self.db.commit()

    def pair_hidden(self, user_a: int, user_b: int) -> bool:
        a, b = canonical_pair(user_a, user_b)
        return self.db.execute(
            "SELECT 1 FROM hidden_pairs WHERE user_a=? AND user_b=?", (a, b)
        ).fetchone() is not None
