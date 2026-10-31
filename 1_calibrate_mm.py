# -*- coding: utf-8 -*-
"""
mm/像素 标定工具（MaixCAM / MaixCAM2）
========================================================
作用：算出“1 个像素等于多少毫米”，之后主程序才能把色块的像素尺寸换算成 mm，
      从而判断它是否在 40mm 见方的范围内。

用法：
  1. 把已知边长的方块（默认按 40mm）正对摄像头、平放在实际工作距离上，
     尽量让方块的边和画面横竖方向平行（别斜着放）。
  2. 把下面的 KNOWN_MM 改成它的真实边长，把 COLOR_TH 改成它的颜色阈值。
  3. 运行本脚本，等终端提示「标定完成」即可（会自动存到 /root/blob_calib.json）。

提示：颜色阈值可以直接用设备自带的「找色块」App 调好后抄过来。
"""

from maix import app, camera, display, image
import json
import os

# ------------------------------------------------------------------
# CONFIG —— 改这里
# ------------------------------------------------------------------
CAM_W, CAM_H = 320, 240          # 摄像头分辨率（主程序和这里保持一致）

KNOWN_MM = 40.0                  # 标定物的真实边长（mm），40mm 方块就填 40
COLOR_TH = [[0, 80, 40, 80, 10, 80]]   # 标定物的 LAB 阈值，如红色
MIN_PIXELS = 200                 # 过滤小噪点

CALIB_PATH = "/root/blob_calib.json"
STABLE_FRAMES = 60               # 连续多少帧稳定后写入结果
MAX_FRAMES = 600                 # 最多等多少帧（防止一直卡着）

# ------------------------------------------------------------------

cam = camera.Camera(CAM_W, CAM_H)
disp = display.Display()

samples = []
frame = 0
saved = False


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

print("=" * 52)
print(" mm/像素 标定")
print(" 标定物边长: %.1f mm" % KNOWN_MM)
print(" 分辨率    : %dx%d" % (CAM_W, CAM_H))
print(" 请把方块正对镜头、边与画面平行，保持工作距离")
print("=" * 52)

while not app.need_exit():
    img = cam.read()
    frame += 1

    blobs = img.find_blobs(COLOR_TH, area_threshold=MIN_PIXELS, pixels_threshold=MIN_PIXELS)
    best = None
    best_w = -1
    for b in blobs:
        w = box_of(b)[2]
        if w > best_w:
            best_w, best = w, b

    if best is not None:
        x, y, w, h = box_of(best)
        samples.append((w + h) / 2.0)

        img.draw_rect(x, y, w, h, image.COLOR_GREEN)
        img.draw_string(x, y - 18 if y > 18 else y + 2,
                        "%d x %d px" % (w, h), color=image.COLOR_GREEN)
        mmpp = KNOWN_MM / max(1.0, (w + h) / 2.0)
        img.draw_string(4, 4, "%.3f mm/px" % mmpp, color=image.COLOR_YELLOW)

        if len(samples) >= STABLE_FRAMES:
            samples.sort()
            med = samples[len(samples) // 2]
            mmpp = KNOWN_MM / med
            data = {"mm_per_px": round(mmpp, 6),
                    "width": CAM_W, "height": CAM_H,
                    "known_mm": KNOWN_MM}
            try:
                with open(CALIB_PATH, "w") as f:
                    json.dump(data, f)
                saved = True
                print("\n[OK] 标定完成: 目标平均像素 = %.1f px" % med)
                print("[OK] 结果: %.4f mm/px，已保存到 %s" % (mmpp, CALIB_PATH))
                print("[OK] 现在可以运行 2_find_blocks.py 了")
            except Exception as e:
                print("[ERR] 保存失败: %s" % e)
            break
    else:
        samples = []
        img.draw_string(4, 4, "no target", color=image.COLOR_RED)

    if frame % 30 == 0:
        print("[info] 第 %d 帧, 检出 %d 个目标, 有效样本 %d"
              % (frame, len(blobs), len(samples)))

    if frame >= MAX_FRAMES:
        print("\n[WARN] 等太久还没稳定检出，检查阈值/光线/距离后重试")
        break

    disp.show(img)

del cam
print("\n结束。")
