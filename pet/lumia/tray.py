"""系统托盘：显示/隐藏宠物、退出。"""

from __future__ import annotations

import logging

from PyQt6.QtGui import QAction, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

log = logging.getLogger("lumia.tray")


def create_tray(app: QApplication, pet: QWidget, icon_pixmap: QPixmap) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("当前桌面环境不支持系统托盘，跳过托盘图标")
        return None

    tray = QSystemTrayIcon(QIcon(icon_pixmap), app)
    tray.setToolTip("Lumia 桌宠")

    menu = QMenu()
    act_toggle = QAction("显示/隐藏宠物", menu)
    act_clean = QAction("纯净模式（隐藏桌面）", menu)
    act_clean.setCheckable(True)
    act_quit = QAction("退出", menu)
    menu.addAction(act_toggle)
    menu.addAction(act_clean)
    menu.addSeparator()
    menu.addAction(act_quit)

    def toggle_visible():
        if pet.isVisible():
            log.info("托盘操作: 隐藏宠物")
            pet.hide()
        else:
            log.info("托盘操作: 显示宠物")
            pet.show()

    act_toggle.triggered.connect(toggle_visible)
    # 菜单弹出前同步勾选状态（可能已通过右键菜单/Esc 切换过）
    menu.aboutToShow.connect(lambda: act_clean.setChecked(pet.is_clean_mode()))
    act_clean.triggered.connect(
        lambda checked: (log.info("托盘操作: 纯净模式 = %s", checked), pet.set_clean_mode(checked))
    )
    act_quit.triggered.connect(lambda: (log.info("托盘操作: 退出"), app.quit()))
    # 单击托盘图标也切换显示
    tray.activated.connect(
        lambda reason: toggle_visible()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )

    tray.setContextMenu(menu)
    tray.show()
    log.info("托盘图标已创建")
    return tray
