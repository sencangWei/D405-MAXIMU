#!/usr/bin/env python3
"""常量失配（位置系 vs 姿态系不一致）是**在哪一环出生**的。

## 约定探针的更正（2026-09-20）

`alignment_conditioning.py` 的 ⓪ 探针给出（现在是对的）：

    只有世界旋转 M          门 0.000°  纯漂移 0.000°  常量失配 0.000°  ← 世界旋转被完全吸收
    只有常量体轴偏移 C=1.5°  门 1.500°  纯漂移 1.499°  常量失配 0.060°  ← 常量偏移进【漂移】列

⇒ 之前 README 把第三列叫「常量失配」是**错的**：一个常量体轴姿态偏移表现为
  `attitude_aligned_ate_rotation_rmse_deg` 那一列，不是 `position_vs_attitude_alignment`。

那第三列（1.4–3.0°）到底是什么？它是**位置导出的世界旋转**与**姿态导出的世界旋转**
的夹角。既然一个自洽的世界旋转会被完全吸收，这一列非零就意味着
**估计的位置与估计的姿态不在同一个世界系里**（不一致 1.4–3.0°）。

本脚本沿链条逐环评测，看这个不一致在哪一环出现：
    vio_raw → vio_corrected_stream → frames → imu_metric → graph → fused
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")

CHAIN = [
    ("vins_raw", "docker2_slam/vio_raw.csv"),
    ("vins_corrected", "docker2_slam/vio_corrected_stream.csv"),
    ("[6/8] frames", "fusion/tight/mast3r/trajectory_frames.csv"),
    ("[6/8] imu_metric", "fusion/tight/mast3r/trajectory_imu_metric.csv"),
    ("[7/8] graph", "fusion/tight/mast3r/trajectory_graph.csv"),
    ("[8/9] fused", "fusion_current/tight/trajectory_fused.csv"),
]


def evaluate(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return E.pose_errors(Pe[inside][valid], Qe[inside][valid],
                         ctx[:, 1:4], iq, 30)


def main():
    groups = sorted(ROOT.glob("**/fusion_current/tight/trajectory_fused.csv"))
    groups = [g.parents[2] for g in groups
              if (g.parents[2] / "lighthouse_body_ground_truth.csv").exists()]
    print(f"逐环评测 {len(groups)} 个 take\n")
    totals = {}
    for g in groups:
        gt = g / "lighthouse_body_ground_truth.csv"
        name = str(g).replace(str(ROOT) + "/", "")
        print(f"--- {name} ---")
        print(f"  {'环节':<18}{'门(rot)':>9}{'漂移':>8}{'位置×姿态':>11}"
              f"{'ATE mm':>9}")
        for label, rel in CHAIN:
            est = g / rel
            if not est.exists():
                print(f"  {label:<18}{'缺':>9}")
                continue
            m = evaluate(est, gt)
            print(f"  {label:<18}{m['ate_rotation_rmse_deg']:>9.2f}"
                  f"{m['attitude_aligned_ate_rotation_rmse_deg']:>8.2f}"
                  f"{m['position_vs_attitude_alignment_rotation_deg']:>11.2f}"
                  f"{m['ate_translation_rmse_m']*1e3:>9.2f}")
            totals.setdefault(label, []).append(
                m["position_vs_attitude_alignment_rotation_deg"])
        print()
    print("=== 第三列（位置系 vs 姿态系不一致）逐环汇总 ===")
    for label, _ in CHAIN:
        v = totals.get(label)
        if not v:
            continue
        print(f"  {label:<18} 中位 {np.median(v):>5.2f}°  "
              f"范围 {min(v):.2f}–{max(v):.2f}°  n={len(v)}")


if __name__ == "__main__":
    main()
