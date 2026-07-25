from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "lumia.json"

DAY_DEFAULTS: dict[str, Any] = {
    "used_seconds": 0,
    "delay_used": 0,
    "delay_ends_at": None,
    "locked": 0,
    "save_grace_used": 0,
}

TABLES = ("delay_log", "ideas", "sit_events", "reminders_log")


class Database:
    def __init__(self, path: Path = DATA_PATH) -> None:
        self._lock = threading.RLock()
        self._path = path
        self._data: dict[str, Any] = {
            "coding_daily": {},
            "seq": {},
            **{t: [] for t in TABLES},
        }
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                for key in self._data:
                    if key in loaded:
                        self._data[key] = loaded[key]
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self._path)

    def _next_id(self, table: str) -> int:
        seq = self._data.setdefault("seq", {})
        seq[table] = int(seq.get(table, 0)) + 1
        return seq[table]

    def append(self, table: str, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            row = {"id": self._next_id(table), **record}
            self._data[table].append(row)
            self._save()
            return row

    def list_rows(self, table: str, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(r) for r in reversed(self._data[table])]
            return rows[:limit] if limit else rows

    def get_day(self, date: str) -> dict[str, Any]:
        with self._lock:
            day = self._data["coding_daily"].get(date)
            if day is None:
                day = {"date": date, **DAY_DEFAULTS}
                self._data["coding_daily"][date] = day
                self._save()
            return dict(day)

    def set_day(self, date: str, **fields: Any) -> None:
        if not fields:
            return
        with self._lock:
            self.get_day(date)
            self._data["coding_daily"][date].update(fields)
            self._save()

    def add_used_seconds(self, date: str, delta: int) -> None:
        with self._lock:
            self.get_day(date)
            self._data["coding_daily"][date]["used_seconds"] += delta
            self._save()

    def log_delay(self, date: str, ts: str, minutes: int) -> None:
        self.append("delay_log", {"date": date, "ts": ts, "minutes": minutes})

    def clear_delay_log(self, date: str) -> None:
        with self._lock:
            self._data["delay_log"] = [
                r for r in self._data["delay_log"] if r["date"] != date
            ]
            self._save()

    def clear_coding_history(self) -> None:
        with self._lock:
            self._data["delay_log"] = []
            self._data["coding_daily"] = {}
            self._save()

    def last_delay_date(self) -> str | None:
        with self._lock:
            dates = [r["date"] for r in self._data["delay_log"]]
            return max(dates) if dates else None

    def delay_dates_between(self, start: str, end: str) -> list[str]:
        with self._lock:
            return sorted(
                {r["date"] for r in self._data["delay_log"] if start <= r["date"] <= end}
            )
