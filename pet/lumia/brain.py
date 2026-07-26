"""轮询本机大脑 /api/pet/state，驱动地瓜派桌宠导演行为。"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer

log = logging.getLogger("lumia.brain")


class BrainClient(QObject):
    def __init__(
        self,
        base_url: str,
        on_state: Callable[[dict[str, Any]], None],
        *,
        interval_ms: int = 3000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.base_url = base_url.rstrip("/")
        self._on_state = on_state
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._interval_ms = interval_ms
        self._failing = False

    def start(self) -> None:
        self._timer.start(self._interval_ms)
        QTimer.singleShot(400, self._poll)

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        url = f"{self.base_url}/api/pet/state"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if self._failing:
                log.info("大脑已恢复: %s", self.base_url)
                self._failing = False
            self._on_state(data if isinstance(data, dict) else {})
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            if not self._failing:
                log.warning("无法连接大脑 %s: %s", url, exc)
                self._failing = True
