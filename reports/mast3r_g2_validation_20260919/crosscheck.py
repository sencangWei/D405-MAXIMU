#!/usr/bin/env python3
"""对任意 group 目录做: 定位 ATE 峰值 + 多链路速度剖面交叉判据 (无真值依赖的独立性检验).
用法: crosscheck.py <group_dir>
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

G = Path(sys.argv[1])
gt_p_csv = G / "lighthouse_body_ground_truth.csv"
fused = G / "fusion" / "trajectory_fused.csv"
if not fused.exists():
    fused = G / "trajectory_fused.csv"

rt, rp, rq = E.load_trajectory(gt_p_csv)
et, ep, eq = E.load_trajectory(fused)
inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
sp, sq, Gp = ep[inside][valid], eq[inside][valid], interp[:, 1:]
R, t = E.rigid_align(sp, Gp)
ate = np.linalg.norm(sp @ R.T + t - Gp, axis=1)

print(f"===== {G.name} =====")
print(f"样本 {len(ate)}  RMSE {np.sqrt(np.mean(ate**2))*1000:.3f}  "
      f"P95 {np.percentile(ate,95)*1000:.3f}  Max {ate.max()*1000:.3f} mm  "
      f"超10mm {int((ate>0.01).sum())} 个")
peak = int(np.argmax(ate))
print(f"峰值在样本 #{peak}  rel={et[peak]-et[0]:.3f}s  ATE={ate[peak]*1000:.3f} mm")

# 峰值附近速度剖面: 各独立链路
srcs = {
    "真值GT      ": gt_p_csv,
    "Docker2 VINS": G / "docker2_slam" / "vio_corrected_stream.csv",
    "MASt3R      ": G / "fusion" / "sparse" / "mast3r" / "trajectory_imu_metric.csv",
    "融合fused    ": fused,
}
if not (G / "fusion" / "sparse" / "mast3r" / "trajectory_imu_metric.csv").exists():
    srcs["MASt3R      "] = G / "fusion" / "tight" / "mast3r" / "trajectory_imu_metric.csv"

lo, hi = et[peak] - et[0] - 0.45, et[peak] - et[0] + 0.45
grid = et[0] + np.arange(lo, hi, 0.1)
print(f"\n速度剖面 (mm/s)  rel {lo:.2f}..{hi:.2f}s")
print(f"{'链路':<14}" + "".join(f"{x-et[0]:>7.1f}" for x in grid))
for nm, p in srcs.items():
    if not Path(p).exists():
        print(f"{nm:<14} (缺失)")
        continue
    tt, pp, _ = E.load_trajectory(Path(p))
    v = np.linalg.norm(np.diff(pp, axis=0), axis=1) / np.diff(tt) * 1000.0
    tc = (tt[1:] + tt[:-1]) / 2
    vi = np.interp(grid, tc, v)
    print(f"{nm:<14}" + "".join(f"{x:>7.1f}" for x in vi)
          + f"   波动 {vi.max()/max(vi.min(),1e-6):5.2f}×")

# 峰值窗口内真值 vs 估计的位移账
w = slice(max(0, peak - 5), min(len(ate), peak + 6))
gt_step = np.linalg.norm(np.diff(rp, axis=0), axis=1)
est_step = np.linalg.norm(np.diff(ep, axis=0), axis=1)
print(f"\n峰值±5帧位移账:  真值 {gt_step[w].sum()*1000:.2f} mm   估计 {est_step[w].sum()*1000:.2f} mm"
      f"   (差 {(est_step[w].sum()-gt_step[w].sum())*1000:+.2f} mm)")
# 姿态误差是否同步
ar = np.degrees((E.Rotation.from_quat(iq).inv()
                 * (E.Rotation.from_matrix(R) * E.Rotation.from_quat(sq))).magnitude())
print(f"姿态误差: 全轨迹中位 {np.median(ar):.3f}°  |  峰值窗口 "
      f"{ar[max(0,peak-2):peak+3].mean():.3f}°  -> "
      + ("姿态同步异常" if ar[max(0,peak-2):peak+3].mean() > np.median(ar) * 1.5
         else "姿态无异常(纯平移型偏差)"))
