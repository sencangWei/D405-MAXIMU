#!/usr/bin/env python3
"""AprilGrid 实时预览与诊断工具。

用法:
  python scripts/preview_aprilgrid.py
  python scripts/preview_aprilgrid.py --family t36h11b1
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import load_config
from ego_vio.camera.realsense_capture import RealSenseCapture


def draw_detections(img, dets):
    for d in dets:
        corners = getattr(d, "corners", None)
        if corners is None:
            continue
        corners = np.asarray(corners, dtype=np.float32)
        if corners.size == 8:
            corners = corners.reshape(4, 2)
        if corners.shape != (4, 2):
            continue
        corners = corners.astype(int)
        for i in range(4):
            pt1 = tuple(corners[i].tolist())
            pt2 = tuple(corners[(i + 1) % 4].tolist())
            cv2.line(img, pt1, pt2, (0, 255, 0), 2)
        cx = int(corners[:, 0].mean())
        cy = int(corners[:, 1].mean())
        cv2.putText(img, str(int(d.tag_id)), (cx, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--family", default="t36h11",
                    help="AprilTag 家族: t36h11 (Kalibr 2-bit) 或 t36h11b1 (AprilTag3 1-bit)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    unit = cfg.units[0]

    try:
        from aprilgrid import Detector
        detector = Detector(args.family)
        print(f"[AprilGrid] 检测器家族: {args.family}")
    except Exception as e:
        print(f"[错误] 无法加载 AprilGrid 检测器: {e}")
        print("请先安装: pip install aprilgrid")
        return 1

    cam = RealSenseCapture(
        serial=unit.camera.serial,
        width=unit.camera.width, height=unit.camera.height,
        fps=unit.camera.fps, enable_depth=False,
        auto_exposure=unit.camera.auto_exposure,
        exposure_us=unit.camera.exposure_us,
        gain=unit.camera.gain,
        name="preview",
    )
    if not cam.start():
        print("[错误] 相机启动失败")
        return 1

    intrinsics = cam.get_intrinsics()
    if intrinsics is not None:
        K, D = intrinsics
        print(f"[相机内参] fx={K[0,0]:.1f} fy={K[1,1]:.1f} cx={K[0,2]:.1f} cy={K[1,2]:.1f}")

    latest_frame = {"img": None, "lock": False}

    def on_frame(f):
        latest_frame["img"] = f.color

    # 临时替换回调
    cam.on_frame = on_frame

    print("\n实时预览中...")
    print("按键: 'q' 退出 | 's' 保存当前帧 | '1'/'2' 切换 t36h11/t36h11b1")

    window_name = "AprilGrid Preview"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    t0 = time.time()
    frame_count = 0
    detect_count = 0
    family = args.family

    while True:
        img = latest_frame["img"]
        if img is None:
            time.sleep(0.03)
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        try:
            dets = detector.detect(gray)
        except Exception:
            dets = []

        frame_count += 1
        if len(dets) >= 4:
            detect_count += 1

        vis = img.copy()
        draw_detections(vis, dets)
        cv2.putText(vis, f"family={family} tags={len(dets)}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        elapsed = time.time() - t0
        if elapsed >= 2.0:
            fps = frame_count / elapsed
            det_rate = detect_count / frame_count * 100 if frame_count else 0
            cv2.putText(vis, f"fps={fps:.1f} detect_rate={det_rate:.0f}%", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        cv2.imshow(window_name, vis)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            path = f"aprilgrid_preview_{time.strftime('%H%M%S')}.jpg"
            cv2.imwrite(path, vis)
            print(f"[保存] {path}")
        elif key == ord('1'):
            family = "t36h11"
            detector = Detector(family)
            print(f"[切换] {family}")
        elif key == ord('2'):
            family = "t36h11b1"
            detector = Detector(family)
            print(f"[切换] {family}")

        # 每 2 秒重置一次统计
        if elapsed >= 2.0:
            t0 = time.time()
            frame_count = 0
            detect_count = 0

    cam.stop()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
