"""SQLite history of daily rates + sent-alert dedupe."""
from __future__ import annotations

import sqlite3

import pandas as pd

from .models import Snapshot

SCHEMA = """CREATE TABLE IF NOT EXISTS rates(date TEXT, base TEXT, quote TEXT, rate REAL, source TEXT, fetched_at TEXT,
  PRIMARY KEY(date, base, quote));
CREATE TABLE IF NOT EXISTS alerts_sent(key TEXT PRIMARY KEY, ts TEXT, message TEXT);"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def upsert(self, s: Snapshot) -> int:
        rows = [(str(d), s.base, q, r, s.source, s.fetched_at.isoformat(timespec="seconds"))
                for d, row in s.rates.items() for q, r in row.items()]
        self.db.executemany("INSERT OR REPLACE INTO rates VALUES(?,?,?,?,?,?)", rows)
        self.db.commit()
        return len(rows)

    def wide(self, base: str = "EUR") -> pd.DataFrame:
        """Index = date, columns = currencies (1 base = x ccy), base column = 1.0."""
        df = pd.read_sql_query("SELECT date, quote, rate FROM rates WHERE base=?", self.db, params=(base,))
        w = df.pivot(index="date", columns="quote", values="rate").sort_index()
        w.index = pd.to_datetime(w.index)
        w[base] = 1.0
        return w

    def already_sent(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM alerts_sent WHERE key=?", (key,)).fetchone() is not None

    def mark_sent(self, key: str, ts: str, message: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO alerts_sent VALUES(?,?,?)", (key, ts, message))
        self.db.commit()
