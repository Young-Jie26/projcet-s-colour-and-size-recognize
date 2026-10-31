# -*- coding: utf-8 -*-
"""
一体化：标定 + 识别 + 标注（MaixCAM / MaixCAM2）
========================================================
一个脚本跑完全部流程，不用再依次运行：

  启动
   ├─ 有 /root/blob_calib.json  → 直接进入【识别】阶段
   └─ 没有（或 FORCE_CALIBRATE=True）→ 先在同一个程序里进入【标定】阶段：
        屏幕提示把 40mm 方块放中间，连续 STABLE_FRAMES 帧稳定后自动算出
        mm/px、存盘，然后**自动切换**到【识别】阶段，全程不用停。

运行时想重新标定：点屏幕右上角的 "CAL" 按钮（有触摸屏时）。

识别输出：
  屏幕上  框 + "红色 正五边形 38x38mm"，合格绿框、超尺寸白框带 x
  终端    每秒一行汇总：颜色 / 形状 / 尺寸 / 候选几何体 / 中心偏移

依赖：先标定过才能出 mm，没标定时只出像素（脚本会提示）。
"""

from maix import app, camera, display, image
import json
import math
import os
import time

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
CAM_W, CAM_H = 320, 240

KNOWN_MM = 40.0          # 标定物真实边长（mm）
FORCE_CALIBRATE = False  # True = 每次启动都重新标定
STABLE_FRAMES = 30       # 标定阶段连续多少帧稳定才接受
CALIB_PATH = "/root/blob_calib.json"

# 识别用：每种颜色的 LAB 阈值
COLORS = [
    ("red",    "红色", [[0, 80, 40, 80, 10, 80]],     image.COLOR_RED),
    ("green",  "绿色", [[0, 80, -120, -10, 0, 30]],   image.COLOR_GREEN),
    ("blue",   "蓝色", [[0, 80, 10, 100, -128, -38],
                        [45, 90, -45, 15, -60, -12]], image.COLOR_BLUE),
    ("yellow", "黄色", [[0, 80, -20, 30, 30, 90]],    image.COLOR_YELLOW),
]

# 标定阶段用：四种颜色都试，取最大的方块
CALIB_TH = []
for _c in COLORS:
    CALIB_TH += _c[2]

MIN_PIXELS = 120
CALIB_MIN_PIXELS = 200

SIZE_MAX_MM = 40.0
SIZE_MIN_MM = 8.0
SIZE_TOL = 1.10

TH_TRI = 0.660
TH_CIRC_PENT = 0.825
TH_PENT_QUAD = 0.935
SQ_ASPECT = 1.20

SHAPE_CN = {"triangle": "三角形", "circle": "圆形", "pentagon": "正五边形",
            "square": "正方形", "rect": "长方形", "unknown": "未知"}
SHAPE_EN = {"triangle": "TRI", "circle": "ROUND", "pentagon": "PENT",
            "square": "SQUARE", "rect": "RECT", "unknown": "?"}
SOLIDS = {
    "circle": ("球体", "圆柱", "圆锥"),
    "triangle": ("三棱柱", "正四面体"),
    "pentagon": ("五棱柱",),
    "square": ("正方体",),
    "rect": ("长方体",),
    "unknown": ("未知",),
}
COLOR_CN = {c[0]: c[1] for c in COLORS}

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


def save_mm_per_px(mmpp):
    try:
        with open(CALIB_PATH, "w") as f:
            json.dump({"mm_per_px": round(mmpp, 6),
                       "width": CAM_W, "height": CAM_H,
                       "known_mm": KNOWN_MM}, f)
        return True
    except Exception as e:
        print("[ERR] 保存标定失败:", e)
        return False


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
    if density < TH_CIRC_PENT:
        return "circle"
    if density < TH_PENT_QUAD:
        return "pentagon"
    return "square" if aspect < SQ_ASPECT else "rect"


def setup_font():
    """加载中文字体，失败就退回英文标注"""
    for p in ("/maixapp/share/font/SourceHanSansCN-Regular.otf",
              "/maixapp/share/font/SourceHanSans-Regular.otf"):
        if not os.path.exists(p):
            continue
        try:
            image.load_font("cn", p, size=14)
            image.set_default_font("cn")
            return True
        except Exception:
            pass
    return False


HAS_CN = setup_font()

cam = camera.Camera(CAM_W, CAM_H)
disp = display.Display()

try:
    from maix import touchscreen
    ts = touchscreen.TouchScreen()
except Exception:
    ts = None

MM_PER_PX = load_mm_per_px()
phase = "detect" if (MM_PER_PX > 0 and not FORCE_CALIBRATE) else "calib"

calib_samples = []
calib_frames = 0
t_last = 0.0
t_calib_hint = 0.0
frame = 0

print("=" * 58)
print(" 一体化：标定 + 识别 + 标注   %dx%d" % (CAM_W, CAM_H))
print(" 中文字体: %s" % ("已加载" if HAS_CN else "不可用，用英文标注"))
print(" 当前阶段: %s" % ("识别" if phase == "detect" else "标定"))
if phase == "detect":
    print(" 标定值  : %.4f mm/px" % MM_PER_PX)
else:
    print(" 请把 %.0fmm 方块放画面中间，连续 %d 帧稳定后自动标定"
          % (KNOWN_MM, STABLE_FRAMES))
print("=" * 58)

try:
    while not app.need_exit():
        img = cam.read()
        frame += 1

        # ---------- 触摸：点右上角 CAL 重新标定 ----------
        if ts is not None:
            try:
                x, y, pressed = ts.read()
                if pressed and x > CAM_W - 56 and y < 26:
                    phase = "calib"
                    calib_samples = []
                    calib_frames = 0
                    print("[INFO] 重新进入标定阶段")
            except Exception:
                pass

        # 右上角按钮
        img.draw_rect(CAM_W - 56, 0, 56, 24, color=image.COLOR_WHITE, thickness=-1)
        img.draw_string(CAM_W - 52, 4, "CAL", color=image.COLOR_BLACK)

        # =================== 标定阶段 ===================
        if phase == "calib":
            calib_frames += 1
            blobs = img.find_blobs(CALIB_TH, area_threshold=CALIB_MIN_PIXELS,
                                   pixels_threshold=CALIB_MIN_PIXELS)
            best, best_area = None, -1
            for b in blobs:
                x, y, w, h = box_of(b)
                if w <= 0 or h <= 0:
                    continue
                if not (0.70 <= float(min(w, h)) / max(w, h) <= 1.45):
                    continue
                if w * h > best_area:
                    best_area, best = w * h, b

            if best is not None:
                x, y, w, h = box_of(best)
                calib_samples.append((w + h) / 2.0)
                img.draw_rect(x, y, w, h, color=image.COLOR_GREEN)
                img.draw_string(x, y - 16 if y > 16 else y + 2,
                                "%dx%d" % (w, h), color=image.COLOR_GREEN)
            else:
                calib_samples = []

            n = len(calib_samples)
            img.draw_string(4, CAM_H - 40,
                            "CALIBRATE: put %.0fmm block in view" % KNOWN_MM,
                            color=image.COLOR_YELLOW)
            img.draw_string(4, CAM_H - 20, "%d/%d stable" % (min(n, STABLE_FRAMES),
                                                             STABLE_FRAMES),
                            color=image.COLOR_YELLOW)

            if n >= STABLE_FRAMES:
                calib_samples.sort()
                med = calib_samples[len(calib_samples) // 2]
                MM_PER_PX = KNOWN_MM / med
                save_mm_per_px(MM_PER_PX)
                print("[OK] 标定完成: %.1f px -> %.4f mm/px" % (med, MM_PER_PX))
                phase = "detect"
                t_last = 0.0

            now = time.time()
            if n == 0 and now - t_calib_hint > 4.0:
                t_calib_hint = now
                print("[hint] 还没找到合适的方块：确认它是红/绿/蓝/黄之一、"
                      "边长和 KNOWN_MM 一致、画面里别太小")
            disp.show(img)
            continue

        # =================== 识别阶段 ===================
        found = []
        total = 0
        for cname, ccn, th, draw_color in COLORS:
            blobs = img.find_blobs(th, area_threshold=MIN_PIXELS,
                                   pixels_threshold=MIN_PIXELS)
            for b in blobs:
                total += 1
                x, y, w, h = box_of(b)
                px = pixels_of(b)
                a_px, b_px, pts = min_rect_of(b)
                density = px / max(1.0, a_px * b_px)
                aspect = max(a_px, b_px) / max(1.0, min(a_px, b_px))
                shape = classify(density, aspect)

                side_long = max(a_px, b_px) * MM_PER_PX
                side_short = min(a_px, b_px) * MM_PER_PX
                ok = (SIZE_MIN_MM <= side_short
                      and side_long <= SIZE_MAX_MM * SIZE_TOL)

                cx, cy = x + w / 2.0, y + h / 2.0
                try:
                    cx, cy = float(b.cxf()), float(b.cyf())
                except Exception:
                    pass

                found.append((cname, ccn, shape, side_long, side_short,
                              ok, cx, cy, density))

                # ---- 标注 ----
                color = image.COLOR_GREEN if ok else image.COLOR_WHITE
                for i in range(4):
                    x1, y1 = pts[i]
                    x2, y2 = pts[(i + 1) % 4]
                    img.draw_line(int(x1), int(y1), int(x2), int(y2), color=color)
                img.draw_rect(x, y, w, h, color=draw_color)

                if HAS_CN:
                    label = "%s %s" % (ccn, SHAPE_CN[shape])
                    label2 = "%.0fx%.0fmm" % (side_long, side_short)
                else:
                    label = "%s %s" % (cname, SHAPE_EN[shape])
                    label2 = "%.0fx%.0fmm" % (side_long, side_short)

                ty = y - 34 if y > 34 else y + 2
                img.draw_string(x, ty, label, color=color)
                img.draw_string(x, ty + 15, label2, color=color)

        img.draw_string(4, 4, "%.3f mm/px" % MM_PER_PX, color=image.COLOR_YELLOW)
        img.draw_string(4, CAM_H - 20, "blobs %d  pass %d"
                        % (total, sum(1 for f in found if f[5])),
                        color=image.COLOR_GREEN)

        disp.show(img)

        # ---- 终端 ----
        now = time.time()
        if now - t_last >= 1.0:
            t_last = now
            passed = [f for f in found if f[5]]
            passed.sort(key=lambda f: f[6])
            parts = []
            for f in passed:
                cand = "/".join(SOLIDS[f[2]])
                parts.append("%s%s %.0fx%.0fmm -> %s"
                             % (f[1], SHAPE_CN[f[2]], f[3], f[4], cand))
            print("[frame %5d] blobs=%d pass=%d  %s"
                  % (frame, total, len(passed), " | ".join(parts)))
            for f in found[:4]:
                if not f[5]:
                    print("    dbg %s %s %.0fx%.0fmm density=%.2f -> 超限/不合格"
                          % (f[1], SHAPE_CN[f[2]], f[3], f[4], f[8]))

except KeyboardInterrupt:
    print("\n[INFO] 用户中断")
except Exception as e:
    print("[ERROR] %s: %s" % (type(e).__name__, e))
    import traceback
    traceback.print_exc()
finally:
    del cam
    print("\n结束，共处理 %d 帧" % frame)
