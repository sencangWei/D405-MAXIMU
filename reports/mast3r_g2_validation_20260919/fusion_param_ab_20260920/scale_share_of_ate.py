#!/usr/bin/env python3
"""**残余尺度误差在 ATE 里占多少**？

## 动机

`ate_stage_budget.py` 发现：MASt3R 链全程 `倍率=真值/估计≈0.94`（轨迹大了 6%），
融合后回到 1.000。但融合后仍有残余（0.972–1.011）。

官方门用 `rigid_align` —— **不含尺度**。所以任何残余尺度误差都会被当成位置误差，
**线性**灌进 ATE：`ΔATE ≈ |s−1| × 轨迹到质心的 RMS 半径`。
本台架半径 ~15–20cm ⇒ **1% 尺度误差 ≈ 1.5–2mm ATE**，与 4mm 目标同量级。

## 做法

同一份输出，两种对齐各算一次 ATE：
  * `rigid` —— 官方口径（可以过门的那个）；
  * `sim3`  —— 允许尺度（Umeyama 带尺度）。
若 `ATE_sim3 ≪ ATE_rigid` ⇒ 差额**就是**残余尺度误差贡献的，且**可以直接修**（重标定尺度）。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def load(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, plt, qlt = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], plt[:, 1:4]


def main():
    print("残余尺度误差占 ATE 多少（融合链）\n")
    print(f"{'cell':<44}{'rigid':>7}{'sim3':>7}{'差额':>7}{'最优s':>8}{'半径':>7}{'预测Δ':>8}")
    print("-" * 92)
    rows = []
    for c in sorted({f.parents[2] for f in
                     ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
        p, gt = c / "fusion_current/tight/trajectory_fused.csv", c / "lighthouse_body_ground_truth.csv"
        if not (p.exists() and gt.exists()):
            continue
        Pe, Pg = load(p, gt)
        R, t = E.rigid_align(Pe, Pg)
        a_r = float(np.sqrt(np.mean(np.linalg.norm(Pe @ R.T + t - Pg, axis=1) ** 2)) * 1000)
        # 用仓库自带的 similarity_align（曾自写 Umeyama 漏除 n ⇒ 尺度 1731 倍）
        s, R2, t2 = E.similarity_align(Pe, Pg)
        a_s = float(np.sqrt(np.mean(np.linalg.norm(s * (Pe @ R2.T) + t2 - Pg, axis=1) ** 2)) * 1000)
        rad = float(np.sqrt(np.mean(np.linalg.norm(Pg - Pg.mean(0), axis=1) ** 2)) * 1000)
        pred = abs(s - 1) * rad
        name = str(c).replace(str(ROOT) + "/", "")
        print(f"{name:<44}{a_r:>7.2f}{a_s:>7.2f}{a_r-a_s:>7.2f}{s:>8.4f}{rad:>7.1f}{pred:>8.2f}")
        rows.append((a_r, a_s, s, rad))
    if not rows:
        return
    A = np.array(rows)
    print("\n=== 汇总（中位）===")
    print(f"  rigid ATE      {np.median(A[:,0]):6.2f} mm   ← 官方口径")
    print(f"  sim3  ATE      {np.median(A[:,1]):6.2f} mm")
    print(f"  尺度贡献       {np.median(A[:,0]-A[:,1]):6.2f} mm  "
          f"（占 rigid ATE 的 {100*np.median((A[:,0]-A[:,1])/A[:,0]):.0f}%）")
    print(f"  最优尺度 s     中位 {np.median(A[:,2]):.4f}  极差 "
          f"{A[:,2].min():.4f}–{A[:,2].max():.4f}（1.0 = 无残余尺度误差）")
    print(f"\n  ⚠ s 是在【真值】上求的 ⇒ 这是**上界**：真机上拿不到真值，")
    print("    但可以用 docker2/stereo 的比值去估（融合已经在做，只是没做准）。")


if __name__ == "__main__":
    main()
