#!/usr/bin/env python3
"""用 IMU 独立判定: 超限区间里身体到底动没动. 只读."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402
from fuse_mast3r_stereo_imu import load_calibrated_imu  # noqa: E402

B = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/"
         "20260914_validation_v11_holdout_batch3/group1")
S = Path("/home/robot/umi_ego_vio_data_device2_c48df736/recordings/"
         "d405_720p_rgb_stereo_ir_20260914_202142")
CAL = Path("/home/robot/ego_vio_humble/config/"
           "imu_runtime_accel_calibrated_raw_gyro_20260816.yaml")

imu_t, gyro, accel, info = load_calibrated_imu(S / "external_imu" / "imu.bin", CAL)
print(f"IMU {len(imu_t)} 采样, ts {imu_t[0]:.3f}..{imu_t[-1]:.3f}")
print(f"  采样间隔中位 {np.median(np.diff(imu_t))*1000:.3f} ms")

est_t, est_p, _ = E.load_trajectory(B / "fusion" / "trajectory_fused.csv")
gt_t, gt_p, _ = E.load_trajectory(B / "lighthouse_body_ground_truth.csv")
print(f"估计 ts {est_t[0]:.3f}..{est_t[-1]:.3f}")
print(f"真值 ts {gt_t[0]:.3f}..{gt_t[-1]:.3f}")

# 逐帧: 估计步长、真值步长、IMU 角速度模、加速度模(去重力后不易,先用原始模+角速度)
gyro_n = np.linalg.norm(gyro, axis=1)
accel_n = np.linalg.norm(accel, axis=1)

print(f"\nIMU 角速度模: 中位 {np.degrees(np.median(gyro_n)):.2f} °/s, "
      f"p95 {np.degrees(np.percentile(gyro_n,95)):.2f} °/s")
print(f"IMU 加速度模: 中位 {np.median(accel_n):.3f} m/s², "
      f"p5 {np.percentile(accel_n,5):.3f}, p95 {np.percentile(accel_n,95):.3f}")

t0 = est_t[0]
print("\n=== 超限区间 估计下标 1070..1095 ===")
print(f"{'#':>5} {'rel(s)':>8} {'est步(mm)':>10} {'gt步(mm)':>9} "
      f"{'IMU|ω|(°/s)':>12} {'IMU|a|(m/s²)':>13}")
for i in range(1070, 1096):
    if i >= len(est_t):
        break
    es = np.linalg.norm(est_p[i] - est_p[i-1]) * 1000
    # 真值该时刻最近邻
    j = int(np.argmin(np.abs(gt_t - est_t[i])))
    gs = np.linalg.norm(gt_p[j] - gt_p[j-1]) * 1000 if j > 0 else float("nan")
    # IMU 该帧曝光时刻附近的窗口(±1帧)
    w = (imu_t >= est_t[i] - 0.02) & (imu_t <= est_t[i] + 0.02)
    gw = np.degrees(np.median(gyro_n[w])) if w.any() else float("nan")
    aw = np.median(accel_n[w]) if w.any() else float("nan")
    mark = "  <== 超10mm" if (1080 <= i <= 1084) else ""
    print(f"{i:>5} {est_t[i]-t0:>8.3f} {es:>10.3f} {gs:>9.3f} "
          f"{gw:>12.3f} {aw:>13.3f}{mark}")

# 对照: 全轨迹上 IMU 角速度与估计速度的相关性
print("\n=== 全轨迹: 估计速度 vs IMU 角速度 (30帧滑窗) ===")
est_v = np.linalg.norm(np.diff(est_p, axis=0), axis=1) / np.diff(est_t) * 1000
gt_v = np.linalg.norm(np.diff(gt_p, axis=0), axis=1) / np.diff(gt_t) * 1000
# 把 IMU 角速度插值到帧时刻
gi = np.interp(est_t[1:], imu_t, np.degrees(gyro_n))
k = 30
def roll(a, k):
    return np.convolve(a, np.ones(k) / k, mode="valid")
ev, gv, gg = roll(est_v, k), roll(gt_v, k), roll(gi, k)
print(f"  corr(估计速度, IMU|ω|) = {np.corrcoef(ev, gg)[0,1]:.4f}")
print(f"  corr(真值速度, IMU|ω|) = {np.corrcoef(gv, gg)[0,1]:.4f}")
print(f"  corr(估计速度, 真值速度) = {np.corrcoef(ev, gv)[0,1]:.4f}")

# 定位超限窗在滑窗序列里的位置
idx = np.arange(len(ev)) + k // 2
sel = (idx >= 1080) & (idx <= 1084)
print(f"\n  超限窗内: 估计速度 {ev[sel].mean():.1f} mm/s, "
      f"真值速度 {gv[sel].mean():.1f} mm/s, IMU|ω| {gg[sel].mean():.2f} °/s")
for name, lo, hi in (("前1秒", 1050, 1075), ("后1秒", 1090, 1115)):
    s = (idx >= lo) & (idx <= hi)
    print(f"  {name}: 估计速度 {ev[s].mean():.1f} mm/s, "
          f"真值速度 {gv[s].mean():.1f} mm/s, IMU|ω| {gg[s].mean():.2f} °/s")
