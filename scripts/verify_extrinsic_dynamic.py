#!/usr/bin/env python3
"""动态验证外参: 用 imucam 标定录制里的 AprilGrid 相机真实轨迹 vs VINS 轨迹。

原理:
  - imucam 录制中板子固定墙上, 相机+IMU 运动
  - 从每帧左IR检测 AprilGrid + PnP -> 相机在板子系的位姿 = 真实轨迹 (ground truth)
  - VINS 用给定外参跑同一段数据 -> 估计轨迹
  - 对齐(尺度/旋转/平移)后比较: 正确的外参 -> 两轨迹接近; 错的外参 -> 偏差大

用法:
  python3 scripts/verify_extrinsic_dynamic.py --session recordings/calib_xxx --vins-csv /tmp/vins_odom.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from collect_calib_data import load_aprilgrid_config, AprilGridPoseTracker


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="imucam 标定录制目录")
    ap.add_argument("--vins-csv", required=True, help="VINS odom CSV")
    ap.add_argument("--aprilgrid", default="config/aprilgrid_6x6_35mm.yaml")
    ap.add_argument("--out", default="/tmp/extrinsic_verify.png")
    args = ap.parse_args()

    sess = Path(args.session)
    frames = sorted((sess / "left_hand" / "frames").glob("*.jpg"))
    print(f"帧数: {len(frames)}")

    grid = load_aprilgrid_config(ROOT / args.aprilgrid)
    # D405 左IR 出厂内参 (rectified, 畸变0)
    K = np.array([[647.5198, 0, 638.5343],
                  [0, 647.5198, 369.7683],
                  [0, 0, 1.0]], dtype=np.float64)
    D = np.zeros(5)

    from aprilgrid import Detector
    det = Detector("t36h11")

    # 相机在板子系里的位置 C_b = -R^T @ t
    traj_gt = []   # (frame_idx, x,y,z in board frame)
    prev_t = None
    for i, fp in enumerate(frames):
        img = cv2.imread(str(fp), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        dets = det.detect(img)
        if len(dets) < 4:
            continue
        obj, img_pts = [], []
        for d in dets:
            tid = int(d.tag_id)
            if tid < 0 or tid >= grid["tagCols"] * grid["tagRows"]:
                continue
            cor = getattr(d, "corners", None)
            if cor is None:
                continue
            cor = np.asarray(cor, dtype=np.float32).reshape(-1, 2)
            if cor.shape != (4, 2):
                continue
            row, col = tid // grid["tagCols"], tid % grid["tagCols"]
            pitch = grid["tagSize"] * (1 + grid["tagSpacing"])
            x0, y0 = col * pitch, row * pitch
            s = grid["tagSize"]
            obj.append(np.array([[x0, y0, 0], [x0+s, y0, 0], [x0+s, y0+s, 0], [x0, y0+s, 0]], np.float32))
            img_pts.append(cor)
        if len(obj) < 4:
            continue
        ok, rvec, tvec = cv2.solvePnP(np.vstack(obj), np.vstack(img_pts), K, D)
        if not ok:
            continue
        R, _ = cv2.Rodrigues(rvec)
        C_b = (-R.T @ tvec).flatten()   # 相机在板子系位置
        traj_gt.append((i, C_b[0], C_b[1], C_b[2]))

    if len(traj_gt) < 20:
        print(f"AprilGrid 轨迹点数太少: {len(traj_gt)}, 无法验证")
        return 1
    print(f"AprilGrid 真实轨迹: {len(traj_gt)} 点")

    # 读 VINS 轨迹
    import csv
    rows = list(csv.DictReader(open(args.vins_csv)))
    if len(rows) < 20:
        print("VINS 轨迹点数太少")
        return 1
    traj_v = np.array([[float(r["x"]), float(r["y"]), float(r["z"])] for r in rows])
    print(f"VINS 轨迹: {len(traj_v)} 点, 路径 {np.sum(np.linalg.norm(np.diff(traj_v,axis=0),axis=1)):.2f}m")

    # 对齐: Umeyama (尺度+旋转+平移)
    P = np.array([[a[1], a[2], a[3]] for a in traj_gt])  # ground truth (目标)
    Q = traj_v[:len(P)] if len(traj_v) >= len(P) else traj_v  # VINS (源)
    n = min(len(P), len(Q))
    P, Q = P[:n], Q[:n]

    mu_p, mu_q = P.mean(0), Q.mean(0)
    Pc, Qc = P - mu_p, Q - mu_q
    H = Qc.T @ Pc
    U, S, Vt = np.linalg.svd(H)
    R_ = U @ Vt
    if np.linalg.det(R_) < 0:
        Vt[-1, :] *= -1
        R_ = U @ Vt
    s_ = np.sum(S) / np.sum(Qc**2)
    t_ = mu_p - s_ * R_ @ mu_q

    aligned = s_ * Q @ R_.T + t_
    err = np.linalg.norm(P - aligned, axis=1)
    ate = np.sqrt(np.mean(err**2))
    print(f"\n对齐后 ATE(RMSE): {ate*100:.1f} cm")
    print(f"尺度: {s_:.3f}  (应接近 1)")
    print(f"\n判读: ATE 小(<15cm)+尺度接近1 = 外参正确; 大 = 外参有误")

    # 画图
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(14, 5))
    ax[0].plot(P[:, 0], P[:, 1], "g-", label="AprilGrid GT")
    ax[0].plot(aligned[:, 0], aligned[:, 1], "r--", label="VINS (aligned)")
    ax[0].set_title(f"XY 轨迹对比 (ATE={ate*100:.1f}cm, 尺度={s_:.2f})")
    ax[0].legend(); ax[0].set_aspect("equal"); ax[0].grid()
    ax[1].plot(err * 100)
    ax[1].set_title("逐点误差 (cm)")
    ax[1].set_xlabel("帧"); ax[1].grid()
    fig.tight_layout()
    fig.savefig(args.out, dpi=150)
    print(f"图 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
