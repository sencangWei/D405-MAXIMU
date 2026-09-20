#!/usr/bin/env python3
"""ATE 的**阶段预算**：融合链每一步各贡献多少位置误差。

## 为什么做

`ab_0914_vs_now.py` 证明**融合尾段参数不决定 ATE**（09-14 与现役 ATE 打平 5.72/5.77mm）。
那 5.7mm 只能在更上游诞生。这个 cell 的 mast3r/ 目录把每一步都留了盘：

  [1/8] trajectory_frames.csv         前端原始（MASt3R 单位，非度量）
  [6/8] trajectory_stereo_*.csv       四个标量候选（各自一个尺度）
        trajectory_imu_metric.csv     选中的那个 × 标量
  [7/8] trajectory_graph.csv          图优化输出
  [8/9] trajectory_fused*.csv         融合输出

逐个喂给官方评测器，看误差**从哪一步开始出现、哪一步被消掉**。

## 口径

`rigid_align`（无尺度）⇒ 非度量阶段 ATE 会很大，那是**单位问题不是误差**，
所以同时报 `sim3_diagnostic_scale_gt_per_estimate`（真值/估计 的尺度比，≈1 才是度量）。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
STAGES = ("trajectory_frames.csv", "trajectory_online_frames.csv",
          "trajectory_stereo_bidirectional.csv", "trajectory_stereo_long_hops.csv",
          "trajectory_stereo_dense10hz.csv", "trajectory_stereo_multisecond.csv",
          "trajectory_imu_metric.csv", "trajectory_graph.csv")


def metrics(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, plt, qlt = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    m = E.pose_errors(Pe[inside][valid], Qe[inside][valid], plt[:, 1:4], qlt, 30)
    return (m["ate_translation_rmse_m"] * 1000.0,
            m["ate_translation_p95_m"] * 1000.0,
            m["ate_translation_max_m"] * 1000.0,
            m["sim3_diagnostic_scale_gt_per_estimate"])


def main():
    cells = sorted({f.parents[3] for f in ROOT.glob("**/fusion/tight/mast3r/trajectory_graph.csv")})
    print(f"ATE 阶段预算（{len(cells)} cell，rigid_align 无尺度）")
    print("倍率 = 真值尺度/估计尺度（1.00 = 已是度量）；非度量阶段的 ATE 不可直接读\n")
    for c in cells:
        gt = c / "lighthouse_body_ground_truth.csv"
        md = c / "fusion/tight/mast3r"
        if not gt.exists():
            continue
        print(f"### {str(c).replace(str(ROOT)+'/', '')}")
        print(f"    {'阶段':<38}{'ATE':>8}{'p95':>7}{'max':>7}{'倍率':>8}")
        for s in STAGES:
            p = md / s
            if not p.exists():
                continue
            try:
                a, p95, mx, sc = metrics(p, gt)
            except Exception as e:                                   # noqa: BLE001
                print(f"    {s:<38}  ! {e}")
                continue
            flag = "" if abs(sc - 1) < 0.02 else "  ← 非度量"
            print(f"    {s:<38}{a:>8.1f}{p95:>7.1f}{mx:>7.1f}{sc:>8.3f}{flag}")
        for tag, rel in (("fusion_unsmoothed", "fusion/tight/trajectory_fused_unsmoothed.csv"),
                         ("fusion_final", "fusion/tight/trajectory_fused.csv"),
                         ("VINS_corrected", "docker2_slam/vio_corrected_stream.csv"),
                         ("VINS_raw", "docker2_slam/vio_raw.csv")):
            p = c / rel
            if not p.exists():
                continue
            try:
                a, p95, mx, sc = metrics(p, gt)
            except Exception as e:                                   # noqa: BLE001
                print(f"    {tag:<38}  ! {e}")
                continue
            print(f"    {tag:<38}{a:>8.1f}{p95:>7.1f}{mx:>7.1f}{sc:>8.3f}")
        print()


if __name__ == "__main__":
    main()
