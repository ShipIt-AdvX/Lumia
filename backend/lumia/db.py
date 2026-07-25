from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "lumia.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS coding_daily (
    date            TEXT PRIMARY KEY,
    used_seconds    INTEGER NOT NULL DEFAULT 0,
    delay_used      INTEGER NOT NULL DEFAULT 0,
    delay_ends_at   TEXT,
    locked          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS delay_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    ts          TEXT NOT NULL,
    minutes     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ideas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT,
    audio_path  TEXT,
    source      TEXT,
    uploaded    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    seated      INTEGER NOT NULL,
    pressure    REAL
);

CREATE TABLE IF NOT EXISTS reminders_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    message     TEXT
);
"""


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def get_day(self, date: str) -> dict[str, Any]:
        row = self.query_one("SELECT * FROM coding_daily WHERE date = ?", (date,))
        if row is None:
            self.execute("INSERT INTO coding_daily (date) VALUES (?)", (date,))
            row = self.query_one("SELECT * FROM coding_daily WHERE date = ?", (date,))
        return dict(row)

    def set_day(self, date: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(
            f"UPDATE coding_daily SET {cols} WHERE date = ?",
            (*fields.values(), date),
        )

    def add_used_seconds(self, date: str, delta: int) -> None:
        self.get_day(date)
        self.execute(
            "UPDATE coding_daily SET used_seconds = used_seconds + ? WHERE date = ?",
            (delta, date),
        )

    def log_delay(self, date: str, ts: str, minutes: int) -> None:
        self.execute(
            "INSERT INTO delay_log (date, ts, minutes) VALUES (?, ?, ?)",
            (date, ts, minutes),
        )

    def clear_delay_log(self, date: str) -> None:
        self.execute("DELETE FROM delay_log WHERE date = ?", (date,))

    def clear_coding_history(self) -> None:
        self.execute("DELETE FROM delay_log")
        self.execute("DELETE FROM coding_daily")

    def last_delay_date(self) -> str | None:
        row = self.query_one("SELECT MAX(date) AS d FROM delay_log")
        return row["d"] if row and row["d"] else None

    def delay_dates_between(self, start: str, end: str) -> list[str]:
        rows = self.query(
            "SELECT DISTINCT date FROM delay_log WHERE date >= ? AND date <= ?",
            (start, end),
        )
        return [r["date"] for r in rows]
