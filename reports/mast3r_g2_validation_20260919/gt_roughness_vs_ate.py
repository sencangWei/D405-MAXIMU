#!/usr/bin/env python3
"""真值自身"毛糙度" vs ATE —— 判断 10mm 门到底在测算法还是在测真值噪声。

思路(比找 freeze-then-catchup 更本质):
  真值是一台光学 tracker 给的 30Hz 位置。它相对**自己的平滑趋势**就有抖动
  (freeze-then-catchup 只是其中一种形状)。
  如果一条估计轨迹跟的是"真值的平滑趋势", 那么它与**原始真值**的 ATE
  必然至少等于真值自身的抖动幅度 —— 那部分误差不是算法的锅。

做法:
  1. 对真值位置做 Savitzky-Golay 局部二次拟合(窗 15 帧 ≈ 0.5s)。
     二次多项式能保住直线/匀加速运动, 所以残差 = 真值的高频毛糙, 不是真运动。
  2. roughness = |GT_raw - GT_smooth| 的 p95 / max。
  3. 对比同组 ATE 的 p95 / max, 并给出 GT 平滑后重算的 ATE。
     —— 若"拿平滑真值打分"能把 max 压到门下, 说明失败主要是真值噪声。
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import savgol_filter

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
]
WIN, ORDER = 15, 2


def ate(est_xyz, est_q, est_t, gt_xyz, gt_q, gt_t):
    inside, valid, interp, iq = E.interpolate_ground_truth(
        est_t, gt_t, gt_xyz, gt_q, 0.1)
    if valid.sum() < 10:
        return None
    P, Q, Pq = est_xyz[inside][valid], interp[:, 1:], est_q[inside][valid]
    R, t = E.rigid_align(P, Q)
    d = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
    ang = np.degrees((E.Rotation.from_quat(iq).inv()
                      * (E.Rotation.from_matrix(R)
                         * E.Rotation.from_quat(Pq))).magnitude())
    return dict(rmse=float(np.sqrt(np.mean(d ** 2))), p95=float(np.percentile(d, 95)),
                mx=float(d.max()), w10=float(np.mean(d <= 10.0) * 100),
                rot=float(np.sqrt(np.mean(ang ** 2))))


rows = []
print(f"{'组/候选':<42}{'GT毛糙p95':>10}{'GT毛糙max':>10}"
      f"{'原ATE p95':>10}{'原ATE max':>10}{'平滑GT后 max':>13}")
print("-" * 96)
for b in BATCHES:
    for g in sorted((ROOT / b).glob("group*")):
        gt = g / "lighthouse_body_ground_truth.csv"
        if not gt.is_file():
            continue
        a = np.genfromtxt(gt, delimiter=",", names=True)
        gt_t = a["t_sec"]
        gt_xyz = np.column_stack([a["x"], a["y"], a["z"]])
        gt_q = np.column_stack([a["qw"], a["qx"], a["qy"], a["qz"]])
        sm = np.column_stack([savgol_filter(gt_xyz[:, i], WIN, ORDER)
                              for i in range(3)])
        rough = np.linalg.norm(gt_xyz - sm, axis=1) * 1000

        for sub in ("sparse", "tight"):
            f = g / "fusion" / sub / "trajectory_fused.csv"
            if not f.is_file():
                continue
            et, ep, eq = E.load_trajectory(f)
            r_raw = ate(ep, eq, et, gt_xyz, gt_q, gt_t)
            r_sm = ate(ep, eq, et, sm, gt_q, gt_t)
            if not r_raw:
                continue
            rows.append(dict(group=f"{b}/{g.name}", cand=sub,
                             gt_rough_p95_mm=float(np.percentile(rough, 95)),
                             gt_rough_max_mm=float(rough.max()),
                             raw=r_raw, smooth_gt=r_sm))
            print(f"{b.split('_')[-1]+'/'+g.name+'/'+sub:<42}"
                  f"{np.percentile(rough, 95):>10.2f}{rough.max():>10.2f}"
                  f"{r_raw['p95']:>10.2f}{r_raw['mx']:>10.2f}"
                  f"{(r_sm['mx'] if r_sm else float('nan')):>13.2f}")

out = Path(__file__).parent / "gt_roughness_vs_ate.json"
out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
gp = np.array([r["gt_rough_p95_mm"] for r in rows])
ap = np.array([r["raw"]["p95"] for r in rows])
print(f"\n{len(rows)} 项: GT 毛糙 p95 中位 {np.median(gp):.2f}mm, "
      f"ATE p95 中位 {np.median(ap):.2f}mm, 相关 {np.corrcoef(gp, ap)[0,1]:.2f}")
flip = [r for r in rows if r["raw"]["mx"] > 10 >= (r["smooth_gt"] or {}).get("mx", 1e9)]
print(f"改用平滑真值打分后 max 由 FAIL 转 PASS: {len(flip)}")
for r in flip:
    print(f"  {r['group']}/{r['cand']}: max {r['raw']['mx']:.2f} → {r['smooth_gt']['mx']:.2f}"
          f"  (GT 毛糙 max {r['gt_rough_max_mm']:.2f}mm)")
print(f"写入 {out}")
