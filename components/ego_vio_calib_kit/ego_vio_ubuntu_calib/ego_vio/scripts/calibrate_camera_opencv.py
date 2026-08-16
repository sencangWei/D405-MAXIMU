#!/usr/bin/env python3
"""用 AprilGrid 检测 + OpenCV calibrateCamera 标定相机内参, 输出 Kalibr 格式 camchain.yaml。

检测用 `aprilgrid` 包(与采集脚本一致), 避免 OpenCV 版本差异。

用法:
  python scripts/calibrate_camera_opencv.py --input recordings/calib_xxx --aprilgrid config/aprilgrid_6x6_35mm.yaml --output camchain.yaml
"""
import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_aprilgrid_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return {
        "tagCols": int(cfg["tagCols"]),
        "tagRows": int(cfg["tagRows"]),
        "tagSize": float(cfg["tagSize"]),
        "tagSpacing": float(cfg["tagSpacing"]),
    }


def read_camera_ts(path: Path):
    rows = []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append((int(r["idx"]), int(r["frame_number"]), float(r["ts_mono"])))
    return rows


def tag_corners_3d(tag_id: int, grid_cfg: dict):
    """AprilGrid 中某个 tag 的 4 个角点 3D 坐标。"""
    row = tag_id // grid_cfg["tagCols"]
    col = tag_id % grid_cfg["tagCols"]
    pitch = grid_cfg["tagSize"] * (1.0 + grid_cfg["tagSpacing"])
    x0 = col * pitch
    y0 = row * pitch
    s = grid_cfg["tagSize"]
    return np.array([
        [x0, y0, 0.0],
        [x0 + s, y0, 0.0],
        [x0 + s, y0 + s, 0.0],
        [x0, y0 + s, 0.0],
    ], dtype=np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="录制目录, 如 recordings/calib_xxx")
    ap.add_argument("--unit", default="left_hand", help="单元名")
    ap.add_argument("--aprilgrid", default="config/aprilgrid_6x6_35mm.yaml", help="AprilGrid 配置")
    ap.add_argument("--output", default="camchain_opencv.yaml", help="输出 camchain yaml")
    ap.add_argument("--every-n", type=int, default=3, help="每 N 帧采样一帧做标定")
    ap.add_argument("--family", default="t36h11", help="AprilTag 家族")
    args = ap.parse_args()

    unit_dir = Path(args.input) / args.unit
    grid_cfg = load_aprilgrid_config(Path(args.aprilgrid))

    try:
        from aprilgrid import Detector
        detector = Detector(args.family)
    except Exception as e:
        print(f"[错误] 无法加载 aprilgrid 检测器: {e}")
        return 1

    cam_rows = read_camera_ts(unit_dir / "camera_ts.csv")
    frames_dir = unit_dir / "frames"

    all_obj_points = []
    all_img_points = []
    image_size = None
    used_frames = 0
    skipped_no_file = 0
    skipped_few_tags = 0
    detection_counts = []

    for i, (idx, fnum, ts) in enumerate(cam_rows):
        if i % args.every_n != 0:
            continue
        img_path = frames_dir / f"{idx:06d}.jpg"
        img = cv2.imread(str(img_path))
        if img is None:
            skipped_no_file += 1
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if image_size is None:
            image_size = gray.shape[::-1]

        try:
            dets = detector.detect(gray)
        except Exception as e:
            print(f"[debug] frame {idx} detect exception: {e}")
            continue

        detection_counts.append(len(dets))
        if len(dets) < 4:
            skipped_few_tags += 1
            continue

        obj_pts = []
        img_pts = []
        for d in dets:
            corners = getattr(d, "corners", None)
            if corners is None:
                continue
            corners = np.asarray(corners, dtype=np.float32)
            if corners.size == 8:
                corners = corners.reshape(4, 2)
            if corners.shape != (4, 2):
                print(f"[debug] frame {idx} tag {d.tag_id} corners shape {corners.shape}")
                continue
            obj_pts.append(tag_corners_3d(int(d.tag_id), grid_cfg))
            img_pts.append(corners)

        if len(obj_pts) < 4:
            skipped_few_tags += 1
            continue

        all_obj_points.append(np.vstack(obj_pts))
        all_img_points.append(np.vstack(img_pts))
        used_frames += 1

    print(f"[debug] 尝试帧数: {len(detection_counts)}, 没读到图: {skipped_no_file}")
    if detection_counts:
        print(f"[debug] 检测 tag 数: min={min(detection_counts)}, max={max(detection_counts)}, "
              f"mean={sum(detection_counts)/len(detection_counts):.1f}")
    print(f"[debug] tag<4 跳过的帧: {skipped_few_tags}")
    print(f"[debug] 有效帧: {used_frames}")

    if used_frames < 10:
        print(f"[错误] 有效帧太少: {used_frames}, 无法标定")
        return 1

    print(f"[标定] 使用 {used_frames}/{len(cam_rows)} 帧")

    h, w = image_size[1], image_size[0]
    K_init = np.array([[max(w, h), 0, w / 2.0],
                       [0, max(w, h), h / 2.0],
                       [0, 0, 1]], dtype=np.float64)
    D_init = np.zeros(5, dtype=np.float64)

    # 用标准 pinhole-radtan 模型(4 参数: k1,k2,p1,p2), 与 Kalibr 一致
    D_init = np.zeros(4, dtype=np.float64)
    ret, K, D, rvecs, tvecs = cv2.calibrateCamera(
        all_obj_points, all_img_points, image_size,
        K_init, D_init,
        flags=cv2.CALIB_FIX_K3,
        criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-6),
    )
    D = np.asarray(D).ravel()[:4]

    # 重投影误差
    total_err = 0
    total_pts = 0
    for obj_pts, img_pts, rvec, tvec in zip(all_obj_points, all_img_points, rvecs, tvecs):
        proj, _ = cv2.projectPoints(obj_pts, rvec, tvec, K, D)
        proj = proj.reshape(-1, 2)
        err = np.linalg.norm(img_pts - proj, axis=1)
        total_err += float(np.sum(err ** 2))
        total_pts += len(obj_pts)
    rms = np.sqrt(total_err / total_pts)

    print(f"[结果] 重投影误差 RMS: {rms:.3f} px")
    print(f"       fx={float(K[0,0]):.3f} fy={float(K[1,1]):.3f} cx={float(K[0,2]):.3f} cy={float(K[1,2]):.3f}")
    print(f"       D=({float(D[0]):.5f}, {float(D[1]):.5f}, {float(D[2]):.5f}, {float(D[3]):.5f})")

    if rms > 2.0:
        print("[警告] 重投影误差偏大(>2px), 建议检查板子平整度/图像清晰度")

    # Kalibr camchain yaml (兼容老版 Kalibr Kinetic, camera_model 用 pinhole)
    camchain = {
        "cam0": {
            "cam_overlaps": [],
            "camera_model": "pinhole",
            "distortion_coeffs": [float(D[0]), float(D[1]), float(D[2]), float(D[3])],
            "distortion_model": "radtan",
            "intrinsics": [float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2])],
            "resolution": [w, h],
            "rostopic": "/cam0/image_raw",
        }
    }

    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(camchain, f, default_flow_style=False, sort_keys=False)

    print(f"[保存] {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
