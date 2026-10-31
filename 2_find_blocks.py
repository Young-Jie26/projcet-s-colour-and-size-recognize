# -*- coding: utf-8 -*-
"""
多颜色色块检测 + 40mm 见方尺寸判定（MaixCAM / MaixCAM2）
========================================================
功能：
  * 同时检测多种颜色（默认 红/绿/蓝/黄，可自行增删）
  * 把像素尺寸换算成 mm（需先用 1_calibrate_mm.py 标定）
  * 只把“尺寸落在 40mm 见方内”的色块标为合格（PASS）
  * 屏幕画框 + 终端每秒输出一次汇总（颜色、中心、尺寸、偏移）

注意：
  * 尺寸换算假设色块基本正对镜头、且处于标定时的那个工作距离。
    距离变远/变近，mm 值都会偏，需要重新标定或按距离补偿。
  * 色块斜放时外接矩形会变大，所以判定用“等效边长 = sqrt(w*h)”，
    比直接用外接框更稳。
"""

from maix import app, camera, display, image
import json
import os
import math
import time

# ------------------------------------------------------------------
# CONFIG —— 主要改这里
# ------------------------------------------------------------------
CAM_W, CAM_H = 320, 240          # 必须和标定时一致

# 颜色表：(名字, LAB阈值, 画框颜色)
# 阈值格式 [Lmin, Lmax, Amin, Amax, Bmin, Bmax]，可写多段
COLORS = [
    ("red",    [[0, 80, 40, 80, 10, 80]],       image.COLOR_RED),
    ("green",  [[0, 80, -120, -10, 0, 30]],     image.COLOR_GREEN),
    ("blue",   [[0, 80, 30, 100, -120, -60]],   image.COLOR_BLUE),
    ("yellow", [[0, 80, -20, 30, 30, 90]],      image.COLOR_YELLOW),
]

MIN_PIXELS = 120                 # 小于这个像素数的当作噪点丢掉

SIZE_MAX_MM = 40.0               # 目标尺寸上限（40mm 见方）
SIZE_MIN_MM = 8.0                # 太小也不看
SIZE_TOL = 1.10                  # 允许 10% 误差，40mm 判到 44mm 也算过

REQUIRE_SQUARE = True            # 是否要求形状接近正方形
SQUARE_MIN, SQUARE_MAX = 0.65, 1.55   # 外接框 w/h 的允许范围（斜放 45° 时约 1.41）

DRAW_CENTER = True               # 画中心十字
PRINT_EVERY = 1.0                # 终端汇总间隔（秒）

CALIB_PATH = "/root/blob_calib.json"

# ------------------------------------------------------------------


def load_mm_per_px():
    """读标定文件，按分辨率换算；没有标定返回 0"""
    try:
        with open(CALIB_PATH) as f:
            d = json.load(f)
        mmpp = float(d.get("mm_per_px", 0))
        w = int(d.get("width", CAM_W) or CAM_W)
        if mmpp > 0 and w > 0:
            return mmpp * (float(w) / CAM_W)   # 分辨率不同则等比缩放
    except Exception:
        pass
    return 0.0


def box_of(b):
    """兼容不同固件：返回 (x, y, w, h)"""
    try:
        return int(b[0]), int(b[1]), int(b[2]), int(b[3])
    except Exception:
        pass
    try:
        return int(b.x()), int(b.y()), int(b.w()), int(b.h())
    except Exception:
        return int(b.x), int(b.y), int(b.w), int(b.h)


MM_PER_PX = load_mm_per_px()

cam = camera.Camera(CAM_W, CAM_H)
disp = display.Display()

print("=" * 56)
print(" 多颜色色块检测  分辨率 %dx%d" % (CAM_W, CAM_H))
if MM_PER_PX > 0:
    print(" 标定: %.4f mm/px  -> 40mm 约合 %d px" % (MM_PER_PX, int(40 / MM_PER_PX)))
else:
    print(" [WARN] 没有标定文件 %s" % CALIB_PATH)
    print("        先运行 1_calibrate_mm.py，否则只能输出像素尺寸")
print("=" * 56)

t_last = 0.0
frame = 0
warned = False

try:
    while not app.need_exit():
        img = cam.read()
        frame += 1

        found = []      # (name, color, cx, cy, w_mm, h_mm, side_mm, pass)
        total = 0

        for name, th, draw_color in COLORS:
            blobs = img.find_blobs(th, area_threshold=MIN_PIXELS,
                                   pixels_threshold=MIN_PIXELS)
            for b in blobs:
                x, y, w, h = box_of(b)
                total += 1
                cx, cy = x + w / 2.0, y + h / 2.0

                if MM_PER_PX > 0:
                    w_mm = w * MM_PER_PX
                    h_mm = h * MM_PER_PX
                    side_mm = math.sqrt(max(1e-6, w_mm * h_mm))
                    ratio = float(w) / max(1.0, float(h))
                    ok = (SIZE_MIN_MM <= side_mm <= SIZE_MAX_MM * SIZE_TOL)
                    if REQUIRE_SQUARE and not (SQUARE_MIN <= ratio <= SQUARE_MAX):
                        ok = False
                else:
                    w_mm = h_mm = side_mm = 0.0
                    ok = None

                found.append((name, draw_color, cx, cy, w_mm, h_mm, side_mm, ok))

                # ---- 画 ----
                img.draw_rect(x, y, w, h, color=draw_color)
                if DRAW_CENTER:
                    img.draw_line(int(cx) - 6, int(cy), int(cx) + 6, int(cy), color=draw_color)
                    img.draw_line(int(cx), int(cy) - 6, int(cx), int(cy) + 6, color=draw_color)

                if ok is None:
                    label = "%s %dpx" % (name, w)
                elif ok:
                    label = "%s %.0fmm" % (name, side_mm)
                else:
                    label = "%s %.0fmm x" % (name, side_mm)
                img.draw_string(x, y - 18 if y > 18 else y + 2, label,
                                color=image.COLOR_GREEN if ok else image.COLOR_WHITE)

        # ---- HUD ----
        if MM_PER_PX > 0:
            img.draw_string(4, 4, "%.3f mm/px" % MM_PER_PX, color=image.COLOR_YELLOW)
        else:
            img.draw_string(4, 4, "NO CALIB", color=image.COLOR_RED)
        img.draw_string(4, CAM_H - 20, "blobs %d  pass %d"
                        % (total, sum(1 for f in found if f[7])), color=image.COLOR_GREEN)

        disp.show(img)

        # ---- 终端汇总 ----
        now = time.time()
        if now - t_last >= PRINT_EVERY:
            t_last = now
            if MM_PER_PX <= 0 and not warned:
                warned = True
                print("[WARN] 未标定，尺寸仅供参考像素值")
            passed = [f for f in found if f[7]]
            passed.sort(key=lambda f: f[2])
            line = " | ".join(
                "%s %.0fmm @(%d,%d) d=(%+.0f,%+.0f)mm"
                % (f[0], f[6], f[2], f[3],
                   (f[2] - CAM_W / 2.0) * MM_PER_PX,
                   (f[3] - CAM_H / 2.0) * MM_PER_PX)
                for f in passed)
            print("[frame %5d] blobs=%d pass=%d %s"
                  % (frame, total, len(passed), line if line else ""))

except KeyboardInterrupt:
    print("\n[INFO] 用户中断")
except Exception as e:
    print("[ERROR] %s: %s" % (type(e).__name__, e))
    import traceback
    traceback.print_exc()
finally:
    del cam
    print("\n结束，共处理 %d 帧" % frame)
