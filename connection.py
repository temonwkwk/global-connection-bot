from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta


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
        """)
        self.db.commit()

    def get_pair(self, user_a, user_b):
        a, b = canonical_pair(user_a, user_b)
        row = self.db.execute("SELECT * FROM pairs WHERE user_a=? AND user_b=?", (a, b)).fetchone()
        return dict(row) if row else {"user_a": a, "user_b": b, "current_connections": 0,
            "lifetime_connections": 0, "streak": 0, "last_connection_date": None, "last_decay_date": None}

    def record_connection(self, user_a, user_b, connection_date: date, server_id=None, channel_id=None, metadata=None):
        a, b = canonical_pair(user_a, user_b)
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
        self.db.commit()
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
        self.db.commit()

    def reset_pair(self, user_a, user_b):
        a, b = canonical_pair(user_a, user_b)
        self.db.execute("DELETE FROM interactions WHERE (author_id=? AND target_id=?) OR (author_id=? AND target_id=?)", (a,b,b,a))
        self.db.execute("DELETE FROM daily_connections WHERE user_a=? AND user_b=?", (a,b))
        self.db.execute("DELETE FROM ledger WHERE user_a=? AND user_b=?", (a,b))
        self.db.execute("DELETE FROM pairs WHERE user_a=? AND user_b=?", (a,b))
        self.db.commit()

    def decay_history(self, user_a, user_b):
        a, b = canonical_pair(user_a, user_b)
        return [dict(row) for row in self.db.execute("SELECT * FROM ledger WHERE user_a=? AND user_b=? AND event_type='decay' ORDER BY id", (a, b))]

    def connections_for(self, user_id):
        rows=self.db.execute("SELECT * FROM pairs WHERE user_a=? OR user_b=? ORDER BY current_connections DESC",(user_id,user_id)).fetchall()
        return [dict(row) for row in rows]
