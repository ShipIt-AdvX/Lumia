#!/usr/bin/env python3
"""用 ElectronBili 开源表情（B 站小电视脸）烘焙全屏精灵。

素材来源：https://github.com/sytnocui/ElectronBili （风格对齐稚晖君 ElectronBot / HoloCubic 屏）
输出：assets/skins/bilibili_face/  1024x600 黑底大脸，适合地瓜派整屏。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "skins" / "bilibili_face"
RAW = ROOT / "assets" / "skins" / "bilibili_face" / "_raw"

# OPi 屏幕
W, H = 1024, 600
FACE_W = 860  # 脸区域宽度，接近全屏

# ElectronBili 表情目录 -> 我们的状态
# 源可在环境变量 ELECTRONBILI_ROOT，或默认 /tmp/ElectronBili
EMOJI_MAP = {
    "idle": ("眨眼", None),           # 眨眼循环
    "walk": ("左右看", "循环"),         # 走远/张望
    "look": ("笑嘻", "循环"),          # 吃饭氛围
    "sleep": ("电池", None),           # 低电/休息感
    "rest": ("无语", "循环"),
    "rest_peek": ("上看", "循环"),
    "lie_down": ("无语", "进出"),
    "get_up": ("笑嘻", "进出"),
    "turn": ("左右看", "进出"),
    "turn_back": ("左右看", "进出"),
    "drag": ("发功", "循环"),          # 生气炸毛（anger 走 drag/fall）
    "fall": ("闪电", None),
    "land": ("笑", None),
}


def find_emoji_root() -> Path:
    env = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    cands = [
        env,
        Path("/tmp/ElectronBili/3.Resources/Emoji/picture"),
        RAW,
    ]
    for c in cands:
        if c and c.is_dir() and any(c.iterdir()):
            return c
    raise SystemExit(
        "找不到 ElectronBili 表情目录。请先:\n"
        "  git clone --depth 1 https://github.com/sytnocui/ElectronBili.git /tmp/ElectronBili\n"
        "或传入路径: python scripts/build_bilibili_fullscreen.py /path/to/picture"
    )


def list_frames(root: Path, name: str, sub: str | None) -> list[Path]:
    base = root / name
    if sub:
        base = base / sub
    frames = sorted(base.glob("frame*.png"))
    if not frames and sub is None:
        # 尝试 循环
        frames = sorted((root / name / "循环").glob("frame*.png"))
    return frames


def compose(face: Image.Image) -> Image.Image:
    """纯黑全屏 + 居中脸（无边框、无天线，只留五官）。"""
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    # 抠掉白/浅色底，只留青色五官
    rgba = face.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            # 近白 / 浅灰 → 透明
            if r > 200 and g > 200 and b > 200:
                pixels[x, y] = (0, 0, 0, 0)
                continue
            # 非品牌青的杂色也尽量清掉（保留偏青像素）
            if b < 140 or g < 80 or r > g + 40:
                # 允许闪电等稍亮的青；过暖/过暗的非脸像素去掉
                if not (b >= 150 and g >= 100 and b >= r):
                    pixels[x, y] = (0, 0, 0, 0)

    ratio = FACE_W / rgba.width
    nh = max(1, int(rgba.height * ratio))
    scaled = rgba.resize((FACE_W, nh), Image.Resampling.LANCZOS)
    x = (W - FACE_W) // 2
    y = (H - nh) // 2
    canvas.alpha_composite(scaled, (x, y))
    return canvas


def pick_n(frames: list[Path], n: int) -> list[Path]:
    if not frames:
        return []
    if len(frames) >= n:
        # 均匀取样
        return [frames[int(i * (len(frames) - 1) / max(1, n - 1))] for i in range(n)]
    # 循环补齐
    out = []
    i = 0
    while len(out) < n:
        out.append(frames[i % len(frames)])
        i += 1
    return out


COUNTS = {
    "idle": 4,
    "walk": 4,
    "drag": 2,
    "fall": 2,
    "land": 2,
    "turn": 3,
    "look": 4,
    "turn_back": 3,
    "lie_down": 3,
    "rest": 2,
    "rest_peek": 3,
    "sleep": 4,
    "get_up": 3,
}

META = {
    "facing": "left",
    "anchor": "center",
    "fullscreen": True,
    "fps": {
        "idle": 6,
        "walk": 8,
        "drag": 10,
        "fall": 10,
        "land": 8,
        "turn": 8,
        "look": 6,
        "turn_back": 8,
        "lie_down": 6,
        "rest": 2,
        "rest_peek": 6,
        "sleep": 3,
        "get_up": 8,
    },
    "loop": {
        "turn": False,
        "turn_back": False,
        "land": False,
        "lie_down": False,
        "rest_peek": False,
        "get_up": False,
    },
}


def main() -> None:
    emoji = find_emoji_root()
    print("emoji root:", emoji)
    # 缓存一份 raw 到仓库（可选，体积大则只烘焙结果）
    OUT.mkdir(parents=True, exist_ok=True)

    # 兜底脸
    fallback = list_frames(emoji, "眨眼", None)
    if not fallback:
        raise SystemExit("眨眼表情缺失")

    for state, n in COUNTS.items():
        name, sub = EMOJI_MAP.get(state, ("眨眼", None))
        frames = list_frames(emoji, name, sub)
        if not frames:
            frames = fallback
            print(f"  warn: {state} 缺 {name}/{sub}，用眨眼兜底")
        chosen = pick_n(frames, n)
        dest = OUT / state
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for i, fp in enumerate(chosen):
            face = Image.open(fp)
            compose(face).save(dest / f"{i}.png")
        print(f"  {state}: {n} frames from {name}")

    (OUT / "meta.json").write_text(json.dumps(META, indent=2, ensure_ascii=False), encoding="utf-8")
    # preview = idle0
    shutil.copy(OUT / "idle" / "0.png", OUT / "preview.png")

    # 更新 catalog：默认全屏小电视脸
    cat_path = ROOT / "assets" / "skins" / "catalog.json"
    catalog = {
        "skins": [
            {"id": "bilibili_face", "name": "小电视·全屏脸", "path": "skins/bilibili_face"},
            {"id": "minecraft_cat", "name": "Minecraft 猫", "path": "sprites"},
            {"id": "bilibili_tv", "name": "小电视·简绘", "path": "skins/bilibili_tv"},
        ]
    }
    cat_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    print("done ->", OUT)


if __name__ == "__main__":
    main()
