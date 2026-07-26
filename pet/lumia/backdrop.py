"""纯净模式幕布：全屏窗口盖住桌面图标与壁纸，只露出桌宠。

原理：幕布窗口盖住桌面图标/壁纸；面板 (Dock) 在 xfwm4 中层级高于
置顶窗口，无法被盖住，故通过 PanelGuard 退出/重启 xfce4-panel 实现
隐藏与还原。退出纯净模式即完整恢复桌面。

幕布背景优先使用 assets/pdbg.png（等比铺满居中裁剪），缺失时回退
夜空渐变色。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPaintEvent, QPixmap, QScreen
from PyQt6.QtWidgets import QWidget

log = logging.getLogger("lumia.backdrop")

BG_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "pdbg.png"


class BackdropWindow(QWidget):
    """覆盖整块屏幕的幕布；Esc 或双击幕布可退出纯净模式。"""

    exit_requested = pyqtSignal()
    pressed = pyqtSignal()

    def __init__(self, screen: QScreen, *, solid_black: bool = False):
        super().__init__()
        # 置顶 + 全屏；桌宠随后 raise 到幕布之上；Tool 避免出现在任务栏
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowTitle("Lumia 幕布")
        self.setCursor(Qt.CursorShape.BlankCursor)  # 纯净模式不显示鼠标
        self.setGeometry(screen.geometry())
        self._solid_black = solid_black

        # 预缩放背景图：等比铺满并居中裁剪（cover），避免每帧重缩放
        self._bg: QPixmap | None = None
        if solid_black:
            log.info("幕布背景: 纯黑（全屏脸）")
        elif BG_IMAGE.exists():
            raw = QPixmap(str(BG_IMAGE))
            if not raw.isNull():
                scaled = raw.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = (scaled.width() - self.width()) // 2
                y = (scaled.height() - self.height()) // 2
                self._bg = scaled.copy(x, y, self.width(), self.height())
                log.info("幕布背景已加载: %s (原图 %dx%d)", BG_IMAGE, raw.width(), raw.height())
            else:
                log.warning("幕布背景图无法解码，回退渐变色: %s", BG_IMAGE)
        else:
            log.warning("幕布背景图不存在，回退渐变色: %s", BG_IMAGE)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        if self._solid_black:
            painter.fillRect(self.rect(), QColor(0, 0, 0))
            return
        if self._bg is not None:
            painter.drawPixmap(0, 0, self._bg)
            return
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor("#1b2735"))  # 夜空渐变，避免纯黑过于压抑
        grad.setColorAt(1.0, QColor("#090a0f"))
        painter.fillRect(self.rect(), grad)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            log.info("幕布收到 Esc，请求退出纯净模式")
            self.exit_requested.emit()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.pressed.emit()  # 供桌宠重新置顶，防止被幕布压住
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        log.info("幕布被双击，请求退出纯净模式")
        self.exit_requested.emit()
        event.accept()


class PanelGuard:
    """隐藏/恢复桌面面板（目前支持 Xfce，其他环境静默跳过）。"""

    def __init__(self):
        self._hidden = False

    @staticmethod
    def _panel_running() -> bool:
        try:
            return subprocess.run(
                ["pgrep", "-x", "xfce4-panel"], capture_output=True, timeout=5
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def hide(self) -> None:
        if self._hidden or not shutil.which("xfce4-panel"):
            return
        # 先记录再退出：即使面板当前未运行（如上次异常退出未恢复），
        # 也能在退出纯净模式时将其拉起来
        self._hidden = True
        if not self._panel_running():
            log.info("xfce4-panel 未在运行，跳过退出（恢复时仍会拉起）")
            return
        # 刚被 restore 拉起的面板可能尚未就绪，--quit 消息会丢失；
        # 退出后校验并重试，避免快速切换纯净模式时面板残留
        try:
            for attempt in range(3):
                subprocess.run(["xfce4-panel", "--quit"], capture_output=True, timeout=10)
                time.sleep(0.4)
                if not self._panel_running():
                    log.info("已退出 xfce4-panel（面板隐藏，第 %d 次尝试）", attempt + 1)
                    return
            log.warning("xfce4-panel 多次 --quit 后仍在运行，改用 pkill 终止")
            subprocess.run(["pkill", "-x", "xfce4-panel"], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("隐藏面板失败: %s", exc)

    def restore(self) -> None:
        if not self._hidden:
            return
        self._hidden = False
        try:
            subprocess.Popen(
                ["xfce4-panel"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("已重启 xfce4-panel（面板恢复）")
        except OSError as exc:
            log.warning("恢复面板失败: %s，可手动执行 xfce4-panel &", exc)
