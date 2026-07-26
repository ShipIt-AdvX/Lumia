"""桌宠主窗口：透明无边框置顶，承载动画绘制与鼠标交互。"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QMouseEvent, QPaintEvent, QPainter
from PyQt6.QtWidgets import QMenu, QWidget

from .animation import BAKED_SCALE, Animator, SpriteLibrary
from .backdrop import BackdropWindow, PanelGuard
from .config import Config
from .state_machine import PetStateMachine

if sys.platform == "win32":
    from . import winsurface
else:
    winsurface = None  # 站立窗口顶边为 Windows 专属功能

log = logging.getLogger("lumia.window")

TICK_MS = 33  # 主循环 ~30Hz


class PetWindow(QWidget):
    def __init__(self, sprites_dir: Path, config: Config):
        super().__init__()
        self.config = config
        # config 的 scale 以原版 MC 猫为基准，换算为相对烘焙帧图的缩放
        scale = float(config.get("scale") or BAKED_SCALE)
        self.library = SpriteLibrary(sprites_dir, display_scale=scale / BAKED_SCALE)
        log.info("桌宠大小: %.2fx 原版（帧图 %sx%s）", scale, *self.library.frame_size())
        self.animator = Animator(self.library)
        self.machine = PetStateMachine(walking_enabled=config.get("walking_enabled"))

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)  # 无按键悬停也产生 mouseMoveEvent（趴卧抬头用）
        w, h = self.library.frame_size()
        self.setFixedSize(w, h)

        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._backdrop: BackdropWindow | None = None  # 纯净模式幕布
        self._panel_guard = PanelGuard()
        # Windows：可站立的窗口顶边平台（非 win32 为 None）
        self._platforms = winsurface.WindowPlatforms() if winsurface else None
        self._platform_hwnd: int | None = None  # 当前站着的窗口
        self._platform_y = 0                    # 站立时桌宠的 y（逻辑像素）
        # 无论何种方式退出都恢复面板，避免桌面残缺
        QGuiApplication.instance().aboutToQuit.connect(self._panel_guard.restore)
        # 亚像素位置累积（窗口坐标为整数，用浮点累积避免慢速移动丢步）
        self._fx = 0.0
        self._fy = 0.0

        self._place_initial()

        self._last_tick = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(TICK_MS)

        screen = QGuiApplication.primaryScreen()
        log.info("屏幕几何: %s 可用区域: %s", screen.geometry(), screen.availableGeometry())

    # --- 几何工具 ---

    def _work_area(self):
        # 纯净模式下面板被幕布盖住，使用整块屏幕作为活动区域
        scr = self.screen()
        return scr.geometry() if self._backdrop is not None else scr.availableGeometry()

    def _ground_y(self) -> int:
        return self._work_area().bottom() - self.height() + 1

    def _floor_y(self) -> int:
        # 当前有效支撑面：站在窗口顶边时为窗口顶，否则为屏幕地面
        if self._platform_hwnd is not None:
            return self._platform_y
        return self._ground_y()

    def _on_ground(self) -> bool:
        return self.y() >= self._floor_y() - 1

    def _place_initial(self) -> None:
        area = self._work_area()
        x = area.left() + int(area.width() * 0.7)
        self.move(x, self._ground_y())
        self._fx, self._fy = float(x), float(self._ground_y())
        log.info("初始位置: (%d, %d)", self.x(), self.y())

    # --- 窗口顶边平台（Windows）---

    def _perch_enabled(self) -> bool:
        return self._platforms is not None and bool(self.config.get("perch_on_windows"))

    def _sync_platform(self) -> None:
        """站立中跟随窗口移动；窗口消失/走出顶边范围则脱离转下落。"""
        if self._platform_hwnd is None:
            return
        if self._dragging or not self._perch_enabled():
            self._platform_hwnd = None
            return
        rect = winsurface.window_rect(self._platform_hwnd)
        if rect is None:
            log.debug("平台窗口消失，脱离下落")
            self._platform_hwnd = None
            return
        dpr = self.screen().devicePixelRatio()
        left, top, right = rect[0] / dpr, rect[1] / dpr, rect[2] / dpr
        cx = self._fx + self.width() / 2
        pet_y = round(top) - self.height()
        if not (left <= cx <= right) or pet_y < self._work_area().top() or pet_y > self._ground_y():
            log.debug("走出窗口顶边或窗口移到不可站位置，脱离")
            self._platform_hwnd = None
            return
        self._platform_y = pet_y
        if abs(pet_y - self._fy) >= 1:  # 窗口垂直移动时跟随（骑窗）
            self._fy = float(pet_y)
            self.move(round(self._fx), pet_y)

    def _try_land_on_window(self, dy: float) -> bool:
        """下落中检测脚底跨过的窗口顶边，命中则吸附站立。"""
        self._platforms.refresh()
        dpr = self.screen().devicePixelRatio()
        cx = (self._fx + self.width() / 2) * dpr
        feet = (self._fy + self.height()) * dpr
        hit = self._platforms.find_landing(cx, feet, feet + dy * dpr)
        if hit is None:
            return False
        hwnd, rect = hit
        pet_y = round(rect[1] / dpr) - self.height()
        # 站上去会出屏或低于地面的不吸附，继续落向地面
        if pet_y < self._work_area().top() or pet_y >= self._ground_y():
            return False
        self._platform_hwnd = hwnd
        self._platform_y = pet_y
        self._fy = float(pet_y)
        log.debug("落在窗口顶边 hwnd=%#x y=%d", hwnd, pet_y)
        return True

    # --- 纯净模式 ---

    def is_clean_mode(self) -> bool:
        return self._backdrop is not None

    def set_clean_mode(self, enabled: bool, save: bool = True) -> None:
        if enabled == self.is_clean_mode():
            return
        if enabled and sys.platform == "win32":
            log.info("Windows 平台不启用纯净模式，避免全屏幕布覆盖桌面")
            return
        if enabled:
            self._backdrop = BackdropWindow(self.screen())
            self._backdrop.exit_requested.connect(lambda: self.set_clean_mode(False))
            self._backdrop.pressed.connect(self.raise_)
            self._backdrop.showFullScreen()
            self.raise_()  # 桌宠回到幕布之上
            self._panel_guard.hide()
            self.setCursor(Qt.CursorShape.BlankCursor)  # 全屏无鼠标（幕布+桌宠都隐藏）
            log.info("纯净模式开启：幕布覆盖 %s", self._backdrop.geometry())
        else:
            self._backdrop.close()
            self._backdrop.deleteLater()
            self._backdrop = None
            self._panel_guard.restore()
            self.unsetCursor()
            # 退出后工作区变小（面板重新占位），若宠物陷在面板区内则抬回地面
            if self.y() > self._ground_y():
                self._fy = float(self._ground_y())
                self.move(self.x(), self._ground_y())
            log.info("纯净模式关闭，桌面已还原")
        if save:
            self.config.set("clean_mode", enabled)

    def showEvent(self, event) -> None:
        # 从托盘恢复显示时同步恢复幕布与面板隐藏
        if self._backdrop is not None:
            self._backdrop.showFullScreen()
            self.raise_()
            self._panel_guard.hide()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        # 隐藏宠物时一并收起幕布并恢复面板，避免留下残缺桌面
        if self._backdrop is not None:
            self._backdrop.hide()
            self._panel_guard.restore()
        super().hideEvent(event)

    # --- 主循环 ---

    def _on_tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.1)  # 防止休眠恢复后大步长跳变
        self._last_tick = now

        area = self._work_area()
        self._sync_platform()
        dx, dy = self.machine.tick(
            dt,
            on_ground=self._on_ground(),
            at_left_edge=self.x() <= area.left(),
            at_right_edge=self.x() + self.width() >= area.right(),
        )

        if not self._dragging and (dx or dy):
            self._fx = max(area.left(), min(self._fx + dx, area.right() - self.width() + 1))
            landed = False
            if dy > 0 and self._platform_hwnd is None and self._perch_enabled():
                landed = self._try_land_on_window(dy)
            if not landed:
                self._fy = min(self._fy + dy, float(self._floor_y()))
            self.move(round(self._fx), round(self._fy))

        self.animator.set_state(self.machine.state)
        self.animator.update(dt)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.animator.current_frame(self.machine.facing_left))

    # --- 鼠标交互 ---

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.machine.state == "sleep":
                event.accept()  # 睡眠中忽略按下（不记拖拽起点），仅双击可唤醒
                return
            self._drag_offset = event.position().toPoint()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            # 无按键悬停：趴卧时抬头张望（睡眠在状态机内忽略）
            self.machine.notify_mouse_move()
            return
        if not self._dragging:
            self._dragging = True
            self._platform_hwnd = None  # 抽离支撑面，松手后重新判定落点
            self.machine.start_drag()
            log.debug("开始拖拽 @ (%d, %d)", self.x(), self.y())
        pos = event.globalPosition().toPoint() - self._drag_offset
        self.move(pos)
        self._fx, self._fy = float(pos.x()), float(pos.y())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            if self._dragging:
                self._dragging = False
                self.machine.end_drag(on_ground=self._on_ground())
                log.debug("结束拖拽 @ (%d, %d) 状态=%s", self.x(), self.y(), self.machine.state)
            else:
                # 未产生拖拽即为单击：趴卧时起身行走
                self.machine.on_click()
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            log.info("双击互动：唤醒/转身看向观察者")
            self.machine.play_gesture()
            event.accept()

    # --- 右键菜单 ---

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)

        act_walk = menu.addAction("允许走动")
        act_walk.setCheckable(True)
        act_walk.setChecked(self.machine.walking_enabled)

        # Windows 专属：站立窗口顶边开关
        act_perch = None
        if sys.platform == "win32":
            act_perch = menu.addAction("可站上窗口")
            act_perch.setCheckable(True)
            act_perch.setChecked(bool(self.config.get("perch_on_windows")))

        # Windows 仅保留桌宠本体：纯净模式与开机自启为 Ubuntu 专属功能
        act_clean = act_autostart = None
        if sys.platform != "win32":
            act_clean = menu.addAction("纯净模式（隐藏桌面）")
            act_clean.setCheckable(True)
            act_clean.setChecked(self.is_clean_mode())

            act_autostart = menu.addAction("开机自启")
            act_autostart.setCheckable(True)
            act_autostart.setChecked(self.config.get("autostart"))

        menu.addSeparator()
        act_hide = menu.addAction("隐藏到托盘")
        act_about = menu.addAction("关于")
        menu.addSeparator()
        act_quit = menu.addAction("退出")
# woshizhushi 114514awdawdawdawdawd
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen is act_walk:
            self.toggle_walking(act_walk.isChecked())
        elif chosen is act_perch:
            log.info("菜单操作: 可站上窗口 = %s", act_perch.isChecked())
            self.config.set("perch_on_windows", act_perch.isChecked())
        elif chosen is act_clean:
            log.info("菜单操作: 纯净模式 = %s", act_clean.isChecked())
            self.set_clean_mode(act_clean.isChecked())
        elif chosen is act_autostart:
            self.toggle_autostart(act_autostart.isChecked())
        elif chosen is act_hide:
            log.info("菜单操作: 隐藏到托盘")
            self.hide()
        elif chosen is act_about:
            self._show_about()
        elif chosen is act_quit:
            log.info("菜单操作: 退出")
            QGuiApplication.instance().quit()

    def toggle_walking(self, enabled: bool) -> None:
        log.info("菜单操作: 允许走动 = %s", enabled)
        self.machine.walking_enabled = enabled
        self.config.set("walking_enabled", enabled)

    def toggle_autostart(self, enabled: bool) -> None:
        from .autostart import set_autostart

        log.info("菜单操作: 开机自启 = %s", enabled)
        if set_autostart(enabled):
            self.config.set("autostart", enabled)

    def _show_about(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

        from . import __version__

        QMessageBox.about(
            self,
            "关于 Lumia",
            f"Lumia 桌宠 v{__version__}\n\n"
            "一只 Minecraft 花斑猫桌面宠物。\n"
            "猫贴图版权归 Mojang Studios 所有，仅供个人使用。",
        )
