#!/usr/bin/env python3
"""A/B：09-14 融合参数（fusion/）vs 09-20 现役参数（fusion_current/），逐 cell 比 ATE/门/姿态。

## 背景

`fusion/` 的 mtime = 2026-09-14 22:03，是**原始 09-14 产物**；`fusion_current/` 是 09-20 重跑。
两者差三个参数（见 README）。这个脚本只做一件事：**谁更准**。

## 口径

只报融合链。三个量：ATE(mm，位置)、门(位置口径 rot)、姿态口径 rot。
判据：ATE 是最终目标（证伪检验已证门≈伪装的位置门）。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
VARIANTS = (("0914", "fusion/tight/trajectory_fused.csv"),
            ("now", "fusion_current/tight/trajectory_fused.csv"))


def metrics(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, plt, qlt = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    m = E.pose_errors(Pe[inside][valid], Qe[inside][valid], plt[:, 1:4], qlt, 30)
    return (m["ate_translation_rmse_m"] * 1000.0,
            m["ate_translation_p95_m"] * 1000.0,
            m["ate_translation_max_m"] * 1000.0,
            m["ate_rotation_rmse_deg"],
            m["attitude_aligned_ate_rotation_rmse_deg"])


def main():
    cells = sorted({f.parents[2] for f in ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")})
    cells = [c for c in cells if (c / "fusion/tight/trajectory_fused.csv").exists()]
    print(f"A/B：09-14 原始参数 vs 09-20 现役参数（{len(cells)} cell）\n")
    print(f"{'cell':<44}" + "".join(f"{v:>26}" for v, _ in VARIANTS))
    print(f"{'':<44}" + "".join(f"{'ATE   p95   max  门/姿态':>26}" for _ in VARIANTS))
    print("-" * 100)
    acc = {v: [] for v, _ in VARIANTS}
    for c in cells:
        gt = c / "lighthouse_body_ground_truth.csv"
        if not gt.exists():
            continue
        row = []
        for v, rel in VARIANTS:
            try:
                m = metrics(c / rel, gt)
                acc[v].append(m)
            except Exception as e:                                   # noqa: BLE001
                print(f"  ! {c.name}/{v}: {e}")
                m = (float("nan"),) * 5
            row.append(m)
        name = str(c).replace(str(ROOT) + "/", "")
        print(f"{name:<44}" + "".join(
            f"{m[0]:>7.1f}{m[1]:>6.1f}{m[2]:>6.1f}{m[3]:>5.2f}/{m[4]:>4.2f}" for m in row))
        d = row[1][0] - row[0][0]
        print(f"{'':<44}  ΔATE = {d:+.1f} mm   {'现役更好' if d < 0 else '09-14 更好'}")
    print("\n=== 汇总（中位）===")
    for v, _ in VARIANTS:
        A = np.array(acc[v])
        print(f"  [{v}] ATE {np.median(A[:,0]):6.2f}  p95 {np.median(A[:,1]):6.2f}  "
              f"max {np.median(A[:,2]):6.2f} mm | 门 {np.median(A[:,3]):5.2f}°  "
              f"姿态 {np.median(A[:,4]):5.2f}° | 门过 {int((A[:,3]<2.0).sum())}/{len(A)}")
    a, b = (np.array(acc[v])[:, 0] for v, _ in VARIANTS)
    print(f"\n  ATE 逐 cell：现役更好 {int((b<a).sum())}/{len(a)}，09-14 更好 {int((b>a).sum())}")
    print(f"  ATE 相对变化 中位 {np.median((b-a)/a)*100:+.1f}%")


if __name__ == "__main__":
    main()
