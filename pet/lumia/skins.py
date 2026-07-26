"""皮肤目录解析与清单。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def catalog() -> list[dict]:
    path = ASSETS / "skins" / "catalog.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return list(data.get("skins") or [])
        except (OSError, json.JSONDecodeError):
            pass
    return [
        {"id": "bilibili_face", "name": "小电视·全屏脸", "path": "skins/bilibili_face"},
        {"id": "minecraft_cat", "name": "Minecraft 猫", "path": "sprites"},
        {"id": "bilibili_tv", "name": "小电视·简绘", "path": "skins/bilibili_tv"},
    ]


def resolve_sprites_dir(skin_id: str | None) -> Path:
    skin_id = (skin_id or "bilibili_face").strip()
    for item in catalog():
        if item.get("id") == skin_id:
            rel = str(item.get("path") or "")
            path = ASSETS / rel
            if path.is_dir() and any(path.iterdir()):
                return path
    # 回退
    for cand in (
        ASSETS / "skins" / skin_id,
        ASSETS / "skins" / "bilibili_face",
        ASSETS / "sprites",
        ASSETS / "skins" / "bilibili_tv",
    ):
        if cand.is_dir() and any(cand.iterdir()):
            return cand
    return ASSETS / "sprites"


def is_fullscreen_skin(sprites_dir: Path) -> bool:
    meta = sprites_dir / "meta.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if data.get("fullscreen"):
                return True
        except (OSError, json.JSONDecodeError):
            pass
    # 接近整屏尺寸才当全屏脸（避免 Minecraft 猫 760x560 被 height>=480 误判）
    for p in sprites_dir.glob("idle/*.png"):
        try:
            from PyQt6.QtGui import QPixmap

            pm = QPixmap(str(p))
            return pm.width() >= 900 and pm.height() >= 500
        except Exception:
            break
    return False
