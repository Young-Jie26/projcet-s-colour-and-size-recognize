# -*- coding: utf-8 -*-
"""
双视角几何体识别（俯视 + 侧视）—— 唯一能把 6 种物体分开的方案
========================================================
为什么需要第二个视角：
    俯视时  球体 / 圆柱 / 圆锥 都是圆；三棱柱 / 正四面体 都是三角形，
    二维轮廓信息不够，任何算法都分不开。
    加上一个侧视（或 30~45° 斜视）后，组合是唯一的：

        ┌────────┬──────┬──────────┐
        │  物体   │ 俯视 │  侧视     │
        ├────────┼──────┼──────────┤
        │ 球体    │ 圆   │ 圆       │
        │ 圆柱    │ 圆   │ 长方形    │
        │ 圆锥    │ 圆   │ 三角形    │
        │ 五棱柱  │五边形│ 长方形    │
        │ 三棱柱  │三角形│ 长方形    │
        │ 正四面体│三角形│ 三角形    │
        └────────┴──────┴──────────┘

硬件怎么搭（任选其一）：
  1) 单相机 + 45° 平面镜：一帧画面里左半边是俯视、右半边是侧视（推荐，成本最低）
  2) 两个相机（一个正装、一个斜装），本脚本先按"同帧两个区域"实现，
     双相机可以改成两次取图，逻辑一样
  3) 相机斜装 30~45°：只剩一个视角，只能分球/柱/锥，三棱柱会和五棱柱混

用之前把下面 ROI_TOP / ROI_SIDE 改成你画面里两个视角的实际区域。
"""

from maix import app, camera, display, image
import json
import math
import time

# ------------------------------------------------------------------
CAM_W, CAM_H = 320, 240

# 画面里两个视角的区域 (x, y, w, h)
ROI_TOP = (0, 0, CAM_W // 2, CAM_H)                    # 左半：俯视
ROI_SIDE = (CAM_W // 2, 0, CAM_W - CAM_W // 2, CAM_H)  # 右半：侧视

COLORS = [
    ("red",    [[0, 80, 40, 80, 10, 80]],     image.COLOR_RED),
    ("green",  [[0, 80, -120, -10, 0, 30]],   image.COLOR_GREEN),
    ("blue",   [[0, 80, 10, 100, -128, -38],
                [45, 90, -45, 15, -60, -12]], image.COLOR_BLUE),
    ("yellow", [[0, 80, -20, 30, 30, 90]],    image.COLOR_YELLOW),
]

MIN_PIXELS = 80
TH_TRI = 0.66
TH_PENT_QUAD = 0.935

# 俯视轮廓 + 侧视轮廓 -> 几何体
TABLE = {
    ("circle", "circle"): "球体",
    ("circle", "rect"): "圆柱",
    ("circle", "triangle"): "圆锥",
    ("pentagon", "rect"): "五棱柱",
    ("triangle", "rect"): "三棱柱",
    ("triangle", "triangle"): "正四面体",
}

CALIB_PATH = "/root/blob_calib.json"
# ------------------------------------------------------------------


def load_mm_per_px():
    try:
        with open(CALIB_PATH) as f:
            d = json.load(f)
        mmpp = float(d.get("mm_per_px", 0))
        w = int(d.get("width", CAM_W) or CAM_W)
        if mmpp > 0 and w > 0:
            return mmpp * (float(w) / CAM_W)
    except Exception:
        pass
    return 0.0


def box_of(b):
    try:
        return int(b[0]), int(b[1]), int(b[2]), int(b[3])
    except Exception:
        pass
    try:
        return int(b.x()), int(b.y()), int(b.w()), int(b.h())
    except Exception:
        return int(b.x), int(b.y), int(b.w), int(b.h)


def pixels_of(b):
    try:
        return int(b[4])
    except Exception:
        pass
    try:
        return int(b.pixels())
    except Exception:
        x, y, w, h = box_of(b)
        return int(w * h)


def min_rect_of(b):
    try:
        cs = b.mini_corners()
        if cs is not None and len(cs) == 4:
            pts = [(float(p[0]), float(p[1])) for p in cs]
            sides = []
            for i in range(4):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % 4]
                sides.append(math.hypot(x2 - x1, y2 - y1))
            a = (sides[0] + sides[2]) / 2.0
            bb = (sides[1] + sides[3]) / 2.0
            if a > 0 and bb > 0:
                return a, bb, pts
    except Exception:
        pass
    x, y, w, h = box_of(b)
    return float(w), float(h), [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def classify(density, aspect):
    if density < TH_TRI:
        return "triangle"
    if density < 0.825:
        return "circle"
    if density < TH_PENT_QUAD:
        return "pentagon"
    return "square" if aspect < 1.2 else "rect"


def view_scan(img, roi, colors):
    """在指定 ROI 内，每种颜色取最大的那个色块"""
    rx, ry, rw, rh = roi
    out = {}
    for cname, th, _color in colors:
        blobs = img.find_blobs(th, area_threshold=MIN_PIXELS,
                               pixels_threshold=MIN_PIXELS)
        best = None
        for b in blobs:
            x, y, w, h = box_of(b)
            cx, cy = x + w / 2.0, y + h / 2.0
            if not (rx <= cx < rx + rw and ry <= cy < ry + rh):
                continue
            if best is None or w * h > best[0]:
                best = (w * h, b, x, y, w, h)
        if best is None:
            continue
        _, b, x, y, w, h = best
        px = pixels_of(b)
        a_px, b_px, _pts = min_rect_of(b)
        density = px / max(1.0, a_px * b_px)
        aspect = max(a_px, b_px) / max(1.0, min(a_px, b_px))
        out[cname] = {"shape": classify(density, aspect),
                      "density": density,
                      "box": (x, y, w, h)}
    return out


MM_PER_PX = load_mm_per_px()

cam = camera.Camera(CAM_W, CAM_H)
disp = display.Display()

print("=" * 62)
print(" 双视角几何体识别  %dx%d" % (CAM_W, CAM_H))
print(" 俯视区 %s   侧视区 %s" % (ROI_TOP, ROI_SIDE))
print("=" * 62)

t_last = 0.0
frame = 0

try:
    while not app.need_exit():
        img = cam.read()
        frame += 1

        top = view_scan(img, ROI_TOP, COLORS)
        side = view_scan(img, ROI_SIDE, COLORS)

        # 画两个视角的分界线
        img.draw_rect(*ROI_TOP, color=image.COLOR_WHITE)
        img.draw_rect(*ROI_SIDE, color=image.COLOR_WHITE)
        img.draw_string(ROI_TOP[0] + 2, ROI_TOP[1] + 2, "TOP", color=image.COLOR_WHITE)
        img.draw_string(ROI_SIDE[0] + 2, ROI_SIDE[1] + 2, "SIDE", color=image.COLOR_WHITE)

        results = []
        for cname, _th, _c in COLORS:
            if cname not in top:
                continue
            # 侧视里同颜色的块；没有就用"没看到"处理
            s = side.get(cname)
            top_shape = top[cname]["shape"]
            if s is None:
                side_shape_name = "-"
                solid = "侧视未检测到"
            else:
                side_shape_name = s["shape"]
                solid = TABLE.get((top_shape, side_shape_name), "组合不支持")

            x, y, w, h = top[cname]["box"]
            img.draw_rect(x, y, w, h, color=image.COLOR_YELLOW)
            img.draw_string(x, y - 16 if y > 16 else y + 2,
                            "%s %s+%s" % (cname, top_shape[:3], side_shape_name[:3]),
                            color=image.COLOR_YELLOW)
            results.append((cname, top_shape, side_shape_name, solid,
                            top[cname]["density"],
                            0 if s is None else s["density"]))

        if results:
            img.draw_string(4, CAM_H - 20, "found %d" % len(results),
                            color=image.COLOR_GREEN)

        disp.show(img)

        now = time.time()
        if now - t_last >= 1.0:
            t_last = now
            if not results:
                print("[frame %5d] 两个视角都没有检测到色块" % frame)
            for r in results:
                print("[frame %5d] %-6s 俯视=%-8s 侧视=%-8s -> %s   "
                      "(density %.2f / %.2f)" % (frame, r[0], r[1], r[2], r[3], r[4], r[5]))

except KeyboardInterrupt:
    print("\n[INFO] 用户中断")
except Exception as e:
    print("[ERROR] %s: %s" % (type(e).__name__, e))
    import traceback
    traceback.print_exc()
finally:
    del cam
    print("\n结束，共处理 %d 帧" % frame)
