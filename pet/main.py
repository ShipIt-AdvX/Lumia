"""Lumia 桌宠入口。

用法：
    python main.py [--debug]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPRITES_DIR = ROOT / "assets" / "sprites"

from lumia.logger import log_environment, setup_logging  # noqa: E402


def ensure_platform() -> None:
    """Wayland 会话下 Qt 无法自主移动窗口，强制走 XWayland (xcb)。"""
    if sys.platform.startswith("linux"):
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland" and "QT_QPA_PLATFORM" not in os.environ:
            os.environ["QT_QPA_PLATFORM"] = "xcb"
            logging.getLogger("lumia.main").info(
                "检测到 Wayland 会话，已设置 QT_QPA_PLATFORM=xcb (经 XWayland 运行)"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Lumia 桌宠")
    parser.add_argument("--debug", action="store_true", help="控制台输出 DEBUG 日志")
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    log = logging.getLogger("lumia.main")
    log.info("========== Lumia 启动 ==========")
    log_environment(log)
    ensure_platform()

    # 延迟导入 Qt：确保平台环境变量已生效
    from PyQt6.QtCore import QLockFile, QDir
    from PyQt6.QtWidgets import QApplication

    from lumia.config import Config
    from lumia.pet_window import PetWindow
    from lumia.tray import create_tray

    # 单实例锁
    lock = QLockFile(str(Path(QDir.tempPath()) / "lumia-pet.lock"))
    if not lock.tryLock(100):
        log.error("已有 Lumia 实例在运行，本次启动退出")
        return 1

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 隐藏宠物后仍驻留托盘
    app.setApplicationName("lumia-pet")
    log.info("Qt 平台插件: %s", app.platformName())

    # SIGTERM/SIGINT 走 Qt 正常退出，确保 aboutToQuit 清理（如恢复面板）执行
    # 到位；Python 信号处理器由宠物窗口 ~30Hz 主循环定时器驱动执行
    import signal

    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    if not SPRITES_DIR.exists() or not any(SPRITES_DIR.iterdir()):
        log.error("素材目录为空，请先运行: python scripts/build_cat_sprites.py")
        return 1

    config = Config()

    # Windows 仅保留桌宠本体：不处理开机自启与纯净模式（Ubuntu 专属）
    is_windows = sys.platform == "win32"

    # 已开启自启时每次启动刷新 .desktop（项目路径变动/Exec 格式升级后自愈）
    if not is_windows and config.get("autostart"):
        from lumia.autostart import set_autostart

        set_autostart(True)

    pet = PetWindow(SPRITES_DIR, config)
    pet.show()

    # 纯净模式：启动即用全屏幕布隐藏桌面/面板，只保留桌宠
    if not is_windows and config.get("clean_mode"):
        pet.set_clean_mode(True, save=False)

    icon = pet.animator.current_frame(facing_left=True)
    tray = create_tray(app, pet, icon)
    if tray is None:
        # 没有托盘时关闭最后窗口即退出，避免无法退出的僵尸进程
        app.setQuitOnLastWindowClosed(True)

    code = app.exec()
    log.info("========== Lumia 退出 (code=%d) ==========", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
