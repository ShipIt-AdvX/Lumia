"""桌宠导演：根据睡觉/吃饭/喝水/久坐算出猫该干什么（供地瓜派与电脑侧消费）。"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from typing import Any


def _parse_hhmm(s: str) -> dtime | None:
    try:
        h, m = (int(x) for x in str(s).strip().split(":")[:2])
        return dtime(h, m)
    except (ValueError, TypeError):
        return None


def _in_window(now: datetime, start: dtime, end: dtime) -> bool:
    cur = now.time()
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


def _near_clock(now: datetime, hhmm: str, window_min: int = 45) -> bool:
    """到点后 window_min 分钟内仍算「该吃饭/该睡」氛围。"""
    t = _parse_hhmm(hhmm)
    if not t:
        return False
    target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if target > now:
        target -= timedelta(days=1)
    return timedelta(0) <= (now - target) < timedelta(minutes=window_min)


_DEBUG_ACTIONS = ("auto", "idle", "sleep", "meal", "sit_away")

_DEBUG_BUBBLES = {
    "idle": "（调试）闲逛中",
    "sleep": "（调试）该睡觉了…嘘，别吵我。",
    "meal": "（调试）该吃饭啦，别光敲键盘！",
    "sit_away": "（调试）坐太久啦，我先走了——起来走走吧！",
}


class PetDirector:
    """无状态计算：依赖 Reminders 快照 + 配置；支持调试强制行为。"""

    def __init__(self, config: Any, reminders: Any) -> None:
        self._cfg = config
        self._reminders = reminders
        self._away_since: datetime | None = None  # 久坐后猫已「走远」的起点
        self._stood_since: datetime | None = None  # 起身计时，够久才回家
        self._debug_action: str | None = None  # None=auto
        self._debug_until: datetime | None = None
        self._debug_bubble: str | None = None
        self._debug_scale: float | None = None

    def set_debug(
        self,
        action: str = "auto",
        *,
        minutes: float = 10,
        bubble: str | None = None,
        scale: float | None = None,
    ) -> dict[str, Any]:
        """调试：强制猫行为。action=auto 清除强制。"""
        action = (action or "auto").strip().lower()
        if action not in _DEBUG_ACTIONS:
            return {"ok": False, "error": f"unknown action: {action}", "allowed": list(_DEBUG_ACTIONS)}
        if action == "auto":
            self._debug_action = None
            self._debug_until = None
            self._debug_bubble = None
            self._debug_scale = None
            return {"ok": True, "debug": self.debug_info(), "state": self.snapshot()}
        mins = max(0.1, float(minutes))
        self._debug_action = action
        self._debug_until = datetime.now() + timedelta(minutes=mins)
        self._debug_bubble = bubble
        self._debug_scale = scale
        return {"ok": True, "debug": self.debug_info(), "state": self.snapshot()}

    def clear_debug(self) -> dict[str, Any]:
        return self.set_debug("auto")

    def debug_info(self) -> dict[str, Any]:
        self._expire_debug()
        return {
            "forced": self._debug_action is not None,
            "action": self._debug_action or "auto",
            "until": self._debug_until.isoformat(timespec="seconds") if self._debug_until else None,
            "bubble": self._debug_bubble,
            "scale": self._debug_scale,
            "allowed": list(_DEBUG_ACTIONS),
        }

    def _expire_debug(self) -> None:
        if self._debug_until and datetime.now() >= self._debug_until:
            self._debug_action = None
            self._debug_until = None
            self._debug_bubble = None
            self._debug_scale = None

    def snapshot(self) -> dict[str, Any]:
        now = datetime.now()
        self._expire_debug()
        sit = self._reminders.sit_snapshot()
        seated = bool(sit.get("seated"))
        seated_sec = int(sit.get("seated_seconds") or 0)
        seated_min = seated_sec // 60
        # 注意：阈值允许为 0，不能用 `or 45`
        raw_th = sit.get("sedentary_minutes")
        threshold = int(raw_th) if raw_th is not None else 45
        threshold_sec = max(0, threshold) * 60
        raw_ret = self._cfg.get("pet", "return_after_stand_sec", default=90)
        return_after = int(raw_ret) if raw_ret is not None else 90

        sleep_start = _parse_hhmm(
            str(self._cfg.get("reminders", "sleep_time", default="01:30") or "01:30")
        ) or dtime(1, 30)
        sleep_end = _parse_hhmm(
            str(self._cfg.get("reminders", "sleep_end", default="07:00") or "07:00")
        ) or dtime(7, 0)
        in_sleep = _in_window(now, sleep_start, sleep_end)

        meals = self._cfg.get("reminders", "meals", default=[]) or []
        meal_now = any(_near_clock(now, m, 45) for m in meals)

        # 久坐离家 / 回家（用秒比较，避免阈值=0 被 or 吃掉）
        if seated and seated_sec >= threshold_sec:
            if self._away_since is None:
                self._away_since = now
            self._stood_since = None
        elif not seated:
            if self._away_since is not None:
                if self._stood_since is None:
                    self._stood_since = now
                elif (now - self._stood_since).total_seconds() >= return_after:
                    self._away_since = None
                    self._stood_since = None
            else:
                self._stood_since = None
        else:
            # 还坐着但未到阈值
            if self._away_since is None:
                self._stood_since = None

        away = self._away_since is not None
        stand_sec = (
            int((now - self._stood_since).total_seconds()) if self._stood_since else 0
        )

        # 优先级：久坐走远 > 睡觉 > 吃饭 > 闲逛
        if away:
            action = "sit_away"
            bubble = "坐太久啦，我先走了——起来走走吧！"
            scale = max(0.35, 1.0 - min(0.65, (seated_min - threshold) * 0.05))
        elif in_sleep:
            action = "sleep"
            bubble = "该睡觉了…嘘，别吵我。"
            scale = 1.0
        elif meal_now:
            action = "meal"
            bubble = "该吃饭啦，别光敲键盘！"
            scale = 1.0
        else:
            action = "idle"
            bubble = ""
            scale = 1.0

        natural = action
        debug = self.debug_info()
        if self._debug_action:
            action = self._debug_action
            bubble = self._debug_bubble or _DEBUG_BUBBLES.get(action, bubble)
            if self._debug_scale is not None:
                scale = float(self._debug_scale)
            elif action == "sit_away":
                scale = 0.45
            else:
                scale = 1.0

        return {
            "action": action,
            "bubble": bubble,
            "scale": round(scale, 2),
            "angry_on_click": action == "sleep",
            "steal_cursor": action == "sit_away",
            "in_sleep_window": in_sleep,
            "meal_window": meal_now,
            "sit": sit,
            "away": away,
            "stand_seconds": stand_sec,
            "return_after_stand_sec": return_after,
            "natural_action": natural,
            "debug": debug,
            "ts": now.isoformat(timespec="seconds"),
        }
