#!/usr/bin/env python3
"""看真值/估计在超限区间的原始形状: 是估计鼓包, 还是真值跳变(tracker换分支)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

B = Path(sys.argv[1] if len(sys.argv) > 1 else
         "/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/"
         "20260914_validation_v11_holdout_batch3/group1")

est_t, est_p, est_q = E.load_trajectory(B / "fusion" / "trajectory_fused.csv")
gt_t, gt_p, gt_q = E.load_trajectory(B / "lighthouse_body_ground_truth.csv")
print(f"估计 {len(est_t)} 点, 真值 {len(gt_t)} 点")

# 真值自身逐帧位移(找跳变)
gt_step = np.linalg.norm(np.diff(gt_p, axis=0), axis=1) * 1000.0
print(f"\n真值逐帧位移: 中位 {np.median(gt_step):.3f} mm, "
      f"p99 {np.percentile(gt_step,99):.3f} mm, 最大 {gt_step.max():.3f} mm")
top = np.argsort(gt_step)[::-1][:15]
print("真值最大的15次逐帧位移:")
for i in sorted(top.tolist()):
    print(f"   t={gt_t[i]:.3f}  步长 {gt_step[i]:7.3f} mm  "
          f"(前后各1帧: {gt_step[i-1] if i>0 else float('nan'):.3f} / "
          f"{gt_step[i+1] if i+1<len(gt_step) else float('nan'):.3f})")

# 估计自身逐帧位移
est_step = np.linalg.norm(np.diff(est_p, axis=0), axis=1) * 1000.0
print(f"\n估计逐帧位移: 中位 {np.median(est_step):.3f} mm, "
      f"p99 {np.percentile(est_step,99):.3f} mm, 最大 {est_step.max():.3f} mm")

# 聚焦 36s 附近
t0 = est_t[0]
print("\n--- 估计轨迹 t 相对 35.80..36.40s (下标 1074..1092) ---")
for i in range(1074, 1093):
    if i >= len(est_t):
        break
    d = np.linalg.norm(est_p[i] - est_p[i-1]) * 1000
    print(f"  #{i:4d} rel={est_t[i]-t0:7.3f}s  est_step={d:7.3f} mm  "
          f"p=[{est_p[i][0]: .4f} {est_p[i][1]: .4f} {est_p[i][2]: .4f}]")

# 真值在同一时间窗内 (最近邻)
print("\n--- 真值 t 相对 35.80..36.40s ---")
lo, hi = t0 + 35.80, t0 + 36.40
sel = np.where((gt_t >= lo) & (gt_t <= hi))[0]
prev = None
for i in sel:
    d = np.linalg.norm(gt_p[i] - gt_p[i-1]) * 1000 if i > 0 else float("nan")
    flag = "  <== 跳变" if d > 8 else ""
    print(f"  gt#{i:4d} rel={gt_t[i]-t0:7.3f}s  gt_step={d:7.3f} mm  "
          f"p=[{gt_p[i][0]: .4f} {gt_p[i][1]: .4f} {gt_p[i][2]: .4f}]{flag}")
    prev = i
