#!/usr/bin/env python3
"""VINS 与 MASt3R 之间是否存在相对时间偏移?

若两条链只差一个时间 τ, 那它们的分歧就正比于速度 —— 与观测到的
"跟着速度走的宽带包"完全吻合。扫描 τ 最小化 V↔M 分歧:
  某个非零 τ 显著降低分歧  => 两链错位, 融合里的时间对齐项该修
  τ*≈0 且曲线平              => 分歧是真实的几何/算法差异

同时报出曲线形状(不平就说明不是一个 τ 能解释的)。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SURVEY = json.loads(Path("/tmp/claude-1000/stereoab/survey_all.json").read_text())
TAUS = np.arange(-0.10, 0.1001, 0.01)


def vm_agree(V, M, tau):
    """把 M 平移 τ 后与 V 比。返回 rmse(mm) 与 max(mm), 以及对齐后的残差序列。"""
    tv, pv = V
    tm, pm = M
    tt = np.union1d(tv, tm + tau)
    tt = tt[(tt >= max(tv[0], tm[0] + tau)) & (tt <= min(tv[-1], tm[-1] + tau))]
    if len(tt) < 20:
        return None
    P = np.column_stack([np.interp(tt, tv, pv[:, i]) for i in range(3)])
    Q = np.column_stack([np.interp(tt - tau, tm, pm[:, i]) for i in range(3)])
    R, t = E.rigid_align(P, Q)
    d = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
    return dict(rmse=float(np.sqrt(np.mean(d ** 2))),
                p95=float(np.percentile(d, 95)), mx=float(d.max()),
                t=tt, res=d)


def main():
    rows = []
    print(f"{'组':<44}{'τ=0 rmse':>10}{'τ* ':>8}{'τ* rmse':>9}{'降幅':>8}"
          f"{'τ=0 max':>10}{'τ* max':>9}{'降幅':>8}")
    print("-" * 108)
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        v = g / "docker2_slam" / "vio_corrected_stream.csv"
        ms_p = next((g / "fusion" / s / "mast3r" / "trajectory_imu_metric.csv"
                     for s in ("sparse", "tight")
                     if (g / "fusion" / s / "mast3r" / "trajectory_imu_metric.csv").is_file()),
                    None)
        if not (v.is_file() and ms_p):
            continue
        V, M = E.load_trajectory(v)[:2], E.load_trajectory(ms_p)[:2]
        curve = {}
        for tau in TAUS:
            r = vm_agree(V, M, float(tau))
            if r:
                curve[round(float(tau), 3)] = (r["rmse"], r["mx"])
        if not curve:
            continue
        base = curve[0.0]
        bd = min(curve, key=lambda d: curve[d][0])
        best = curve[bd]
        rows.append(dict(group=m["group"], base_rmse=base[0], base_mx=base[1],
                         tau=bd, best_rmse=best[0], best_mx=best[1],
                         curve={str(k): v for k, v in curve.items()}))
        print(f"{m['group']:<44}{base[0]:>10.2f}{bd*1000:>+7.0f}ms{best[0]:>9.2f}"
              f"{best[0]-base[0]:>+8.2f}{base[1]:>10.2f}{best[1]:>9.2f}"
              f"{best[1]-base[1]:>+8.2f}")

    Path("/tmp/claude-1000/stereoab/vm_offset_scan.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    taus = np.array([r["tau"] for r in rows]) * 1000
    gain = np.array([r["base_rmse"] - r["best_rmse"] for r in rows])
    print(f"\nV↔M 最优 τ*: 中位 {np.median(taus):+.1f}ms  范围 [{taus.min():+.0f}, {taus.max():+.0f}]ms")
    print(f"τ* 带来的 rmse 降幅: 中位 {np.median(gain):.2f}mm  "
          f"(相对于 τ=0 的中位 rmse {np.median([r['base_rmse'] for r in rows]):.2f}mm)")
    print("\n曲线(前 3 组, τ ms : rmse mm) —— 平则说明不是一个偏移能解释的:")
    for r in rows[:3]:
        c = {float(k) * 1000: v[0] for k, v in r["curve"].items()}
        print(f"  {r['group']}")
        print("   " + "  ".join(f"{t:+.0f}:{c[t]:.1f}" for t in sorted(c)))


if __name__ == "__main__":
    main()
