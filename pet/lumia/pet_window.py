"""桌宠主窗口：透明无边框置顶，承载动画绘制与鼠标交互。"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication, QMouseEvent, QPaintEvent, QPainter
from PyQt6.QtWidgets import QLabel, QMenu, QWidget

from .animation import Animator, SpriteLibrary
from .backdrop import BackdropWindow, PanelGuard
from .config import Config
from .skins import catalog, is_fullscreen_skin, resolve_sprites_dir
from .state_machine import PetStateMachine

log = logging.getLogger("lumia.window")

TICK_MS = 33  # 主循环 ~30Hz

_FONT_FAMILY: str | None = None  # 字体只注册一次（切皮肤重复 addApplicationFont 会累积）



def _bubble_font(point_size: int = 11) -> QFont:
    """气泡用 Minecraft AE（优先 pet/assets/fonts，其次仓库根 assets/）。"""
    global _FONT_FAMILY
    if _FONT_FAMILY is None:
        here = Path(__file__).resolve().parent.parent
        candidates = [
            here / "assets" / "fonts" / "Minecraft_AE.ttf",
            here.parent / "assets" / "Minecraft_AE.ttf",
        ]
        _FONT_FAMILY = "Minecraft AE"
        for font_path in candidates:
            if not font_path.exists():
                continue
            fid = QFontDatabase.addApplicationFont(str(font_path))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                _FONT_FAMILY = fams[0]
                log.info("气泡字体: %s (%s)", _FONT_FAMILY, font_path)
                break
        else:
            log.warning("未找到 Minecraft_AE.ttf，气泡回退系统字体")
    font = QFont(_FONT_FAMILY, point_size)
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    return font


class PetWindow(QWidget):
    _brain_debug_done = pyqtSignal(str, object)  # 后台调试请求结果 -> 主线程

    def __init__(self, sprites_dir: Path, config: Config):
        super().__init__()
        self.config = config
        self.library = SpriteLibrary(sprites_dir)
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
        self._base_w, self._base_h = w, h
        self._fullscreen_face = is_fullscreen_skin(sprites_dir)
        self.setFixedSize(w, h)

        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._backdrop: BackdropWindow | None = None  # 纯净模式幕布
        self._panel_guard = PanelGuard()
        # 无论何种方式退出都恢复面板，避免桌面残缺
        QGuiApplication.instance().aboutToQuit.connect(self._panel_guard.restore)
        # 亚像素位置累积（窗口坐标为整数，用浮点累积避免慢速移动丢步）
        self._fx = 0.0
        self._fy = 0.0
        self._scale = 1.0
        self._director_action = "idle"
        self._bubble_until = 0.0
        # 重绘与缩放缓存：动画实际 ~8fps，30Hz 主循环里画面没变就不重绘；
        # 全屏脸的平滑缩放代价极高，只在帧/尺寸变化时重算
        self._paint_key: tuple | None = None
        self._scaled_key: tuple | None = None
        self._scaled_frame = None
        self._brain_debug_done.connect(self._on_brain_debug_result)

        self._bubble = QLabel(self)
        self._bubble.setWordWrap(True)
        self._bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bubble_pt = 22 if self._fullscreen_face else 11
        self._bubble.setFont(_bubble_font(bubble_pt))
        self._bubble.setStyleSheet(
            "QLabel {"
            " background: rgba(0,0,0,200);"
            " color: #00aeec;"
            " border-radius: 8px;"
            " padding: 10px 16px;"
            " border: 2px solid #00aeec;"
            "}"
            if self._fullscreen_face
            else
            "QLabel {"
            " background: rgba(255,255,255,230);"
            " color: #222;"
            " border-radius: 12px;"
            " padding: 6px 10px;"
            " border: 1px solid rgba(0,0,0,40);"
            "}"
        )
        self._bubble.hide()

        if self._fullscreen_face:
            # 全屏脸：不乱走；久坐时靠缩小表示走远
            self.machine.walking_enabled = False
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

    def _on_ground(self) -> bool:
        if self._fullscreen_face:
            return True  # 全屏脸不参与重力/下落
        return self.y() >= self._ground_y() - 1

    def _place_initial(self) -> None:
        scr = self.screen() or QGuiApplication.primaryScreen()
        full = scr.geometry()
        if self._fullscreen_face:
            # 真正铺满整块屏（奖杯 OLED 脸）
            self.setFixedSize(full.width(), full.height())
            self._base_w, self._base_h = full.width(), full.height()
            self.move(full.left(), full.top())
            self._fx, self._fy = float(full.left()), float(full.top())
            log.info("全屏脸模式，铺满: %s", full)
            return
        area = self._work_area()
        x = area.left() + int(area.width() * 0.7)
        self.move(x, self._ground_y())
        self._fx, self._fy = float(x), float(self._ground_y())
        log.info("初始位置: (%d, %d)", self.x(), self.y())

    # --- 纯净模式 ---

    def is_clean_mode(self) -> bool:
        return self._backdrop is not None

    def set_clean_mode(self, enabled: bool, save: bool = True) -> None:
        if enabled == self.is_clean_mode():
            return
        if enabled:
            self._backdrop = BackdropWindow(
                self.screen(), solid_black=self._fullscreen_face
            )
            self._backdrop.exit_requested.connect(lambda: self.set_clean_mode(False))
            self._backdrop.pressed.connect(self.raise_)
            self._backdrop.showFullScreen()
            self.raise_()  # 桌宠回到幕布之上
            self._panel_guard.hide()
            self.setCursor(Qt.CursorShape.BlankCursor)  # 全屏无鼠标（幕布+桌宠都隐藏）
            if self._fullscreen_face:
                self._place_initial()
                self.raise_()
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

    # --- 导演（大脑） ---

    def apply_director(self, snap: dict[str, Any]) -> None:
        # Windows 设置改外形 → 大脑 snapshot.skin → 地瓜派热切换
        want_skin = str(snap.get("skin") or "").strip()
        cur_skin = str(self.config.get("skin") or "")
        if want_skin and want_skin != cur_skin:
            self.set_skin(want_skin, from_brain=True)

        action = str(snap.get("action") or "idle")
        bubble = str(snap.get("bubble") or "")
        scale = float(snap.get("scale") or 1.0)
        prev = self._director_action
        self._director_action = action

        mode = action if action in ("sleep", "meal", "sit_away") else None
        self.machine.set_director_mode(mode)
        # 全屏脸不缩窗；桌宠（猫）跟随久坐 scale
        if not self._fullscreen_face:
            self._apply_scale(scale)
        elif abs(self._scale - 1.0) > 0.02:
            self._apply_scale(1.0)

        if action != prev and bubble:
            self.say(bubble, ms=4500)
        elif action in ("sleep", "meal", "sit_away") and bubble:
            # 周期性刷新气泡，避免用户忘了
            if time.monotonic() > self._bubble_until - 0.5:
                self.say(bubble, ms=4000)

    def say(self, text: str, ms: int = 2500) -> None:
        if not text:
            return
        self._bubble.setText(text)
        self._bubble.adjustSize()
        bw = min(max(self._bubble.sizeHint().width(), 80), max(160, self.width() + 40))
        self._bubble.setFixedWidth(bw)
        self._bubble.adjustSize()
        x = max(0, (self.width() - self._bubble.width()) // 2)
        y = max(0, -self._bubble.height() + 8)
        # 气泡放在窗口内顶部（窗口太矮时贴顶）
        if y < 0:
            y = 4
        self._bubble.move(x, y)
        self._bubble.show()
        self._bubble.raise_()
        self._bubble_until = time.monotonic() + ms / 1000.0

    def _apply_scale(self, scale: float) -> None:
        scale = max(0.3, min(1.2, scale))
        if abs(scale - self._scale) < 0.02:
            return
        self._scale = scale
        w = max(24, int(self._base_w * scale))
        h = max(24, int(self._base_h * scale))
        self.setFixedSize(w, h)
        # 缩小时贴地面
        self._fy = float(self._ground_y())
        self.move(round(self._fx), round(self._fy))
        log.info("缩放 -> %.2f 尺寸 %dx%d", scale, w, h)

    # --- 主循环 ---

    def _on_tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.1)  # 防止休眠恢复后大步长跳变
        self._last_tick = now

        if self._bubble.isVisible() and now >= self._bubble_until:
            self._bubble.hide()

        area = self._work_area()
        dx, dy = self.machine.tick(
            dt,
            on_ground=self._on_ground(),
            at_left_edge=self.x() <= area.left(),
            at_right_edge=self.x() + self.width() >= area.right(),
        )

        if not self._dragging and (dx or dy):
            self._fx = max(area.left(), min(self._fx + dx, area.right() - self.width() + 1))
            self._fy = min(self._fy + dy, float(self._ground_y()))
            self.move(round(self._fx), round(self._fy))

        self.animator.set_state(self.machine.state)
        self.animator.update(dt)
        # 画面未变（同帧同朝向）则跳过重绘，避免 30Hz 无效全窗口刷新
        key = (self.animator.state, self.animator.frame_index, self.machine.facing_left)
        if key != self._paint_key:
            self._paint_key = key
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        frame = self.animator.current_frame(self.machine.facing_left)
        # 全屏脸 / display_scale / 导演缩放：窗口与原图不一致时拉伸铺满；
        # 平滑缩放开销大，结果按 (状态, 帧, 朝向, 尺寸) 缓存，重绘直接复用
        if frame.width() != self.width() or frame.height() != self.height():
            key = (
                self.animator.state,
                self.animator.frame_index,
                self.machine.facing_left,
                self.width(),
                self.height(),
            )
            if key != self._scaled_key or self._scaled_frame is None:
                self._scaled_frame = frame.scaled(
                    self.size(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._scaled_key = key
            painter.drawPixmap(0, 0, self._scaled_frame)
        else:
            painter.drawPixmap(0, 0, frame)

    # --- 鼠标交互 ---

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.machine.director_mode == "sleep" or self.machine.state == "sleep":
                # 该睡觉时点猫会生气
                if self.machine.director_mode == "sleep":
                    self.machine.anger_burst()
                    self.say("哼！这时候还点我？去睡觉！", ms=2800)
                event.accept()
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

        act_clean = menu.addAction("纯净模式（隐藏桌面）")
        act_clean.setCheckable(True)
        act_clean.setChecked(self.is_clean_mode())

        act_autostart = menu.addAction("开机自启")
        act_autostart.setCheckable(True)
        act_autostart.setChecked(self.config.get("autostart"))

        menu.addSeparator()
        skin_menu = menu.addMenu("外形皮肤")
        skin_actions: list[tuple[object, str]] = []
        current_skin = str(self.config.get("skin") or "bilibili_tv")
        for item in catalog():
            sid = str(item.get("id") or "")
            name = str(item.get("name") or sid)
            act = skin_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(sid == current_skin)
            skin_actions.append((act, sid))

        menu.addSeparator()
        dbg = menu.addMenu("调试·强制行为")
        act_dbg_auto = dbg.addAction("恢复自动")
        act_dbg_idle = dbg.addAction("闲逛 idle")
        act_dbg_sleep = dbg.addAction("睡觉 sleep")
        act_dbg_meal = dbg.addAction("吃饭 meal")
        act_dbg_away = dbg.addAction("久坐走远 sit_away")

        menu.addSeparator()
        act_hide = menu.addAction("隐藏到托盘")
        act_about = menu.addAction("关于")
        menu.addSeparator()
        act_quit = menu.addAction("退出")

        chosen = menu.exec(event.globalPos())
        menu.deleteLater()  # 菜单以窗口为 parent，不销毁会随右键次数累积
        if chosen is act_walk:
            self.toggle_walking(act_walk.isChecked())
        elif chosen is act_clean:
            log.info("菜单操作: 纯净模式 = %s", act_clean.isChecked())
            self.set_clean_mode(act_clean.isChecked())
        elif chosen is act_autostart:
            self.toggle_autostart(act_autostart.isChecked())
        elif chosen is act_dbg_auto:
            self._force_brain_debug("auto")
        elif chosen is act_dbg_idle:
            self._force_brain_debug("idle")
        elif chosen is act_dbg_sleep:
            self._force_brain_debug("sleep")
        elif chosen is act_dbg_meal:
            self._force_brain_debug("meal")
        elif chosen is act_dbg_away:
            self._force_brain_debug("sit_away")
        elif chosen is act_hide:
            log.info("菜单操作: 隐藏到托盘")
            self.hide()
        elif chosen is act_about:
            self._show_about()
        elif chosen is act_quit:
            log.info("菜单操作: 退出")
            QGuiApplication.instance().quit()
        else:
            for act, sid in skin_actions:
                if chosen is act:
                    self.set_skin(sid)
                    break

    def set_skin(self, skin_id: str, *, from_brain: bool = False) -> None:
        """热切换外形（小电视全屏脸 / 猫）。from_brain=True 时不回写大脑。"""
        skin_id = (skin_id or "bilibili_face").strip()
        if skin_id == str(self.config.get("skin") or ""):
            return
        path = resolve_sprites_dir(skin_id)
        if not path.exists():
            self.say(f"皮肤不存在: {skin_id}", ms=2000)
            return
        try:
            lib = SpriteLibrary(path)
        except RuntimeError as exc:
            log.error("加载皮肤失败: %s", exc)
            self.say("皮肤加载失败", ms=2000)
            return
        self.library = lib
        self.animator = Animator(lib)
        w, h = lib.frame_size()
        self._base_w, self._base_h = w, h
        self._fullscreen_face = is_fullscreen_skin(path)
        self._scale = 1.0
        self._paint_key = None    # 新皮肤同名状态/帧号也是新画面，强制重绘
        self._scaled_key = None
        self._scaled_frame = None
        self.setFixedSize(w, h)
        bubble_pt = 22 if self._fullscreen_face else 11
        self._bubble.setFont(_bubble_font(bubble_pt))
        if self._fullscreen_face:
            self.machine.walking_enabled = False
            if not self.is_clean_mode():
                self.set_clean_mode(True, save=True)
        else:
            # 猫等桌宠：桌面行走，退出全屏幕布
            self.machine.walking_enabled = bool(self.config.get("walking_enabled"))
            if self.is_clean_mode():
                self.set_clean_mode(False, save=True)
            else:
                self.config.set("clean_mode", False)
        self._place_initial()
        self.config.set("skin", skin_id)
        name = next((s.get("name") for s in catalog() if s.get("id") == skin_id), skin_id)
        self.say(f"外形 → {name}", ms=2200)
        log.info("切换皮肤: %s fullscreen=%s (%s)", skin_id, self._fullscreen_face, path)
        if not from_brain:
            self._push_skin_to_brain(skin_id)

    def _push_skin_to_brain(self, skin_id: str) -> None:
        """右键本地切换时回写大脑，与 Windows 设置对齐（后台线程，不阻主线程）。"""
        url = str(self.config.get("brain_url") or "").rstrip("/")
        if not url:
            return
    
        def worker() -> None:
            import json
            import urllib.error
            import urllib.request
    
            try:
                body = json.dumps({"skin": skin_id}).encode()
                req = urllib.request.Request(
                    f"{url}/api/pet/skin",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    resp.read()
                log.info("已同步外形到大脑: %s", skin_id)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.warning("同步外形到大脑失败: %s", exc)
    
        threading.Thread(target=worker, daemon=True, name="skin-push").start()
    
    def _force_brain_debug(self, action: str, minutes: float = 15) -> None:
        """向电脑大脑 POST /api/pet/debug（后台线程）；失败则本地直接套导演。"""
        url = str(self.config.get("brain_url") or "").rstrip("/")
        if not url:
            self._apply_local_debug(action)
            return
    
        def worker() -> None:
            import json
            import urllib.error
            import urllib.request
    
            try:
                body = json.dumps({"action": action, "minutes": minutes}).encode()
                req = urllib.request.Request(
                    f"{url}/api/pet/debug",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode())
                self._brain_debug_done.emit(action, data.get("state") or {})
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                log.warning("大脑调试失败，改本地强制: %s", exc)
                self._brain_debug_done.emit(action, None)
    
        threading.Thread(target=worker, daemon=True, name="brain-debug").start()
    
    def _on_brain_debug_result(self, action: str, snap: object) -> None:
        """主线程：应用大脑调试结果，None 表示请求失败走本地兜底。"""
        if snap is None:
            self._apply_local_debug(action)
            return
        if snap:
            self.apply_director(snap)
        self.say(f"调试 → {action}", ms=2000)
        log.info("已请求大脑调试: %s", action)
    
    def _apply_local_debug(self, action: str) -> None:
        """离线兜底：本地直接套导演模式。"""
        mode = None if action in ("auto", "idle") else action
        self.machine.set_director_mode(mode)
        bubbles = {
            "idle": "（本地调试）闲逛",
            "sleep": "（本地调试）去睡觉！",
            "meal": "（本地调试）去吃饭！",
            "sit_away": "（本地调试）走远啦",
            "auto": "（本地调试）恢复自动",
        }
        self._apply_scale(0.45 if action == "sit_away" else 1.0)
        self.say(bubbles.get(action, action), ms=2500)

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
            "Lumia 桌宠：小电视 / Minecraft 猫可切换。\n"
            "猫贴图版权归 Mojang Studios 所有，仅供个人使用。",
        )
