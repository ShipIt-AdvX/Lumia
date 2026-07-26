"""轮询本机大脑 /api/pet/state，驱动地瓜派桌宠导演行为。

网络请求在后台线程执行，避免大脑掉线时 4s 超时阻塞 GUI 主线程
（轮询 3s < 超时 4s 会让主循环被连续卡死）。结果经 Qt 信号送回主线程。
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger("lumia.brain")


class BrainClient(QObject):
    _state_ready = pyqtSignal(dict)  # 后台线程 -> 主线程（队列连接）

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
        self._busy = False
        self._state_ready.connect(self._deliver)

    def start(self) -> None:
        self._timer.start(self._interval_ms)
        QTimer.singleShot(400, self._poll)

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        if self._busy:
            return  # 上一次请求尚未返回（大脑掉线超时中），跳过本轮
        self._busy = True
        threading.Thread(target=self._fetch, daemon=True, name="brain-poll").start()

    def _fetch(self) -> None:
        """后台线程：只做网络与解析，不碰任何 Qt 对象。"""
        url = f"{self.base_url}/api/pet/state"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if self._failing:
                log.info("大脑已恢复: %s", self.base_url)
                self._failing = False
            self._state_ready.emit(data if isinstance(data, dict) else {})
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            if not self._failing:
                log.warning("无法连接大脑 %s: %s", url, exc)
                self._failing = True
        finally:
            self._busy = False

    def _deliver(self, data: dict) -> None:
        self._on_state(data)
