"""帧动画：按目录约定加载素材并按各状态帧率播放。

素材约定：assets/sprites/<状态名>/<序号>.png + meta.json
meta.json 声明默认朝向(facing)与各状态帧率(fps)。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QTransform

log = logging.getLogger("lumia.animation")

DEFAULT_FPS = 8
# 素材烘焙倍数：build_cat_sprites.py 中 SCALE=20，即原版 MC 猫的 5 倍
# （运行时按 config 的 scale/BAKED_SCALE 缩放到目标倍数）
BAKED_SCALE = 5.0


class SpriteLibrary:
    """扫描素材目录，持有各状态的帧序列（含镜像缓存）。

    display_scale: 相对烘焙帧图的显示缩放倍数（1.0 = 原图尺寸）。
    """

    def __init__(self, sprites_dir: Path, display_scale: float = 1.0):
        self.sprites_dir = sprites_dir
        self.display_scale = display_scale
        self.frames: dict[str, list[QPixmap]] = {}
        self.frames_mirrored: dict[str, list[QPixmap]] = {}
        self.fps: dict[str, int] = {}
        self.loop: dict[str, bool] = {}
        self.native_facing = "left"
        self._load()

    def _load(self) -> None:
        meta_path = self.sprites_dir / "meta.json"
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.native_facing = meta.get("facing", "left")
        fps_map = meta.get("fps", {})
        loop_map = meta.get("loop", {})

        mirror = QTransform().scale(-1, 1)
        for state_dir in sorted(self.sprites_dir.iterdir()):
            if not state_dir.is_dir():
                continue
            state = state_dir.name
            pngs = sorted(state_dir.glob("*.png"), key=lambda p: int(p.stem))
            frames = [QPixmap(str(p)) for p in pngs]
            frames = [f for f in frames if not f.isNull()]
            if not frames:
                log.warning("状态 %s 目录下没有可用帧图，已跳过", state)
                continue
            frames = [self._apply_scale(f) for f in frames]
            self.frames[state] = frames
            self.frames_mirrored[state] = [f.transformed(mirror) for f in frames]
            self.fps[state] = int(fps_map.get(state, DEFAULT_FPS))
            self.loop[state] = bool(loop_map.get(state, True))
            log.debug("加载状态 %s: %d 帧 @ %d FPS", state, len(frames), self.fps[state])

        if not self.frames:
            raise RuntimeError(
                f"素材目录 {self.sprites_dir} 为空，请先运行 scripts/build_cat_sprites.py"
            )
        log.info("素材加载完成: %s", {s: len(f) for s, f in self.frames.items()})

    def _apply_scale(self, pixmap: QPixmap) -> QPixmap:
        """按 display_scale 缩放帧图；最近邻采样保留像素风格。"""
        if abs(self.display_scale - 1.0) < 1e-3:
            return pixmap
        return pixmap.scaled(
            max(1, round(pixmap.width() * self.display_scale)),
            max(1, round(pixmap.height() * self.display_scale)),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    def frame_size(self) -> tuple[int, int]:
        first = next(iter(self.frames.values()))[0]
        return first.width(), first.height()


class Animator:
    """驱动某一状态的帧播放，按累计时间推进帧序号。"""

    def __init__(self, library: SpriteLibrary):
        self.lib = library
        self.state = "idle"
        self._elapsed = 0.0
        self._index = 0

    def set_state(self, state: str) -> None:
        if state == self.state:
            return
        if state not in self.lib.frames:
            log.warning("请求的状态 %s 无素材，回退到 idle", state)
            state = "idle"
        self.state = state
        self._elapsed = 0.0
        self._index = 0

    def update(self, dt: float) -> None:
        """dt: 秒。按状态帧率推进当前帧；非循环状态停在末帧。"""
        self._elapsed += dt
        interval = 1.0 / self.lib.fps.get(self.state, DEFAULT_FPS)
        n = len(self.lib.frames[self.state])
        while self._elapsed >= interval:
            self._elapsed -= interval
            if self._index + 1 >= n and not self.lib.loop.get(self.state, True):
                self._elapsed = 0.0
                break
            self._index = (self._index + 1) % n

    def current_frame(self, facing_left: bool) -> QPixmap:
        native_left = self.lib.native_facing == "left"
        use_native = facing_left == native_left
        source = self.lib.frames if use_native else self.lib.frames_mirrored
        return source[self.state][self._index]
