"""从 Minecraft 猫贴图 (64x32) 烘焙桌宠序列帧（伪 3D）。

用 scripts/cat3d.py 的盒体模型 + 软渲染器离线烘焙：
- 旧 5 态（idle/walk/drag/fall/land）在侧视角 yaw=90 烘焙；
- turn/look/turn_back：猫从侧面转身正对观察者（“看见你”）；
- lie_down/rest/rest_peek/sleep/get_up：疲劳趴下、抬头张望、
  睡觉（飘 Zzz）与起身，由状态机按体力系统串联。

输出：assets/sprites/<状态>/<序号>.png + meta.json + preview.png
用法：python scripts/build_cat_sprites.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from cat3d import make_boxes, render

ROOT = Path(__file__).resolve().parent.parent
TEXTURE = ROOT / "assets" / "textures" / "cat_calico.png"
OUT_DIR = ROOT / "assets" / "sprites"

SCALE = 20  # 最近邻放大倍数（4=原版 1 倍，20=5 倍）
CANVAS = (38, 28)  # 1x 画布
GROUND_ROW = 27    # 脚底所在行（1x）

PROFILE = 90.0  # 侧视 yaw（朝屏幕左）

# 尾巴姿态：两段的 (方向, 横向错位)。方向 U=竖直上/D=竖直下/B=水平向后，
# 错位为沿 z 的整数像素平移（横向摆动），全程轴对齐——8bit 像素风无倾斜。
TAILS = {
    "rest":   (("U", 0), ("U", 1)),    # 立起、尖略向后
    "swish1": (("U", 1), ("U", 2)),    # 向后甩
    "swish2": (("U", 0), ("U", -1)),   # 向前甩
    "down":   (("D", 0), ("D", 1)),    # 下垂（拖拽悬空 / 睡觉贴地，触地即停）
    "down2":  (("U", 0), ("B", 0)),    # 上折向后（趴下过渡，错位 0 保证 L 形衔接不悬空）
    "high":   (("U", -1), ("U", -1)),  # 前竖警觉（下落）
    "high2":  (("U", 0), ("U", 0)),    # 笔直向上
    "curl":   (("B", 0), ("B", 1)),    # 平铺贴地、尖卷向身侧
    "curl2":  (("B", 0), ("B", 0)),    # 平铺贴地
}

# 睡眠 Zzz 像素图案（4x4）
Z_PATTERN = ("1111", "0010", "0100", "1111")


def draw_zzz(img: Image.Image, phase: int, total: int) -> Image.Image:
    """在头部上方画三个渐大的 z，随帧相位上浮淡出（素材朝左，头在左侧）。"""
    d = ImageDraw.Draw(img)
    prog0 = phase / max(1, total)
    for k, (size_1x, off_1x) in enumerate(((0.9, 0.0), (1.2, 1.6), (1.6, 3.2))):
        px = max(2, round(SCALE * size_1x / 4))  # 单个像素块边长
        prog = (prog0 + k * 0.17) % 1.0
        gx = round((8.5 + off_1x * 0.9 + prog * 1.2) * SCALE)
        gy = round((15.5 - off_1x * 1.3 - prog * 1.5) * SCALE)
        alpha = max(70, int(255 * (1.0 - 0.5 * prog)))
        for ry, row in enumerate(Z_PATTERN):
            for rx, c in enumerate(row):
                if c == "1":
                    x0, y0 = gx + rx * px, gy + ry * px
                    d.rectangle((x0, y0, x0 + px - 1, y0 + px - 1),
                                fill=(255, 255, 255, alpha))
    return img


def gen_frames(tex: Image.Image) -> dict[str, list[Image.Image]]:
    def frame(yaw: float = PROFILE, tail: str = "rest", **kw) -> Image.Image:
        boxes = make_boxes(tail=TAILS[tail], **kw)
        return render(boxes, yaw, tex, SCALE, CANVAS, GROUND_ROW)

    frames: dict[str, list[Image.Image]] = {}
    # 待机：尾巴缓摆 + 头部轻微起伏
    frames["idle"] = [
        frame(tail="rest"),
        frame(tail="swish1", head_dy=-0.5),
        frame(tail="swish2"),
        frame(tail="swish1", head_dy=-0.5),
    ]
    # 走路：对角步态（前近+后远 一组），腿按像素前后迈步、过渡帧抬腿+身体上浮
    frames["walk"] = [
        frame(legs=((-2, 0), (2, 0), (2, 0), (-2, 0)), tail="swish1"),
        frame(legs=((0, 1), (0, 0), (0, 0), (0, 1)), body_dy=0.5, tail="rest"),
        frame(legs=((2, 0), (-2, 0), (-2, 0), (2, 0)), tail="swish1"),
        frame(legs=((0, 0), (0, 1), (0, 1), (0, 0)), body_dy=0.5, tail="rest"),
    ]
    # 拖拽：悬空，四腿前后张开挣扎（像素平移），尾巴下垂
    frames["drag"] = [
        frame(legs=((-1, 0), (-1, 0), (1, 0), (1, 0)), tail="down", head_dy=-0.5),
        frame(legs=((-2, 0), (-2, 0), (2, 0), (2, 0)), tail="down", head_dy=-0.5),
    ]
    # 下落：前腿前伸、后腿后蹬（像素平移张开），尾巴高竖
    frames["fall"] = [
        frame(legs=((-2, 1), (-2, 1), (2, 1), (2, 1)), tail="high"),
        frame(legs=((-3, 0), (-3, 0), (3, 0), (3, 0)), tail="high2"),
    ]
    # 落地：蹲伏压扁后恢复
    frames["land"] = [
        frame(squash=0.78, tail="swish1"),
        frame(tail="rest"),
    ]
    # 转身看你：侧面 -> 正脸（非循环，运行时停在末帧接 look）
    frames["turn"] = [frame(yaw=y) for y in (64, 38, 14)]
    # 注视：正对观察者，头部起伏 + 尾巴摆动 + 轻微晃动
    frames["look"] = [
        frame(yaw=0, tail="rest"),
        frame(yaw=4, tail="swish1", head_dy=-0.5),
        frame(yaw=0, tail="swish2"),
        frame(yaw=-4, tail="swish1", head_dy=-0.5),
    ]
    # 转回侧面（非循环）
    frames["turn_back"] = [frame(yaw=y) for y in (14, 38, 64)]

    # 趴下（非循环）：站立 -> 趴卧，头随之下沉
    frames["lie_down"] = [
        frame(lie=0.35, tail="down2"),
        frame(lie=0.7, head_dy=-0.5, tail="curl2"),
        frame(lie=1.0, head_dy=-1.0, tail="curl"),
    ]
    # 趴卧休息：缓慢呼吸起伏
    frames["rest"] = [
        frame(lie=1.0, head_dy=-1.0, tail="curl"),
        frame(lie=1.0, head_dy=-0.6, body_dy=0.4, tail="curl2"),
    ]
    # 趴卧抬头张望（非循环，停末帧）：鼠标经过时微微抬头
    frames["rest_peek"] = [
        frame(lie=1.0, head_dy=0.5, tail="curl2"),
        frame(lie=1.0, head_dy=2.0, tail="rest"),
        frame(lie=1.0, head_dy=3.0, tail="rest"),
    ]
    # 睡觉：头埋低贴地 + 呼吸起伏 + 飘 Zzz，尾巴放松下垂贴地
    sleep_frames = []
    for i in range(4):
        im = frame(
            lie=1.0,
            head_dy=-3.0 if i % 2 == 0 else -2.6,
            body_dy=0.0 if i % 2 == 0 else 0.4,
            tail="down",
        )
        sleep_frames.append(draw_zzz(im, i, 4))
    frames["sleep"] = sleep_frames
    # 起身（非循环）：趴卧 -> 站立
    frames["get_up"] = [
        frame(lie=1.0, head_dy=-1.0, tail="curl"),
        frame(lie=0.6, head_dy=-0.3, tail="down2"),
        frame(lie=0.0, tail="rest"),
    ]
    return frames


def write_output(frames: dict[str, list[Image.Image]]) -> None:
    for state, imgs in frames.items():
        d = OUT_DIR / state
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.png"):
            old.unlink()
        for i, im in enumerate(imgs):
            im.save(d / f"{i}.png")
    meta = {
        "facing": "left",  # 素材默认朝向，代码里向右移动时水平镜像
        "anchor": "bottom-center",
        "fps": {
            "idle": 4, "walk": 8, "drag": 10, "fall": 10, "land": 8,
            "turn": 8, "look": 4, "turn_back": 8,
            "lie_down": 8, "rest": 2, "rest_peek": 8, "sleep": 2, "get_up": 8,
        },
        # 非循环状态：播完停在末帧，由状态机接管切换
        "loop": {
            "turn": False, "turn_back": False, "land": False,
            "lie_down": False, "rest_peek": False, "get_up": False,
        },
    }
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # 拼接预览图（每行一个状态）供目检
    max_cols = max(len(v) for v in frames.values())
    fw, fh = CANVAS[0] * SCALE, CANVAS[1] * SCALE
    sheet = Image.new("RGBA", (max_cols * fw, len(frames) * fh), (90, 90, 110, 255))
    for row, (state, imgs) in enumerate(frames.items()):
        for col, im in enumerate(imgs):
            sheet.alpha_composite(im, (col * fw, row * fh))
    sheet.save(OUT_DIR / "preview.png")


def main() -> None:
    tex = Image.open(TEXTURE).convert("RGBA")
    if tex.size != (64, 32):
        raise SystemExit(f"贴图尺寸 {tex.size} 不是标准 64x32 MC 猫贴图")
    frames = gen_frames(tex)
    write_output(frames)
    total = sum(len(v) for v in frames.values())
    print(f"完成：{total} 帧已输出到 {OUT_DIR}（含 preview.png 预览图）")


if __name__ == "__main__":
    main()
