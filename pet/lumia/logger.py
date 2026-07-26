"""日志体系：控制台 + 轮转文件双通道，附全局异常捕获。

日志文件位置：
- Linux:   ~/.local/share/lumia-pet/logs/lumia.log
- Windows: %LOCALAPPDATA%/lumia-pet/logs/lumia.log
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import APP_NAME

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def log_dir() -> Path:
    """返回平台对应的日志目录（不保证存在）。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME / "logs"


def setup_logging(debug: bool = False) -> logging.Logger:
    """初始化根日志器：控制台(INFO/DEBUG) + 轮转文件(DEBUG)。幂等。"""
    global _initialized
    root = logging.getLogger()
    if _initialized:
        return root
    _initialized = True

    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            d / "lumia.log", maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:  # 日志目录不可写时降级为仅控制台
        root.warning("无法创建日志文件目录，仅输出到控制台: %s", exc)

    _install_excepthook()
    return root


def _install_excepthook() -> None:
    """全局异常钩子：未捕获异常完整写入日志（Qt 回调中的异常也会经过这里）。"""
    log = logging.getLogger("lumia.crash")

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical("未捕获异常，程序可能即将退出", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = hook


def log_environment(log: logging.Logger) -> None:
    """启动时记录环境信息，便于换机排查。"""
    log.info("Python %s | %s %s", platform.python_version(), platform.system(), platform.release())
    log.info(
        "会话环境: XDG_SESSION_TYPE=%s QT_QPA_PLATFORM=%s DISPLAY=%s WAYLAND_DISPLAY=%s",
        os.environ.get("XDG_SESSION_TYPE", "<未设置>"),
        os.environ.get("QT_QPA_PLATFORM", "<未设置>"),
        os.environ.get("DISPLAY", "<未设置>"),
        os.environ.get("WAYLAND_DISPLAY", "<未设置>"),
    )
    log.info("日志目录: %s", log_dir())
