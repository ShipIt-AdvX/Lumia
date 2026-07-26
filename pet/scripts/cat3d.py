"""MC 猫 3D 盒体模型 + 微型软渲染器（离线烘焙用，运行时不依赖本模块）。

坐标系：y 向上（地面 y=0），猫头朝 -z，尾巴朝 +z，x 为左右（+x 近侧）。
相机：沿 +z 方向的水平正交投影，因此盒体的顶/底面永远不可见，
每个盒体只需给出 4 个竖直面的贴图切片。
yaw=0 为猫正对观察者（"转身看你"的正脸），yaw=90 为朝屏幕左的侧面。

渲染：角点做部件俯仰(pitch) + 全局偏航(yaw)变换后正交投影，背面剔除，
画家算法按深度排序，每个面用仿射变换把贴图切片映射为平行四边形，
最近邻采样保持 MC 大像素质感；按法线朝向做简易明暗。
"""

from __future__ import annotations

import math

from PIL import Image, ImageEnhance

# 每面 4 角的顶点索引（顶点编码 bit0:x+ bit1:y+ bit2:z+），顺序 TL,TR,BR,BL（从盒外看）
FACE_IDX = {
    "-z": (2, 3, 1, 0),
    "+z": (7, 6, 4, 5),
    "+x": (7, 3, 1, 5),
    "-x": (2, 6, 4, 0),
}
_ROT_OP = {
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}

# --- MC 猫贴图 (64x32) 各部件竖直面的 UV 切片：dir -> (crop, 预旋转角度) ---
# 盒体 UV 布局: box(w,h,d)@(u,v) -> 右(u,v+d) 前(u+d,v+d) 左(u+d+w,v+d) 后(u+2d+w,v+d)
HEAD_FACES = {
    "-z": ((5, 5, 10, 9), 0),      # 正脸
    "+x": ((10, 5, 15, 9), 0),     # 左侧脸（旧侧视图同款）
    "-x": ((0, 5, 5, 9), 0),
    "+z": ((15, 5, 20, 9), 0),
}
NOSE_FACES = {
    "-z": ((2, 26, 5, 28), 0),
    "+x": ((0, 26, 2, 28), 0),
    "-x": ((5, 26, 7, 28), 0),
    "+z": None,  # 埋在头里
}
EAR_L_FACES = {  # ear1 @(0,10)
    "-z": ((2, 12, 3, 13), 0),
    "+x": ((0, 12, 2, 13), 0),
    "-x": ((3, 12, 5, 13), 0),
    "+z": ((5, 12, 6, 13), 0),
}
EAR_R_FACES = {  # ear2 @(6,10)
    "-z": ((8, 12, 9, 13), 0),
    "+x": ((6, 12, 8, 13), 0),
    "-x": ((9, 12, 11, 13), 0),
    "+z": ((11, 12, 12, 13), 0),
}
BODY_FACES = {  # box(4,16,6)@(20,0)，模型里绕 x 转 90° 平躺，故侧面切片需旋转
    "-z": ((26, 0, 30, 6), 0),     # 胸口（UV 顶面）
    "+z": ((30, 0, 34, 6), 0),     # 尾端（UV 底面）
    "+x": ((20, 6, 26, 22), 270),  # 左侧身（旧侧视图同款旋转）
    "-x": ((30, 6, 36, 22), 90),
}
FRONT_LEG_FACES = {  # box(2,10,2)@(40,0)，只取下段 7px
    "-z": ((42, 5, 44, 12), 0),
    "+x": ((40, 5, 42, 12), 0),
    "-x": ((44, 5, 46, 12), 0),
    "+z": ((46, 5, 48, 12), 0),
}
BACK_LEG_FACES = {  # box(2,6,2)@(8,13)
    "-z": ((10, 15, 12, 21), 0),
    "+x": ((8, 15, 10, 21), 0),
    "-x": ((12, 15, 14, 21), 0),
    "+z": ((14, 15, 16, 21), 0),
}
TAIL1_FACES = {  # tail1 box(1,8,1)@(0,15) 上半段
    "-z": ((1, 16, 2, 20), 0),
    "+x": ((0, 16, 1, 20), 0),
    "-x": ((2, 16, 3, 20), 0),
    "+z": ((3, 16, 4, 20), 0),
}
TAIL2_FACES = {  # tail1 下半段
    "-z": ((1, 20, 2, 24), 0),
    "+x": ((0, 20, 1, 24), 0),
    "-x": ((2, 20, 3, 24), 0),
    "+z": ((3, 20, 4, 24), 0),
}


class Box:
    """一个盒体部件：世界坐标角点 + 各竖直面的贴图切片。"""

    def __init__(self, center: tuple, size: tuple, faces: dict, dim: bool = False):
        cx, cy, cz = center
        hx, hy, hz = size[0] / 2, size[1] / 2, size[2] / 2
        self.pts = [
            [cx + (hx if i & 1 else -hx),
             cy + (hy if i & 2 else -hy),
             cz + (hz if i & 4 else -hz)]
            for i in range(8)
        ]
        self.faces = faces
        self.dim = dim  # 远侧部件：侧视时压暗，正面视角不压

    def pitch(self, deg: float, pivot: tuple) -> "Box":
        """绕过 pivot 的 x 轴旋转（腿摆/尾摆）。正角度使腿的下端向 -z（前方）。"""
        a = math.radians(deg)
        _, py, pz = pivot
        for p in self.pts:
            y, z = p[1] - py, p[2] - pz
            p[1] = py + y * math.cos(a) - z * math.sin(a)
            p[2] = pz + y * math.sin(a) + z * math.cos(a)
        return self

    def translate(self, dx: float = 0, dy: float = 0, dz: float = 0) -> "Box":
        for p in self.pts:
            p[0] += dx
            p[1] += dy
            p[2] += dz
        return self

    def scale_y(self, f: float) -> "Box":
        """以地面 y=0 为基准纵向缩放（落地压扁）。"""
        for p in self.pts:
            p[1] *= f
        return self


def _tail_boxes(base_y: float, base_z: float, segs: tuple) -> list["Box"]:
    """按轴对齐分段组装尾巴（8bit 像素风，横向错位摆动而非旋转，无倾斜）。

    segs: 两段的 (方向, 横向错位) 序列；方向 U=竖直上 / D=竖直下 / B=水平向后(+z)，
    错位为该关节垂直于段轴的整数像素平移（U/D 沿 z，B 沿 y），即横向摆动。
    """
    boxes: list[Box] = []
    y, z = base_y, base_z
    faces = (TAIL1_FACES, TAIL2_FACES)
    for i, (orient, sway) in enumerate(segs):
        f = faces[min(i, len(faces) - 1)]
        if orient == "U":
            z += sway
            boxes.append(Box((0, y + 2, z), (1, 4, 1), f))
            y += 4
        elif orient == "D":
            z += sway
            y = max(0.0, y - 4)  # 触地即停，尾巴不穿地板（趴卧时下垂贴地）
            boxes.append(Box((0, y + 2, z), (1, 4, 1), f))
        else:  # "B"：水平向后，错位改为上下方向（避免沿段轴拉出缝隙）
            y += sway
            boxes.append(Box((0, y, z + 2), (1, 1, 4), f))
            z += 4
    return boxes


def make_boxes(
    head_dy: float = 0.0,
    body_dy: float = 0.0,
    tail: tuple = (("U", 0), ("U", 1)),
    legs: tuple = ((0, 0), (0, 0), (0, 0), (0, 0)),
    squash: float = 1.0,
    lie: float = 0.0,
) -> list[Box]:
    """按姿态参数组装整猫的盒体列表（8bit 像素风：肢体只做轴对齐平移，不旋转）。

    tail: 两段尾巴的 (方向, 横向错位) 序列，方向 U=竖直上/D=竖直下/B=水平向后，
          错位为该关节沿 z 的整数像素平移（横向摆动），全程轴对齐无倾斜。
    legs: 四条腿的 (dz, dy) 像素平移，顺序 (前近, 前远, 后近, 后远)；
          dz 负值向前（-z）迈步、正值后撤，dy 为抬离地面的像素（均保持竖直）。
    squash: 纵向压缩系数（落地帧 <1）
    lie: 趴卧程度 0..1（0=站立，1=完全趴下）；>0 时身体/头部下沉，
         半程时腿保持竖直呈蹲伏，=1 时腿 90° 平铺贴地并顶住身底。
    """
    boxes: list[Box] = []
    bd = body_dy
    sink = 3.5 * lie  # 趴卧时身体/头部下沉像素（身底压到平铺腿上，呼吸帧也不脱开）

    # 躯干（站立 y 5..11；趴卧随 sink 下沉贴地）
    boxes.append(Box((0, 8 + bd - sink, 0), (4, 6, 16), BODY_FACES))

    # 头部组（头/吻/双耳）
    hy = 11 + bd + head_dy - sink
    boxes.append(Box((0, hy, -10.5), (5, 4, 5), HEAD_FACES))
    boxes.append(Box((0, hy - 1, -14), (3, 2, 2), NOSE_FACES))
    boxes.append(Box((1.5, hy + 2.5, -9), (1, 1, 2), EAR_L_FACES))
    boxes.append(Box((-1.5, hy + 2.5, -9), (1, 1, 2), EAR_R_FACES, dim=True))

    if lie >= 1.0:
        # 完全趴卧：腿 90° 平铺贴地（轴对齐，无倾斜），前腿前伸、后腿藏于身下
        for x, z, h, faces, dim in (
            (1.0, -6, 7, FRONT_LEG_FACES, False),
            (-1.0, -6, 7, FRONT_LEG_FACES, True),
            (1.0, 6, 6, BACK_LEG_FACES, False),
            (-1.0, 6, 6, BACK_LEG_FACES, True),
        ):
            leg = Box((x, h / 2, z), (2, h, 2), faces, dim=dim)
            leg.pitch(-90, (x, 0, z))  # 90° 为整数直角，投影仍是轴对齐矩形
            leg.translate(dy=1)  # 平铺后盒体跨 -1..1，抬到 0..2：贴地且顶住身底，不悬浮
            boxes.append(leg)
    else:
        # 站立/蹲伏四腿：竖直盒体，只做像素平移（dz 前后迈步 / dy 抬腿），绝不旋转；
        # 趴下/起身过渡（0<lie<1）身体下沉罩住竖腿，呈蹲伏姿不留悬空缝
        fn, ff, bn, bf = legs
        for (dz, dy), x, z, h, faces, dim in (
            (fn, 1.0, -6, 7, FRONT_LEG_FACES, False),
            (ff, -1.0, -6, 7, FRONT_LEG_FACES, True),
            (bn, 1.0, 6, 6, BACK_LEG_FACES, False),
            (bf, -1.0, 6, 6, BACK_LEG_FACES, True),
        ):
            leg = Box((x, h / 2, z), (2, h, 2), faces, dim=dim)
            if dz or dy:
                leg.translate(dy=dy, dz=dz)
            boxes.append(leg)

    # 尾巴：轴对齐分段（8bit 像素风，横向错位摆动而非旋转，无倾斜）
    p1y, p1z = 10.5 + bd - sink, 7.5
    boxes.extend(_tail_boxes(p1y, p1z, tail))

    if squash != 1.0:
        for b in boxes:
            b.scale_y(squash)
    return boxes


def render(
    boxes: list[Box],
    yaw_deg: float,
    tex: Image.Image,
    scale: int,
    canvas_1x: tuple = (38, 28),
    ground_row: int = 27,
) -> Image.Image:
    """渲染一帧：全局 yaw -> 投影 -> 剔除 -> 排序 -> 逐面仿射贴图。"""
    th = math.radians(yaw_deg)
    sin_t, cos_t = math.sin(th), math.cos(th)
    W, H = canvas_1x[0] * scale, canvas_1x[1] * scale
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ox, oy = canvas_1x[0] / 2, float(ground_row)
    dim_f = 1.0 - 0.35 * abs(sin_t)  # 远侧压暗随视角平滑过渡

    quads = []
    for box in boxes:
        tp = [
            (x * cos_t + z * sin_t, y, -x * sin_t + z * cos_t)
            for x, y, z in box.pts
        ]
        for dirn, spec in box.faces.items():
            if spec is None:
                continue
            i0, i1, i2, i3 = FACE_IDX[dirn]
            c0, c1, c2, c3 = tp[i0], tp[i1], tp[i2], tp[i3]
            ux, uy, uz = c1[0] - c0[0], c1[1] - c0[1], c1[2] - c0[2]
            vx, vy, vz = c3[0] - c0[0], c3[1] - c0[1], c3[2] - c0[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            nlen = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            if nz / nlen > -1e-6:  # 背向或平行于相机
                continue
            depth = (c0[2] + c1[2] + c2[2] + c3[2]) / 4
            bright = 0.7 + 0.3 * (-nz / nlen)
            if box.dim:
                bright *= dim_f
            quads.append((depth, bright, dirn, spec, (c0, c1, c3)))

    quads.sort(key=lambda q: -q[0])  # 画家算法：先画远的
    for _, bright, dirn, (crop_box, rot), (c0, c1, c3) in quads:
        face = tex.crop(crop_box)
        if rot:
            face = face.transpose(_ROT_OP[rot])
        if dirn in ("+x", "-x"):
            # 侧面角点顺序使贴图 u 轴与屏幕 x 反向，预翻转抵消
            face = face.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if bright < 0.999:
            a = face.getchannel("A")
            face = ImageEnhance.Brightness(face.convert("RGB")).enhance(bright).convert("RGBA")
            face.putalpha(a)

        w, h = face.size
        p0 = ((c0[0] + ox) * scale, (oy - c0[1]) * scale)
        p1 = ((c1[0] + ox) * scale, (oy - c1[1]) * scale)
        p3 = ((c3[0] + ox) * scale, (oy - c3[1]) * scale)
        # 正向仿射 tex(u,v) -> p0 + u/w*(p1-p0) + v/h*(p3-p0)，求逆给 PIL
        m11, m21 = (p1[0] - p0[0]) / w, (p1[1] - p0[1]) / w
        m12, m22 = (p3[0] - p0[0]) / h, (p3[1] - p0[1]) / h
        det = m11 * m22 - m12 * m21
        if abs(det) < 1e-9:
            continue
        i11, i12, i21, i22 = m22 / det, -m12 / det, -m21 / det, m11 / det
        coeffs = (
            i11, i12, -(i11 * p0[0] + i12 * p0[1]),
            i21, i22, -(i21 * p0[0] + i22 * p0[1]),
        )
        layer = face.transform((W, H), Image.Transform.AFFINE, coeffs, Image.Resampling.NEAREST)
        canvas.alpha_composite(layer)
    return canvas
