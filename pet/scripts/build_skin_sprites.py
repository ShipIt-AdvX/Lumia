#!/usr/bin/env python3
"""生成桌宠皮肤精灵帧：bilibili 小电视简绘。

用法：
    python scripts/build_skin_sprites.py
输出：
    assets/skins/<skin_id>/{idle,walk,...}/*.png + meta.json
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "skins"

# 与现有猫皮肤同一套状态目录
STATES = {
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
    "anchor": "bottom-center",
    "fps": {
        "idle": 4,
        "walk": 8,
        "drag": 10,
        "fall": 10,
        "land": 8,
        "turn": 8,
        "look": 4,
        "turn_back": 8,
        "lie_down": 8,
        "rest": 2,
        "rest_peek": 8,
        "sleep": 2,
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

SIZE = 128
CX, CY = SIZE // 2, SIZE // 2 + 4


def _new() -> Image.Image:
    return Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))


def _rr(draw: ImageDraw.ImageDraw, box, fill, r=18):
    draw.rounded_rectangle(box, radius=r, fill=fill)


# ---------- Bilibili 小电视 ----------


def draw_tv(
    img: Image.Image,
    *,
    face: str = "uwu",
    tilt: float = 0.0,
    bounce: int = 0,
    foot_phase: float = 0.0,
    color: tuple[int, int, int] = (0, 174, 236),
) -> None:
    """按参考图：蓝框 + 天线 + 小脚 + 屏里的脸。"""
    body = _new()
    d = ImageDraw.Draw(body)
    blue = color
    white = (255, 255, 255, 255)
    y0 = 18 + bounce

    # 天线
    d.line((40, y0 + 8, 28, y0 - 6), fill=blue + (255,), width=7)
    d.line((88, y0 + 8, 100, y0 - 6), fill=blue + (255,), width=7)
    d.ellipse((24, y0 - 12, 34, y0 - 2), fill=blue + (255,))
    d.ellipse((94, y0 - 12, 104, y0 - 2), fill=blue + (255,))

    # 机身外框
    _rr(d, (22, y0 + 10, 106, y0 + 88), blue + (255,), r=22)
    # 屏幕
    _rr(d, (32, y0 + 22, 96, y0 + 74), white, r=14)

    # 脚（走路时交替抬起）
    fl = 4 + int(3 * math.sin(foot_phase))
    fr = 4 + int(3 * math.sin(foot_phase + math.pi))
    d.ellipse((40, y0 + 84 + fl, 58, y0 + 100 + fl), fill=blue + (255,))
    d.ellipse((70, y0 + 84 + fr, 88, y0 + 100 + fr), fill=blue + (255,))

    # 脸（对齐参考：斜眯眼矩形 + w 嘴）
    fy0 = y0 + 22
    eye_y = fy0 + 16
    if face == "uwu":
        # 参考图 ˘ ˘：外缘偏低、靠中心偏高
        d.polygon(
            [(40, eye_y + 8), (58, eye_y + 2), (58, eye_y + 11), (40, eye_y + 17)],
            fill=blue + (255,),
        )
        d.polygon(
            [(70, eye_y + 2), (88, eye_y + 8), (88, eye_y + 17), (70, eye_y + 11)],
            fill=blue + (255,),
        )
        my = fy0 + 40
        d.line([(46, my), (55, my + 9), (64, my + 1), (73, my + 9), (82, my)], fill=blue + (255,), width=5)
    elif face == "sleep":
        d.arc((40, eye_y, 58, eye_y + 14), 200, 340, fill=blue + (255,), width=4)
        d.arc((70, eye_y, 88, eye_y + 14), 200, 340, fill=blue + (255,), width=4)
        d.line([(54, fy0 + 42), (74, fy0 + 42)], fill=blue + (255,), width=3)
        # z
        zx, zy = 92, y0 + 8
        d.line([(zx, zy), (zx + 12, zy), (zx, zy + 12), (zx + 12, zy + 12)], fill=blue + (200,), width=2)
    elif face == "angry":
        d.line([(42, eye_y), (58, eye_y + 8)], fill=blue + (255,), width=5)
        d.line([(86, eye_y), (70, eye_y + 8)], fill=blue + (255,), width=5)
        d.arc((52, fy0 + 40, 76, fy0 + 54), 20, 160, fill=blue + (255,), width=4)
    elif face == "eat":
        d.ellipse((44, eye_y, 56, eye_y + 12), fill=blue + (255,))
        d.ellipse((72, eye_y, 84, eye_y + 12), fill=blue + (255,))
        d.ellipse((48, eye_y + 2, 52, eye_y + 6), fill=white)
        d.ellipse((76, eye_y + 2, 80, eye_y + 6), fill=white)
        # 张嘴
        d.ellipse((54, fy0 + 36, 74, fy0 + 52), fill=blue + (255,))
        d.ellipse((58, fy0 + 40, 70, fy0 + 48), fill=(255, 180, 180, 255))
    elif face == "look":
        d.ellipse((44, eye_y - 2, 58, eye_y + 14), outline=blue + (255,), width=4)
        d.ellipse((70, eye_y - 2, 84, eye_y + 14), outline=blue + (255,), width=4)
        d.ellipse((50, eye_y + 4, 56, eye_y + 10), fill=blue + (255,))
        d.ellipse((76, eye_y + 4, 82, eye_y + 10), fill=blue + (255,))
        d.arc((52, fy0 + 40, 76, fy0 + 52), 200, 340, fill=blue + (255,), width=3)
    else:  # neutral
        d.ellipse((44, eye_y, 56, eye_y + 12), fill=blue + (255,))
        d.ellipse((72, eye_y, 84, eye_y + 12), fill=blue + (255,))
        d.arc((52, fy0 + 40, 76, fy0 + 52), 200, 340, fill=blue + (255,), width=3)

    if abs(tilt) > 0.01:
        body = body.rotate(tilt, resample=Image.Resampling.BICUBIC, center=(CX, CY))
    img.alpha_composite(body)


def face_for_state(state: str, i: int, n: int) -> tuple[str, float, int, float]:
    """返回 face, tilt, bounce, foot_phase。"""
    t = i / max(1, n)
    if state == "sleep":
        return "sleep", 0.0, 0, 0.0
    if state in ("look",) and i >= n // 2:
        return "angry", 0.0, 0, 0.0
    if state == "look":
        return "look", 0.0, 0, 0.0
    if state in ("rest", "rest_peek", "lie_down"):
        return ("look" if state == "rest_peek" else "sleep"), 0.0, 0, 0.0
    if state in ("walk", "turn", "turn_back"):
        tilt = 8 * math.sin(t * math.pi * 2)
        return "uwu", tilt, 0, t * math.pi * 2
    if state == "drag":
        return "look", -12, -4, 0.0
    if state == "fall":
        return "look", 15 * (1 if i % 2 == 0 else -1), 0, 0.0
    if state in ("land", "get_up"):
        return "uwu", 0.0, 2 * (1 - i), 0.0
    # idle / meal atmosphere via look frames from director uses LOOK
    bounce = int(2 * math.sin(t * math.pi * 2))
    return "uwu", 0.0, bounce, 0.0


def build_tv_skin(skin_id: str, color: tuple[int, int, int], *, meal_face: bool = True) -> None:
    root = OUT / skin_id
    for state, n in STATES.items():
        d = root / state
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            img = _new()
            face, tilt, bounce, phase = face_for_state(state, i, n)
            # 导演 meal 用 look 状态；吃东西时把 look 中性帧换成 eat
            if meal_face and state == "look" and i < 2:
                face = "eat"
            draw_tv(img, face=face, tilt=tilt, bounce=bounce, foot_phase=phase, color=color)
            img.save(d / f"{i}.png")
    (root / "meta.json").write_text(json.dumps(META, indent=2), encoding="utf-8")
    # preview
    prev = _new()
    draw_tv(prev, face="uwu", color=color)
    prev.save(root / "preview.png")
    print(f"built {skin_id} -> {root}")


# ---------- 22 / 33 娘（简化 chibi，蓝/粉配色） ----------


def draw_niao(
    img: Image.Image,
    *,
    hair: tuple[int, int, int],
    accent: tuple[int, int, int],
    face: str = "smile",
    tilt: float = 0.0,
    bounce: int = 0,
    step: float = 0.0,
) -> None:
    body = _new()
    d = ImageDraw.Draw(body)
    y = 10 + bounce
    skin = (255, 230, 220, 255)
    # 头发
    d.ellipse((28, y + 8, 100, y + 78), fill=hair + (255,))
    # 刘海
    d.ellipse((34, y + 14, 94, y + 48), fill=hair + (255,))
    # 脸
    d.ellipse((40, y + 28, 88, y + 78), fill=skin)
    # 侧发
    d.ellipse((24, y + 40, 44, y + 90), fill=hair + (255,))
    d.ellipse((84, y + 40, 104, y + 90), fill=hair + (255,))
    # 眼睛 / 嘴
    ey = y + 48
    if face == "sleep":
        d.arc((48, ey, 60, ey + 10), 200, 340, fill=(80, 60, 60, 255), width=3)
        d.arc((68, ey, 80, ey + 10), 200, 340, fill=(80, 60, 60, 255), width=3)
    elif face == "angry":
        d.line([(48, ey), (58, ey + 6)], fill=(80, 40, 40, 255), width=3)
        d.line([(80, ey), (70, ey + 6)], fill=(80, 40, 40, 255), width=3)
        d.arc((56, y + 62, 72, y + 72), 10, 170, fill=(80, 40, 40, 255), width=2)
    elif face == "eat":
        d.ellipse((50, ey, 58, ey + 10), fill=(60, 40, 40, 255))
        d.ellipse((70, ey, 78, ey + 10), fill=(60, 40, 40, 255))
        d.ellipse((56, y + 60, 72, y + 72), fill=(220, 80, 100, 255))
    else:
        d.ellipse((50, ey, 58, ey + 12), fill=(60, 40, 40, 255))
        d.ellipse((70, ey, 78, ey + 12), fill=(60, 40, 40, 255))
        d.ellipse((52, ey + 2, 55, ey + 5), fill=(255, 255, 255, 255))
        d.ellipse((72, ey + 2, 75, ey + 5), fill=(255, 255, 255, 255))
        d.arc((56, y + 60, 72, y + 70), 200, 340, fill=(200, 80, 100, 255), width=2)
    # 腮红
    d.ellipse((44, y + 58, 52, y + 64), fill=(255, 160, 160, 120))
    d.ellipse((76, y + 58, 84, y + 64), fill=(255, 160, 160, 120))
    # 身子（小裙子）
    d.polygon([(48, y + 78), (80, y + 78), (88, y + 108), (40, y + 108)], fill=accent + (255,))
    # 腿
    lx = int(4 * math.sin(step))
    d.rectangle((52 + lx, y + 108, 58 + lx, y + 122), fill=skin)
    d.rectangle((70 - lx, y + 108, 76 - lx, y + 122), fill=skin)
    # 发饰小电视角标色块
    d.rounded_rectangle((56, y + 6, 72, y + 18), radius=3, fill=accent + (255,))

    if abs(tilt) > 0.01:
        body = body.rotate(tilt, resample=Image.Resampling.BICUBIC, center=(CX, CY))
    img.alpha_composite(body)


def build_niao_skin(skin_id: str, hair: tuple[int, int, int], accent: tuple[int, int, int]) -> None:
    root = OUT / skin_id
    for state, n in STATES.items():
        d = root / state
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            img = _new()
            t = i / max(1, n)
            face, tilt, bounce, step = "smile", 0.0, 0, 0.0
            if state == "sleep":
                face = "sleep"
            elif state == "look" and i >= 2:
                face = "angry"
            elif state == "look":
                face = "eat"
            elif state in ("rest", "lie_down"):
                face = "sleep"
            elif state == "rest_peek":
                face = "smile"
            elif state in ("walk", "turn", "turn_back"):
                tilt = 6 * math.sin(t * math.pi * 2)
                step = t * math.pi * 2
            elif state == "drag":
                tilt, bounce = -10, -3
            elif state == "idle":
                bounce = int(2 * math.sin(t * math.pi * 2))
            draw_niao(img, hair=hair, accent=accent, face=face, tilt=tilt, bounce=bounce, step=step)
            img.save(d / f"{i}.png")
    (root / "meta.json").write_text(json.dumps(META, indent=2), encoding="utf-8")
    prev = _new()
    draw_niao(prev, hair=hair, accent=accent, face="smile")
    prev.save(root / "preview.png")
    print(f"built {skin_id} -> {root}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # 产品主外形：B 站小电视（参考图配色）
    build_tv_skin("bilibili_tv", (0, 174, 236))
    # 备用：小电视粉变体
    build_tv_skin("bilibili_tv_pink", (255, 105, 180))
    # 皮肤清单（不再生成 2233）
    catalog = {
        "skins": [
            {"id": "bilibili_face", "name": "小电视·全屏脸", "path": "skins/bilibili_face"},
            {"id": "minecraft_cat", "name": "Minecraft 猫", "path": "sprites"},
            {"id": "bilibili_tv", "name": "小电视·简绘", "path": "skins/bilibili_tv"},
        ]
    }
    (OUT / "catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    print("catalog ->", OUT / "catalog.json")


if __name__ == "__main__":
    main()
