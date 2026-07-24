"""Coding-time limit engine.

Rules (from the product spec):

1. Daily quota = ``n + k*m`` hours, where ``n`` is a fixed daily allowance,
   ``k`` a conversion coefficient and ``m`` sleep hours.
2. When the quota is exhausted the developer may invoke a one-time **delay**
   (60-90 min). Only one delay is allowed within any 2-day window.
3. Using a delay grants a final ``delay_minutes`` block; once it ends the day is
   hard-locked (no more development today).
4. Each delay used earlier in the same ISO week shrinks ``k`` by a factor of
   0.8 (``k_eff = k * 0.8**delays_this_week``); the penalty resets every Monday.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .config import Config
from .db import Database
from .events import EventBus


def _today() -> str:
    return date.today().isoformat()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


class CodingTracker:
    def __init__(self, config: Config, db: Database, bus: EventBus) -> None:
        self._cfg = config
        self._db = db
        self._bus = bus
        self._state: str = "idle"
        self._active_date = _today()

    # -- quota maths -----------------------------------------------------
    def _penalty_exponent(self, today: date) -> int:
        """Number of distinct delay days earlier this ISO week (before today)."""
        start = _week_start(today)
        yesterday = today - timedelta(days=1)
        if yesterday < start:
            return 0
        return len(
            self._db.delay_dates_between(start.isoformat(), yesterday.isoformat())
        )

    def _k_effective(self, today: date) -> float:
        base_k = float(self._cfg.get("coding", "conversion_k", default=0.5))
        return base_k * (0.8 ** self._penalty_exponent(today))

    def allowed_seconds(self, today: date | None = None) -> int:
        today = today or date.today()
        n = float(self._cfg.get("coding", "daily_fixed_hours", default=4.0))
        m = float(self._cfg.get("coding", "sleep_hours", default=7.0))
        k_eff = self._k_effective(today)
        return int(round((n + k_eff * m) * 3600))

    def _delay_minutes(self) -> int:
        return int(self._cfg.get("coding", "delay_minutes", default=60))

    # -- delay availability ---------------------------------------------
    def delay_available(self, day: dict[str, Any]) -> bool:
        if day["delay_used"]:
            return False
        last = self._db.last_delay_date()
        if last is None:
            return True
        gap = (date.today() - date.fromisoformat(last)).days
        return gap >= 2  # only one delay within any 2-day window

    # -- per-tick update -------------------------------------------------
    def tick(self, coding_now: bool, dt: int = 1) -> None:
        today = _today()
        if coding_now:
            self._db.add_used_seconds(today, dt)
        self._evaluate()

    def _evaluate(self) -> None:
        snap = self.snapshot()
        new_state = snap["state"]
        if new_state == self._state:
            return
        self._state = new_state
        if new_state == "limit_reached":
            self._bus.emit(
                "coding_limit",
                "今日开发时长已用尽",
                "该收尾了。需要的话可以使用一次延时（今天最后一段）。",
                actions=[
                    {"id": "delay", "label": f"延时 {self._delay_minutes()} 分钟"},
                    {"id": "achievements", "label": "看看今天的成绩"},
                ],
            )
        elif new_state == "delay_active":
            self._bus.emit(
                "coding_delay_started",
                "延时已开启",
                f"最后 {self._delay_minutes()} 分钟，用完今天就真的下班了。",
            )
        elif new_state == "day_locked":
            self._bus.emit(
                "coding_locked",
                "今天到此为止",
                "开发时长（含延时）已结束。去看看今天做成了什么吧。",
                actions=[{"id": "achievements", "label": "查看成就墙"}],
            )

    # -- snapshot for API/state -----------------------------------------
    def snapshot(self) -> dict[str, Any]:
        today = date.today()
        day = self._db.get_day(today.isoformat())
        used = int(day["used_seconds"])
        allowed = self.allowed_seconds(today)
        delay_used = bool(day["delay_used"])
        delay_secs = self._delay_minutes() * 60
        allowed_eff = allowed + (delay_secs if delay_used else 0)
        now = datetime.now()

        state = "idle"
        ends_at = day["delay_ends_at"]
        if delay_used:
            ended = ends_at is not None and now >= datetime.fromisoformat(ends_at)
            if ended or used >= allowed_eff:
                state = "day_locked"
            else:
                state = "delay_active"
        elif used >= allowed:
            state = "limit_reached" if self.delay_available(day) else "day_locked"
        else:
            from . import activity  # local import to avoid cost when unused

            coding_now = activity.is_coding(
                self._cfg.get("coding", "dev_processes", default=[]),
                float(self._cfg.get("coding", "idle_threshold_seconds", default=60)),
            )
            state = "coding" if coding_now else "idle"

        # Persist a hard lock so a restart keeps the day closed.
        if state == "day_locked" and not day["locked"]:
            self._db.set_day(today.isoformat(), locked=1)

        return {
            "date": today.isoformat(),
            "state": state,
            "used_seconds": used,
            "allowed_seconds": allowed,
            "allowed_effective_seconds": allowed_eff,
            "remaining_seconds": max(0, allowed_eff - used),
            "n_hours": float(self._cfg.get("coding", "daily_fixed_hours", default=4.0)),
            "m_hours": float(self._cfg.get("coding", "sleep_hours", default=7.0)),
            "k_base": float(self._cfg.get("coding", "conversion_k", default=0.5)),
            "k_effective": round(self._k_effective(today), 4),
            "week_delay_penalty": self._penalty_exponent(today),
            "delay": {
                "available": self.delay_available(day),
                "used_today": delay_used,
                "minutes": self._delay_minutes(),
                "ends_at": ends_at,
            },
        }

    # -- actions ---------------------------------------------------------
    def request_delay(self) -> dict[str, Any]:
        today = date.today()
        day = self._db.get_day(today.isoformat())
        if day["delay_used"]:
            return {"ok": False, "reason": "今天已经用过延时了。"}
        if not self.delay_available(day):
            return {"ok": False, "reason": "两天内只能使用一次延时。"}
        minutes = self._delay_minutes()
        ends_at = (datetime.now() + timedelta(minutes=minutes)).isoformat(
            timespec="seconds"
        )
        self._db.set_day(today.isoformat(), delay_used=1, delay_ends_at=ends_at)
        self._db.log_delay(today.isoformat(), datetime.now().isoformat(), minutes)
        self._state = "delay_active"
        self._bus.emit(
            "coding_delay_started",
            "延时已开启",
            f"最后 {minutes} 分钟，用完今天就真的下班了。",
        )
        return {"ok": True, "ends_at": ends_at, "minutes": minutes}
