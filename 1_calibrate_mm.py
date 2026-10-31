# -*- coding: utf-8 -*-
"""
mm/像素 标定工具（任意颜色目标）—— MaixCAM / MaixCAM2
========================================================
作用：算出“1 个像素等于多少毫米”，之后主程序才能把色块的像素尺寸换算成 mm，
      从而判断它是否在 40mm 见方的范围内。

用法：
  1. 把已知边长的方块（默认 40mm）正对摄像头、平放在实际工作距离上，
     尽量让方块的边和画面横竖方向平行（别斜着放）。
  2. 改下面两个参数：
        KNOWN_MM = 40.0            # 它的真实边长
        TARGET   = "auto"          # 见下方说明
  3. 运行，等终端提示「标定完成」（自动存到 /root/blob_calib.json）。

TARGET 可选：
    "auto"    红/绿/蓝/黄 都试，取画面里最大的那个方块（默认，最省事）
    "red" / "green" / "blue" / "yellow"   只认这一种颜色
    "white" / "black"                     浅色 / 深色目标（如白色亚克力块）
    "custom"                              用下面 CUSTOM_TH 里的自定义阈值

为什么标定物不一定是红色：
    标定只关心“像素宽度”，跟颜色无关，所以目标是什么颜色都行，
    只要脚本能在画面里稳定地把它框出来即可。
"""

from maix import app, camera, display, image
import json
import os

# ------------------------------------------------------------------
# CONFIG —— 改这里
# ------------------------------------------------------------------
CAM_W, CAM_H = 320, 240          # 摄像头分辨率（主程序和这里保持一致）

KNOWN_MM = 40.0                  # 标定物的真实边长（mm）
TARGET = "auto"                  # auto / red / green / blue / yellow / white / black / custom
CUSTOM_TH = []                   # TARGET="custom" 时使用，例如 [[0,80,40,80,10,80]]

MIN_PIXELS = 200                 # 过滤小噪点
ASPECT_MIN, ASPECT_MAX = 0.70, 1.45   # 只接受接近正方形/圆形的目标

CALIB_PATH = "/root/blob_calib.json"
STABLE_FRAMES = 60               # 连续多少帧稳定后写入结果
MAX_FRAMES = 900                 # 最多等多少帧（防止一直卡着）

# ------------------------------------------------------------------
PRESETS = {
    "red":    [[0, 80, 40, 80, 10, 80]],
    "green":  [[0, 80, -120, -10, 0, 30]],
    "blue":   [[0, 80, 10, 100, -128, -38],
               [45, 90, -45, 15, -60, -12]],
    "yellow": [[0, 80, -20, 30, 30, 90]],
    "white":  [[80, 100, -8, 8, -8, 8]],
    "black":  [[0, 25, -10, 10, -10, 10]],
}
PRESET_NAMES = {0: "red", 1: "green", 2: "blue", 3: "yellow",
                4: "white", 5: "black"}


def build_thresholds():
    """按 TARGET 组装阈值列表，并返回每一段阈值对应的名字（用于打印）"""
    if TARGET == "custom":
        return CUSTOM_TH, []
    if TARGET == "auto":
        th, names = [], []
        for name in ("red", "green", "blue", "yellow"):
            th += PRESETS[name]
            names += [name] * len(PRESETS[name])
        return th, names
    th = PRESETS.get(TARGET)
    if not th:
        print("[WARN] 未知的 TARGET=%r，回退到 auto" % TARGET)
        return build_thresholds_auto()
    return th, [TARGET] * len(th)


def build_thresholds_auto():
    th, names = [], []
    for name in ("red", "green", "blue", "yellow"):
        th += PRESETS[name]
        names += [name] * len(PRESETS[name])
    return th, names


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


def color_of(b, names):
    """尽力猜出这个块命中的是哪一段阈值（部分固件没有 code()）"""
    try:
        code = int(b.code())
        for i in range(min(len(names), 31)):
            if code & (1 << i):
                return names[i]
    except Exception:
        pass
    return "?"


TH, TH_NAMES = build_thresholds()

cam = camera.Camera(CAM_W, CAM_H)
disp = display.Display()

samples = []
frame = 0
last_color = "?"

print("=" * 56)
print(" mm/像素 标定")
print(" 目标边长 : %.1f mm" % KNOWN_MM)
print(" 颜色模式 : %s（共 %d 段阈值）" % (TARGET, len(TH)))
print(" 分辨率   : %dx%d" % (CAM_W, CAM_H))
print(" 请把方块正对镜头、边与画面平行，保持工作距离")
print("=" * 56)

if not TH:
    print("[ERROR] 阈值为空，检查 TARGET / CUSTOM_TH")
    raise SystemExit(1)

while not app.need_exit():
    img = cam.read()
    frame += 1

    blobs = img.find_blobs(TH, area_threshold=MIN_PIXELS, pixels_threshold=MIN_PIXELS)

    best = None
    best_area = -1
    for b in blobs:
        x, y, w, h = box_of(b)
        if w <= 0 or h <= 0:
            continue
        ratio = float(min(w, h)) / max(w, h)
        if not (ASPECT_MIN <= ratio <= ASPECT_MAX):
            continue          # 形状不像方块，跳过（避免抓到长条）
        if w * h > best_area:
            best_area, best = w * h, b

    if best is not None:
        x, y, w, h = box_of(best)
        last_color = color_of(best, TH_NAMES)
        samples.append((w + h) / 2.0)

        img.draw_rect(x, y, w, h, image.COLOR_GREEN)
        img.draw_string(x, y - 18 if y > 18 else y + 2,
                        "%s %dx%d" % (last_color, w, h), color=image.COLOR_GREEN)
        mmpp = KNOWN_MM / max(1.0, (w + h) / 2.0)
        img.draw_string(4, 4, "%.3f mm/px  %d/%d"
                        % (mmpp, min(len(samples), STABLE_FRAMES), STABLE_FRAMES),
                        color=image.COLOR_YELLOW)

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
                print("\n[OK] 标定完成: 目标平均像素 = %.1f px（颜色: %s）" % (med, last_color))
                print("[OK] 结果: %.4f mm/px，已保存到 %s" % (mmpp, CALIB_PATH))
                print("[OK] 现在可以运行 2/3/4/5 号脚本了")
            except Exception as e:
                print("[ERR] 保存失败: %s" % e)
            break
    else:
        samples = []          # 一旦断了就重新积累，避免混入不同距离的样本
        img.draw_string(4, 4, "no target", color=image.COLOR_RED)

    if frame % 30 == 0:
        print("[info] 第 %d 帧: 检出 %d 个块, 有效样本 %d/%d, 当前目标颜色=%s"
              % (frame, len(blobs), len(samples), STABLE_FRAMES, last_color))

    if frame >= MAX_FRAMES:
        print("\n[WARN] 等太久还没稳定检出，可能原因：")
        print("       1) 标定物颜色不在 auto 覆盖范围（红/绿/蓝/黄）→ 改 TARGET，")
        print("          比如白色块用 TARGET=\"white\"，深色块用 \"black\"")
        print("       2) 光照太暗/太亮，物体在画面里发灰 → 加光或换角度")
        print("       3) 物体太小或太远 → 靠近一点，或调小 MIN_PIXELS")
        break

    disp.show(img)

del cam
print("\n结束。")
