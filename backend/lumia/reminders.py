"""Reminder scheduler + sedentary detection.

A single low-frequency loop (called ~every 30s) drives:

* clock reminders  — sleep time, meal times (each fires once per day),
* interval reminders — drink water / move (every N minutes),
* sedentary nudge  — driven by the chair pressure sensor; when the developer
  has been continuously seated past the threshold we emit a "move" reminder and
  (optionally) ask the chair to stretch.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from . import chair
from .config import Config
from .db import Database
from .events import EventBus


class Reminders:
    def __init__(self, config: Config, db: Database, bus: EventBus) -> None:
        self._cfg = config
        self._db = db
        self._bus = bus
        # sit state
        self.seated: bool = False
        self.seated_since: datetime | None = None
        self.last_pressure: float | None = None
        self._last_sedentary_nudge: datetime | None = None
        # firing bookkeeping
        self._fired_clock: set[str] = set()  # "date|kind|HH:MM"
        self._fired_day: str = date.today().isoformat()
        self._last_water: datetime = datetime.now()
        self._last_move: datetime = datetime.now()

    # -- sensor input ----------------------------------------------------
    def update_sit(self, seated: bool, pressure: float | None) -> dict[str, Any]:
        now = datetime.now()
        self.last_pressure = pressure
        if seated and not self.seated:
            self.seated_since = now
            self._last_sedentary_nudge = None
        if not seated:
            self.seated_since = None
            self._last_sedentary_nudge = None
        self.seated = seated
        self._db.execute(
            "INSERT INTO sit_events (ts, seated, pressure) VALUES (?,?,?)",
            (now.isoformat(timespec="seconds"), 1 if seated else 0, pressure),
        )
        return self.sit_snapshot()

    def sit_snapshot(self) -> dict[str, Any]:
        secs = 0
        if self.seated and self.seated_since:
            secs = int((datetime.now() - self.seated_since).total_seconds())
        return {
            "seated": self.seated,
            "pressure": self.last_pressure,
            "seated_seconds": secs,
            "sedentary_minutes": int(self._cfg.get("sit", "sedentary_minutes", default=45)),
        }

    # -- periodic tick ---------------------------------------------------
    def tick(self) -> None:
        now = datetime.now()
        today = now.date().isoformat()
        if today != self._fired_day:  # new day -> reset clock reminders
            self._fired_clock.clear()
            self._fired_day = today

        self._check_clock(now, "sleep", [self._cfg.get("reminders", "sleep_time", default="01:30")],
                           "该睡觉了", "别熬了，关机前把今天投到墙上看看吧。")
        self._check_clock(now, "meal", self._cfg.get("reminders", "meals", default=[]) or [],
                          "该吃饭了", "离开键盘，去好好吃一顿。")
        self._check_interval(now, "water", "water_interval_min", "_last_water",
                             "喝口水", "补点水分，顺便让眼睛歇一会儿。")
        self._check_interval(now, "move", "move_interval_min", "_last_move",
                             "起来动一动", "站起来伸展一下，走两步。")
        self._check_sedentary(now)

    def _check_clock(self, now: datetime, kind: str, times: list[str],
                     title: str, message: str) -> None:
        for hhmm in times:
            try:
                hh, mm = (int(x) for x in str(hhmm).split(":"))
            except ValueError:
                continue
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            key = f"{self._fired_day}|{kind}|{hhmm}"
            # fire within a 2-minute window after the target, once per day
            if key not in self._fired_clock and target <= now < target + timedelta(minutes=2):
                self._fired_clock.add(key)
                self._emit(kind, title, message)

    def _check_interval(self, now: datetime, kind: str, cfg_key: str,
                       attr: str, title: str, message: str) -> None:
        minutes = int(self._cfg.get("reminders", cfg_key, default=0) or 0)
        if minutes <= 0:
            return
        last: datetime = getattr(self, attr)
        if now - last >= timedelta(minutes=minutes):
            setattr(self, attr, now)
            self._emit(kind, title, message)

    def _check_sedentary(self, now: datetime) -> None:
        if not self.seated or not self.seated_since:
            return
        threshold = int(self._cfg.get("sit", "sedentary_minutes", default=45))
        renudge = int(self._cfg.get("sit", "renudge_minutes", default=20))
        seated_min = (now - self.seated_since).total_seconds() / 60
        if seated_min < threshold:
            return
        if self._last_sedentary_nudge and now - self._last_sedentary_nudge < timedelta(minutes=renudge):
            return
        self._last_sedentary_nudge = now
        chair_result = chair.stretch(self._cfg, source="sit_nudge")
        self._emit(
            "sedentary",
            "久坐提醒",
            f"你已经连续坐了约 {int(seated_min)} 分钟，起来拉伸一下。",
            data={"chair": chair_result, "seated_minutes": int(seated_min)},
        )

    def _emit(self, kind: str, title: str, message: str,
             data: dict[str, Any] | None = None) -> None:
        self._db.execute(
            "INSERT INTO reminders_log (ts, kind, message) VALUES (?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), kind, message),
        )
        self._bus.emit(f"reminder_{kind}", title, message, data=data)
