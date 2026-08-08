#!/usr/bin/env python3
"""手眼标定: 固定出厂内参, 用 AprilGrid 相机位姿 + IMU 陀螺积分解外参.

原理 (AX=XB):
  C_i = I_i * X   (C_i 相机在板子系位姿, I_i IMU 位姿, X = T_imu_cam 外参)
  -> 相邻时刻: ΔI_ij * X = X * ΔC_ij
  用多对相对运动解 X (旋转部分 Tsai/Lie 线性最小二乘, 平移部分线性最小二乘).

输入:  imucam 标定录制 (left_hand/frames + camera_ts.csv + imu.bin)
输出:  T_imu_cam (X), 以及 VINS body_T_cam0 = R_g @ X (重力Y->Z组合)

用法:
  python3 scripts/handeye_calibration.py --session recordings/calib_xxx
"""
from __future__ import annotations

import argparse
import csv
import struct
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from collect_calib_data import load_aprilgrid_config

IMU_PACK_FMT = "<dI7f"
IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)
DEG2RAD = np.pi / 180.0

# 出厂 rectified 内参 (D405 Y8)
K = np.array([[647.5198, 0, 638.5343],
              [0, 647.5198, 369.7683],
              [0, 0, 1.0]], dtype=np.float64)
D = np.zeros(5)


def log_rot(R):
    """旋转矩阵 -> 旋转向量."""
    angle = np.arccos(np.clip((np.trace(R) - 1) / 2.0, -1, 1))
    if angle < 1e-9:
        return np.zeros(3)
    return angle / (2 * np.sin(angle)) * np.array([R[2, 1] - R[1, 2],
                                                   R[0, 2] - R[2, 0],
                                                   R[1, 0] - R[0, 1]])


def exp_rot(w):
    """旋转向量 -> 旋转矩阵."""
    theta = np.linalg.norm(w)
    if theta < 1e-9:
        return np.eye(3)
    wx = np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0]])
    return np.eye(3) + np.sin(theta) / theta * wx + (1 - np.cos(theta)) / theta**2 * wx @ wx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--min-rot-deg", type=float, default=5.0,
                    help="用于 AX=XB 的相邻相对旋转最小角(度)")
    ap.add_argument("--aprilgrid", default="config/aprilgrid_6x6_35mm.yaml")
    args = ap.parse_args()
    sess = Path(args.session).resolve()
    unit = sess / "left_hand"

    grid = load_aprilgrid_config(ROOT / args.aprilgrid)
    from aprilgrid import Detector
    det = Detector("t36h11")

    # 1. 提取相机位姿 (R_cb, t_cb) + 对应时间
    cam_rows = {}
    with (unit / "camera_ts.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            cam_rows[int(r["idx"])] = float(r["ts_mono"])

    # IMU 时间: 用 imu_ts.csv rx_mono (与相机同一 monotonic 时钟)
    imu_ts = []
    with (unit / "imu_ts.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            imu_ts.append(float(r["rx_mono"]))

    frames_dir = unit / "frames"
    poses = []  # (t, R_c, t_c)
    for idx, t in cam_rows.items():
        img = cv2.imread(str(frames_dir / f"{idx:06d}.jpg"), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        dets = det.detect(img)
        if len(dets) < 6:
            continue
        obj, ipts = [], []
        for d in dets:
            tid = int(d.tag_id)
            if tid >= grid["tagCols"] * grid["tagRows"]:
                continue
            cor = np.asarray(d.corners, dtype=np.float32).reshape(-1, 2)
            if cor.shape != (4, 2):
                continue
            row, col = tid // grid["tagCols"], tid % grid["tagCols"]
            pitch = grid["tagSize"] * (1 + grid["tagSpacing"])
            x0, y0 = col * pitch, row * pitch
            s = grid["tagSize"]
            obj.append(np.array([[x0, y0, 0], [x0+s, y0, 0],
                                 [x0+s, y0+s, 0], [x0, y0+s, 0]], np.float32))
            ipts.append(cor)
        if len(obj) < 6:
            continue
        ok, rvec, tvec = cv2.solvePnP(np.vstack(obj), np.vstack(ipts), K, D)
        if ok:
            R, _ = cv2.Rodrigues(rvec)
            poses.append((t, R, tvec.flatten()))
    poses.sort(key=lambda p: p[0])
    print(f"相机位姿: {len(poses)} 个")

    # 2. 读陀螺+加速度计, 相邻位姿间预积分 ΔR_I / Δp
    imu = []  # (t, gx,gy,gz rad/s, ax,ay,az g)
    with (unit / "imu.bin").open("rb") as f:
        while True:
            c = f.read(IMU_PACK_SIZE)
            if len(c) < IMU_PACK_SIZE:
                break
            ts, _cnt, gx, gy, gz, ax, ay, az, _t = struct.unpack(IMU_PACK_FMT, c)
            imu.append((ts, gx * DEG2RAD, gy * DEG2RAD, gz * DEG2RAD,
                        ax, ay, az))
    # 用 imu_ts.csv 把 fitted ts 换成 rx_mono (同一时钟)
    rx_map = []
    with (unit / "imu_ts.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            rx_map.append(float(r["rx_mono"]))
    assert len(rx_map) == len(imu), "imu.bin 与 imu_ts.csv 数量不一致"
    for i in range(len(imu)):
        imu[i] = (rx_map[i], imu[i][1], imu[i][2], imu[i][3],
                  imu[i][4], imu[i][5], imu[i][6])
    imu.sort(key=lambda x: x[0])

    imu_t = np.array([x[0] for x in imu])
    gyr_w = np.array([[x[1], x[2], x[3]] for x in imu])
    acc_g = np.array([[x[4], x[5], x[6]] for x in imu]) * 9.81  # g -> m/s2

    # 重力在 IMU 系: 加速度计均值 ≈ -g (运动平均抵消)
    g_imu = -acc_g.mean(axis=0)

    # 3. 相邻位姿对: A=ΔI(IMU), B=ΔC(相机), 用旋转差>阈值
    #    相机位姿 C = (R^T, -R^T t)  (solvePnP 给 board->cam, 取逆得 cam->world)
    pairs_A = []  # (ΔR_I, Δp_I)
    pairs_B = []  # (ΔR_C, Δt_C)
    used = 0
    for i in range(len(poses) - 1):
        t1, R1, tc1 = poses[i]
        t2, R2, tc2 = poses[i + 1]
        # 相机相对运动 B = C_i^{-1} C_j, 在 cam_i 系:
        #   B_R = R_i @ R_j^T,  B_t = t_i - R_i R_j^T t_j
        dRc = R1 @ R2.T
        ang = np.degrees(np.arccos(np.clip((np.trace(dRc) - 1) / 2, -1, 1)))
        if ang < args.min_rot_deg:
            continue
        # IMU 预积分 ΔR_I / Δp (imu_i 系), 减重力
        mask = (imu_t >= t1) & (imu_t <= t2)
        if mask.sum() < 5:
            continue
        idx = np.where(mask)[0]
        R_prev = np.eye(3)
        dv = np.zeros(3)
        dp = np.zeros(3)
        for m in range(len(idx) - 1):
            k, kn = idx[m], idx[m + 1]
            dt_k = imu_t[kn] - imu_t[k]
            w = gyr_w[k]
            a = acc_g[k] - g_imu
            R_prev = R_prev @ exp_rot(w * dt_k)
            dv += R_prev @ a * dt_k
            dp += dv * dt_k
        pairs_A.append((R_prev, dp))
        # B_t = 相机位移在 cam_i 系 (B = C_i^{-1} C_j)
        Bt = tc1 - dRc @ tc2
        pairs_B.append((dRc, Bt))
        used += 1
    print(f"AX=XB 约束对: {used} 个 (旋转>{args.min_rot_deg}°)")

    if used < 5:
        print("约束太少, 无法标定")
        return 1

    # 4. 解 R_X: AX=XB 旋转部分. log(R_A) = R_X @ log(R_B) (同角, 轴经 R_X 映射)
    A = []
    b = []
    for (dR_I, _), (dRc, _) in zip(pairs_A, pairs_B):
        wA = log_rot(dR_I)
        wB = log_rot(dRc)
        if np.linalg.norm(wB) < 1e-6 or np.linalg.norm(wA) < 1e-6:
            continue
        ax = wA / np.linalg.norm(wA)
        bx = wB / np.linalg.norm(wB)
        for j in range(3):
            row = np.zeros(9)
            row[j*3:(j+1)*3] = bx
            A.append(row)
            b.append(ax[j])
    A = np.array(A)
    b = np.array(b)
    RX_flat, *_ = np.linalg.lstsq(A, b, rcond=None)
    RX = RX_flat.reshape(3, 3)
    U, _, Vt = np.linalg.svd(RX)
    RX = U @ Vt
    print(f"R_X (T_imu_cam 旋转):\n{np.round(RX, 5)}")

    # 5. 解 t_X: (A_R - I) t_x = R_X B_t - A_t  (对每对约束)
    A = []
    b = []
    for (dR_I, dp_I), (dRc, Bt) in zip(pairs_A, pairs_B):
        M = dR_I - np.eye(3)
        rhs = RX @ Bt - dp_I
        A.append(M)
        b.append(rhs)
    A = np.vstack(A)
    b = np.hstack(b)
    TX, *_ = np.linalg.lstsq(A, b, rcond=None)
    print(f"t_X (T_imu_cam 平移): {np.round(TX, 5)}")

    # 6. 输出 X = (RX, TX), 以及 VINS body_T_cam0 = R_g @ X (重力Y->Z组合)
    print("\n===== 结果 =====")
    print("X (T_imu_cam) = T_imu_cam0 flatten:")
    X = np.eye(4)
    X[:3, :3] = RX
    X[:3, 3] = TX
    print(" ".join(f"{v:.8f}" for v in X.flatten()))
    # 重力变换
    Rg = np.array([[0.99980212, -0.01423891, -0.01389161],
                   [-0.01423891, -0.02458715, -0.99959628],
                   [0.01389161,  0.99959628, -0.02478503]])
    body = np.eye(4)
    body[:3, :3] = Rg @ RX
    body[:3, 3] = Rg @ TX
    print("body_T_cam0 (R_g @ X):")
    print(" ".join(f"{v:.8f}" for v in body.flatten()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
