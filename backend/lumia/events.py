"""In-memory event bus.

Background loops (coding limit, reminders) push events here; the Electron
front-end polls ``/api/events/poll?after=<id>`` to render non-focus popups.
Events are monotonically numbered so the client can resume without gaps.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any


class EventBus:
    MAX_EVENTS = 300

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._next_id = 1

    def emit(
        self,
        type: str,
        title: str,
        message: str,
        *,
        actions: list[dict[str, str]] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            event = {
                "id": self._next_id,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "type": type,
                "title": title,
                "message": message,
                "actions": actions or [],
                "data": data or {},
            }
            self._next_id += 1
            self._events.append(event)
            if len(self._events) > self.MAX_EVENTS:
                self._events = self._events[-self.MAX_EVENTS :]
            return event

    def poll(self, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if e["id"] > after]

    def latest_id(self) -> int:
        with self._lock:
            return self._next_id - 1
