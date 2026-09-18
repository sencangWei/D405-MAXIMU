#!/usr/bin/env python3
"""假设: 尾巴由"估计与真值之间的整体时间偏移"主导, 而非位姿解算误差。

做法: 给估计轨迹加一个时间偏移 δ, 重新对齐打分, 扫描 δ。
  - 若某个非零 δ 让 max/P95 大幅塌陷  => 残差是时间偏置, 可标定
  - 若最优 δ≈0 且曲线平坦           => 不是时间问题, 是真实几何误差
同时报告: 30ms 采样下 δ 的分辨率极限(速度×δ 就是位置代价)。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
    "20260916_fusion_v11_umi_only_batch",
]
DELTAS = np.arange(-0.060, 0.0601, 0.005)


def score(et, ep, eq, rt, rp, rq, delta):
    """把估计按 +δ 平移时间后打分(即用真值在 t+δ 处比对)。"""
    inside, valid, interp, iq = E.interpolate_ground_truth(
        et + delta, rt, rp, rq, 0.1)
    if valid.sum() < 10:
        return None
    sp, sq, G = ep[inside][valid], eq[inside][valid], interp[:, 1:]
    R, tr = E.rigid_align(sp, G)
    ate = np.linalg.norm(sp @ R.T + tr - G, axis=1) * 1000
    return dict(rmse=float(np.sqrt(np.mean(ate ** 2))),
                p95=float(np.percentile(ate, 95)),
                mx=float(ate.max()),
                w10=float(np.mean(ate <= 10.0) * 100))


def main():
    rows = []
    for b in BATCHES:
        for g in sorted((ROOT / b).glob("group*")):
            fu = g / "fusion" / "trajectory_fused.csv"
            gt = g / "lighthouse_body_ground_truth.csv"
            if not (fu.is_file() and gt.is_file()):
                continue
            et, ep, eq = E.load_trajectory(fu)
            rt, rp, rq = E.load_trajectory(gt)

            curve = {}
            for d in DELTAS:
                r = score(et, ep, eq, rt, rp, rq, d)
                if r:
                    curve[round(float(d), 3)] = r
            base = curve[0.0]
            best_d = min(curve, key=lambda d: curve[d]["mx"])
            best = curve[best_d]

            rows.append(dict(group=f"{b}/{g.name}", base=base, best_d=best_d,
                             best=best,
                             gain_mx=base["mx"] - best["mx"],
                             gain_rmse=base["rmse"] - best["rmse"],
                             curve=curve))
            print(f"  {b}/{g.name:<12} base max {base['mx']:>6.2f} → "
                  f"δ*={best_d*1000:>+6.1f}ms max {best['mx']:>6.2f}  "
                  f"(−{base['mx']-best['mx']:.2f}mm, rmse {base['rmse']:.2f}→{best['rmse']:.2f})")

    Path("/tmp/claude-1000/stereoab/time_offset_scan.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    print("\n" + "=" * 118)
    print(f"{'组':<44}{'基准max':>9}{'δ*':>9}{'δ*后max':>10}{'基准rmse':>10}"
          f"{'δ*后rmse':>10}{'基准P95':>9}{'δ*后P95':>10}")
    print("-" * 118)
    for r in sorted(rows, key=lambda r: -r["gain_mx"]):
        print(f"{r['group']:<44}{r['base']['mx']:>9.2f}{r['best_d']*1000:>+8.1f}ms"
              f"{r['best']['mx']:>10.2f}{r['base']['rmse']:>10.2f}"
              f"{r['best']['rmse']:>10.2f}{r['base']['p95']:>9.2f}{r['best']['p95']:>10.2f}")

    ds = np.array([r["best_d"] for r in rows]) * 1000
    print(f"\n最优 δ*: 中位 {np.median(ds):+.1f}ms  均值 {ds.mean():+.1f}ms  "
          f"范围 [{ds.min():+.1f}, {ds.max():+.1f}]ms")
    print(f"δ* 使 max 达标的组: {sum(1 for r in rows if r['best']['mx']<=10.0)}/{len(rows)}")
    print(f"基准就达标的组:     {sum(1 for r in rows if r['base']['mx']<=10.0)}/{len(rows)}")


if __name__ == "__main__":
    main()
