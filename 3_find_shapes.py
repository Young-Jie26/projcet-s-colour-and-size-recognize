# -*- coding: utf-8 -*-
"""
颜色块 + 形状识别（MaixCAM / MaixCAM2）
========================================================
在 2_find_blocks.py 的基础上，增加“形状识别”：
    triangle 三角形 / circle 圆形 / square 正方形 / rect 长方形

原理（关键，决定了它为什么稳）：
  取色块的【最小外接旋转矩形】(mini_corners)，算填充率
        density = 色块像素数 / 最小外接矩形面积
  数学上：
        任意三角形   -> 最小外接矩形面积 = 2 × 三角形面积  =>  density ≈ 0.50
        圆形         -> 矩形 4r²，圆 πr²                   =>  density ≈ 0.785
        正方形/长方形 -> 矩形就是它自己                     =>  density ≈ 1.00
  这三个值相差很大，而且【与旋转角度无关】（斜着放也不影响），
  所以比用外接框的宽高比、或者数角点都更靠谱。
  再用最小外接矩形的边长比区分 正方形 vs 长方形。

前置条件：先跑 1_calibrate_mm.py 完成 mm/像素 标定。
"""

from maix import app, camera, display, image
import json
import os
import math
import time

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
CAM_W, CAM_H = 320, 240

# (名字, LAB阈值, 画框颜色)
COLORS = [
    ("red",    [[0, 80, 40, 80, 10, 80]],     image.COLOR_RED),
    ("green",  [[0, 80, -120, -10, 0, 30]],   image.COLOR_GREEN),
    # 蓝色拆两段：深蓝系(a 正、b 很负) + 浅蓝系(a 负、b 中等负)
    # 只写一段 [0,80,30,100,-120,-60] 只能识别“纯蓝”，宝蓝/藏青/天蓝都会漏
    ("blue",   [[0, 80, 10, 100, -128, -38],     # 纯蓝/宝蓝/藏青
                [45, 90, -45, 15, -60, -12]],    # 天蓝/湖蓝
               image.COLOR_BLUE),
    ("yellow", [[0, 80, -20, 30, 30, 90]],    image.COLOR_YELLOW),
]

MIN_PIXELS = 150                 # 噪点过滤

SIZE_MAX_MM = 40.0               # 40mm 见方
SIZE_MIN_MM = 8.0
SIZE_TOL = 1.10                  # 允许 10% 误差

# ---- 形状判定阈值（填充率）----
TH_TRI = 0.64                    # < 0.64 -> 三角形（理论 0.50）
TH_QUAD = 0.89                   # > 0.89 -> 四边形（理论 1.00）
# 0.64 ~ 0.89 之间 -> 圆形（理论 0.785）

SQ_ASPECT = 1.20                 # 四边形里，长宽比 < 1.2 算正方形，否则长方形

DRAW_POLY = True                 # 画最小外接矩形
SHAPE_DEBUG = True               # 终端打印密度/长宽比，方便现场调阈值
PRINT_EVERY = 1.0

CALIB_PATH = "/root/blob_calib.json"

SHAPE_CN = {"triangle": "三角形", "circle": "圆形",
            "square": "正方形", "rect": "长方形", "unknown": "未知"}

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
    """返回最小外接矩形的 (边长a, 边长b, 4个角点) ；拿不到就用 bbox 兜底"""
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
    """按填充率判形状，返回 (名字, 置信度0~1)"""
    if density < TH_TRI:
        ideal, name = 0.50, "triangle"
    elif density > TH_QUAD:
        ideal, name = 1.00, "square" if aspect < SQ_ASPECT else "rect"
    else:
        ideal, name = 0.785, "circle"
    conf = max(0.0, 1.0 - abs(density - ideal) / 0.25)
    return name, conf


MM_PER_PX = load_mm_per_px()

cam = camera.Camera(CAM_W, CAM_H)
disp = display.Display()

print("=" * 60)
print(" 颜色块 + 形状识别   分辨率 %dx%d" % (CAM_W, CAM_H))
if MM_PER_PX > 0:
    print(" 标定: %.4f mm/px   -> 40mm 约合 %d px" % (MM_PER_PX, int(40 / MM_PER_PX)))
else:
    print(" [WARN] 未标定，先跑 1_calibrate_mm.py，否则只能看像素")
print("=" * 60)

t_last = 0.0
frame = 0

try:
    while not app.need_exit():
        img = cam.read()
        frame += 1

        found = []
        total = 0

        for cname, th, draw_color in COLORS:
            blobs = img.find_blobs(th, area_threshold=MIN_PIXELS,
                                   pixels_threshold=MIN_PIXELS)
            for b in blobs:
                total += 1
                x, y, w, h = box_of(b)
                px = pixels_of(b)
                a_px, b_px, pts = min_rect_of(b)

                # 最小外接矩形面积（像素）
                rect_area = max(1.0, a_px * b_px)
                density = px / rect_area
                aspect = max(a_px, b_px) / max(1.0, min(a_px, b_px))

                shape, conf = classify(density, aspect)

                # ---- 尺寸换算 ----
                if MM_PER_PX > 0:
                    # 用最小外接矩形的两条边，斜放也不会算大
                    side_a = a_px * MM_PER_PX
                    side_b = b_px * MM_PER_PX
                    side_long = max(side_a, side_b)
                    side_short = min(side_a, side_b)
                    # “能放进 40mm 见方” => 最长边不超 40mm
                    ok = (SIZE_MIN_MM <= side_short
                          and side_long <= SIZE_MAX_MM * SIZE_TOL)
                    side_mm = side_long          # 打印用
                else:
                    side_a = side_b = side_mm = 0.0
                    ok = None

                cx, cy = x + w / 2.0, y + h / 2.0
                try:
                    cx, cy = float(b.cxf()), float(b.cyf())
                except Exception:
                    pass

                if MM_PER_PX > 0:
                    size_str = "%.0fx%.0fmm" % (side_long, side_short)
                else:
                    size_str = "%dx%dpx" % (a_px, b_px)

                found.append((cname, shape, conf, density, aspect,
                              side_mm, ok, cx, cy, size_str))

                # ---- 画 ----
                if DRAW_POLY:
                    for i in range(4):
                        x1, y1 = pts[i]
                        x2, y2 = pts[(i + 1) % 4]
                        img.draw_line(int(x1), int(y1), int(x2), int(y2),
                                      color=draw_color)
                img.draw_rect(x, y, w, h, color=draw_color)

                short = {"triangle": "tri", "circle": "cir",
                         "square": "sq", "rect": "rect", "unknown": "?"}[shape]
                label = "%s %s %s" % (cname, short, size_str)
                if ok is not None and not ok:
                    label += " x"
                img.draw_string(x, y - 18 if y > 18 else y + 2, label,
                                color=image.COLOR_GREEN if ok else image.COLOR_WHITE)

        # ---- HUD ----
        img.draw_string(4, 4, ("%.3f mm/px" % MM_PER_PX) if MM_PER_PX > 0 else "NO CALIB",
                        color=image.COLOR_YELLOW if MM_PER_PX > 0 else image.COLOR_RED)
        img.draw_string(4, CAM_H - 20, "blobs %d  pass %d"
                        % (total, sum(1 for f in found if f[6])), color=image.COLOR_GREEN)

        disp.show(img)

        # ---- 终端 ----
        now = time.time()
        if now - t_last >= PRINT_EVERY:
            t_last = now
            passed = [f for f in found if f[6]]
            passed.sort(key=lambda f: f[7])
            parts = []
            for f in passed:
                parts.append("%s %s(%.2f) %s @(%d,%d)" %
                             (f[0], SHAPE_CN[f[1]], f[2], f[9], f[7], f[8]))
            print("[frame %5d] blobs=%d pass=%d %s"
                  % (frame, total, len(passed), " | ".join(parts)))
            if SHAPE_DEBUG and not passed and found:
                for f in found[:3]:
                    print("    dbg %s shape=%s density=%.2f aspect=%.2f size=%s ok=%s"
                          % (f[0], f[1], f[3], f[4], f[9], f[6]))

except KeyboardInterrupt:
    print("\n[INFO] 用户中断")
except Exception as e:
    print("[ERROR] %s: %s" % (type(e).__name__, e))
    import traceback
    traceback.print_exc()
finally:
    del cam
    print("\n结束，共处理 %d 帧" % frame)
