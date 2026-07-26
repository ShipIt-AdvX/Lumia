"""Windows 窗口顶边探测：枚举可见顶层窗口，供桌宠作为落脚平台。

仅 win32 可用（ctypes 调 user32/dwmapi）。返回坐标均为物理像素，
调用方需按 devicePixelRatio 换算为 Qt 逻辑坐标。
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes

log = logging.getLogger("lumia.winsurface")

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

DWMWA_EXTENDED_FRAME_BOUNDS = 9  # 去除不可见边框的真实窗口矩形
DWMWA_CLOAKED = 14               # UWP 挂起/隐藏窗口（可见属性仍为真）
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080

# 桌面/任务栏等 shell 窗口不作为平台
EXCLUDED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

MIN_WIDTH = 120          # 顶边太窄的窗口不值得站（物理像素）
REFRESH_INTERVAL = 0.25  # 枚举节流 s

# 64 位下 HWND 为指针宽度，显式声明避免截断
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD
]

Rect = tuple[int, int, int, int]  # (left, top, right, bottom) 物理像素


def _frame_rect(hwnd: int) -> Rect | None:
    """DWM 扩展边界矩形（无阴影边框），失败时回退 GetWindowRect。"""
    rect = wintypes.RECT()
    ok = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
    ) == 0
    if not ok and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _is_cloaked(hwnd: int) -> bool:
    val = wintypes.DWORD(0)
    if dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)
    ) == 0:
        return bool(val.value)
    return False


def window_rect(hwnd: int) -> Rect | None:
    """窗口当前矩形；已关闭/不可见/最小化时返回 None（供站立跟随校验）。"""
    if (
        not user32.IsWindow(hwnd)
        or not user32.IsWindowVisible(hwnd)
        or user32.IsIconic(hwnd)
    ):
        return None
    return _frame_rect(hwnd)


class WindowPlatforms:
    """节流枚举可站立的窗口（按 z 序自上而下），提供下落着陆查询。"""

    def __init__(self):
        self._pid = os.getpid()
        self._items: list[tuple[int, Rect]] = []
        self._stamp = 0.0

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._stamp < REFRESH_INTERVAL:
            return
        self._stamp = now
        items: list[tuple[int, Rect]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def on_window(hwnd, _lparam):
            # 最大化窗口顶边贴屏幕顶，站上去会整只出屏，一并排除
            if (
                not user32.IsWindowVisible(hwnd)
                or user32.IsIconic(hwnd)
                or user32.IsZoomed(hwnd)
            ):
                return True
            if user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value in EXCLUDED_CLASSES:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self._pid:  # 排除桌宠自身窗口
                return True
            if _is_cloaked(hwnd):
                return True
            rect = _frame_rect(hwnd)
            if rect is None or rect[2] - rect[0] < MIN_WIDTH:
                return True
            items.append((hwnd, rect))
            return True

        user32.EnumWindows(on_window, 0)
        self._items = items

    def find_landing(self, cx: float, feet_min: float, feet_max: float) -> tuple[int, Rect] | None:
        """找 x=cx 处、顶边落在 [feet_min, feet_max] 内的最高未遮挡窗口。

        坐标均为物理像素；_items 为 z 序自上而下，用于遮挡判断。
        """
        best: tuple[int, Rect] | None = None
        for idx, (hwnd, rect) in enumerate(self._items):
            left, top, right, _ = rect
            if not (left <= cx <= right) or not (feet_min <= top <= feet_max):
                continue
            # 落点被更高 z 序窗口盖住则不可站
            covered = any(
                al <= cx <= ar and at <= top - 1 <= ab
                for _, (al, at, ar, ab) in self._items[:idx]
            )
            if covered:
                continue
            if best is None or top < best[1][1]:
                best = (hwnd, rect)
        return best
