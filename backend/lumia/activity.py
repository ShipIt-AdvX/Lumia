"""前台程序 + 空闲检测: 判断当前这一秒是否算作 "编程".

仅 Windows 用 ctypes 实现; 其他平台返回 None / 0, 时间不计入.
"""
from __future__ import annotations

import sys

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    def foreground_process() -> str | None:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = _kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not handle:
            return None
        try:
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(size.value)
            if _kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            ):
                full = buf.value
                return full.rsplit("\\", 1)[-1] if full else None
            return None
        finally:
            _kernel32.CloseHandle(handle)

    def idle_seconds() -> float:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = _kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)

else:  # pragma: no cover - 非 Windows 兜底

    def foreground_process() -> str | None:
        return None

    def idle_seconds() -> float:
        return 0.0


def is_coding(dev_processes: list[str], idle_threshold: float) -> bool:
    """前台是开发程序、且用户没空闲, 才算在编程."""
    proc = foreground_process()
    if proc is None:
        return False
    if idle_seconds() > idle_threshold:
        return False
    wanted = {p.lower() for p in dev_processes}
    return proc.lower() in wanted
