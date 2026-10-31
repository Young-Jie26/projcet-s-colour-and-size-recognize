# -*- coding: utf-8 -*-
"""
0_lab_probe.py —— 取色 / LAB 诊断工具
========================================================
用途：当某个颜色（比如蓝色）怎么都检测不到时，用它看画面里真实的 LAB 值，
      再照着把阈值填到 2_find_blocks.py / 3_find_shapes.py 里。

用法：在 MaixVision 里运行，把目标物体放到画面**正中间**，
      终端会打印 5×4 个采样点的 RGB / LAB，以及按中心点算出的建议阈值。

为什么要这个工具：
  LAB 不是“RGB 各减一点”，同一类颜色（比如蓝）差别非常大：
      正蓝 RGB(0,0,255)   -> L=32  a=+79  b=-108
      天蓝 RGB(135,206,235)-> L=79  a=-14  b= -19
  一个阈值窗口覆盖不了所有蓝，所以必须按实物测一次。
"""

from maix import app, camera, display, image
import math
import time

# ------------------------------------------------------------------
CAM_W, CAM_H = 320, 240
GRID_COLS, GRID_ROWS = 5, 4      # 网格采样点
HALF = 4                         # 每个点取 (2*HALF+1)^2 的平均，抗噪
PRINT_EVERY = 1.5                # 终端打印间隔（秒）
# ------------------------------------------------------------------

try:
    import numpy as np
    HAS_NP = True
except Exception:
    HAS_NP = False


def srgb_to_lab(r, g, b):
    """sRGB(0-255) -> LAB（L:0-100, a/b:-128~127），与 MaixPy 阈值同一约定"""
    def inv(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = inv(r), inv(g), inv(b)
    X = r * 0.4124 + g * 0.3576 + b * 0.1805
    Y = r * 0.2126 + g * 0.7152 + b * 0.0722
    Z = r * 0.0193 + g * 0.1192 + b * 0.9505
    X /= 0.95047
    Z /= 1.08883

    def f(t):
        return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = f(X), f(Y), f(Z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def sample_rgb(arr, x, y, half=HALF):
    """arr 是 BGR 的 numpy 数组，返回该点邻域平均的 (r, g, b)"""
    h, w = arr.shape[0], arr.shape[1]
    y0, y1 = max(0, y - half), min(h, y + half + 1)
    x0, x1 = max(0, x - half), min(w, x + half + 1)
    patch = arr[y0:y1, x0:x1]
    b = float(patch[:, :, 0].mean())
    g = float(patch[:, :, 1].mean())
    r = float(patch[:, :, 2].mean())
    return int(round(r)), int(round(g)), int(round(b))


def suggest(lab):
    L, a, b = lab
    lo = lambda v, lo_, hi_: max(lo_, min(hi_, int(round(v))))
    return "[%d, %d, %d, %d, %d, %d]" % (
        lo(L - 12, 0, 100), lo(L + 12, 0, 100),
        lo(a - 25, -128, 127), lo(a + 25, -128, 127),
        lo(b - 25, -128, 127), lo(b + 25, -128, 127))


if not HAS_NP:
    print("[ERROR] 没有 numpy，无法取色。可以改用设备自带的「找色块」App 取 LAB。")
    raise SystemExit(1)

cam = camera.Camera(CAM_W, CAM_H)
disp = display.Display()

print("=" * 64)
print(" LAB 取色工具   %dx%d" % (CAM_W, CAM_H))
print(" 把目标物体放到画面正中间，看下面终端输出的 L / a / b")
print("=" * 64)

t_last = 0.0
frame = 0

try:
    while not app.need_exit():
        img = cam.read()
        frame += 1
        arr = image.image2cv(img)          # BGR numpy

        # 网格采样点
        xs = [int(CAM_W * (i + 0.5) / GRID_COLS) for i in range(GRID_COLS)]
        ys = [int(CAM_H * (j + 0.5) / GRID_ROWS) for j in range(GRID_ROWS)]

        for y in ys:
            for x in xs:
                img.draw_cross(x, y, color=image.COLOR_GREEN, size=4)
        # 中心加粗标记
        img.draw_cross(CAM_W // 2, CAM_H // 2, color=image.COLOR_RED, size=10)

        now = time.time()
        if now - t_last >= PRINT_EVERY:
            t_last = now
            print("\n--- frame %d ---" % frame)
            print("     坐标        RGB               L      a      b")
            for y in ys:
                for x in xs:
                    r, g, b = sample_rgb(arr, x, y)
                    L, a, bb = srgb_to_lab(r, g, b)
                    print("  (%3d,%3d)  (%3d,%3d,%3d)   %6.1f %6.1f %6.1f"
                          % (x, y, r, g, b, L, a, bb))

            cx, cy = CAM_W // 2, CAM_H // 2
            r, g, b = sample_rgb(arr, cx, cy, half=8)
            lab = srgb_to_lab(r, g, b)
            print("  中心 (%d,%d) RGB(%d,%d,%d)  L=%.1f a=%.1f b=%.1f"
                  % (cx, cy, r, g, b, lab[0], lab[1], lab[2]))
            print("  建议阈值: %s" % suggest(lab))
            print("  （把物体挪到中心后多看几组，取它们的公共范围最稳）")

        disp.show(img)

except KeyboardInterrupt:
    print("\n[INFO] 用户中断")
except Exception as e:
    print("[ERROR] %s: %s" % (type(e).__name__, e))
    import traceback
    traceback.print_exc()
finally:
    del cam
    print("\n结束")
