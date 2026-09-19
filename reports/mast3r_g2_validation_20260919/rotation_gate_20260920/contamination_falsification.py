#!/usr/bin/env python3
"""**证伪检验**：门随位置误差（ATE）变化，而姿态口径**不随**它变化。

## 为什么这是一个真正的检验

§四 的说法是「门 = 真姿态误差 ⊕ 位置污染」，机制是「位置解出的对齐旋转被套到姿态上」。
这个说法有一个**可以直接证伪**的推论：

  * 门用的对齐旋转来自**位置** ⇒ 位置误差越大，对齐越歪 ⇒ **门应随 ATE 单调上升**；
  * 姿态口径的对齐旋转来自**姿态**，位置完全不参与 ⇒ **它应与 ATE 无关**。

若两者都随 ATE 上升 ⇒ 「污染」解释不了，真实机理是「ATE 大的估计姿态也差」；
若两者都与 ATE 无关 ⇒ 两者量的是同一件事，分解无意义。

## 为什么要带上 vins_raw

只比 `fused` 的话 ATE 只有 3.9–9.1mm，杠杆臂太短，相关性可能被噪声淹没。
`vins_raw` 的 ATE 是 15–127mm（**10 倍跨度**），拿它当自变量，
「门 vs 姿态口径」的差别会非常清楚。

## 判据

  * `corr(门, ATE)` 显著为正、`corr(姿态口径, ATE)` 不显著 ⇒ **污染假设成立**；
  * 两者都显著 ⇒ 假设被证伪；
  * 顺带给出「要让门过 2.0° 需要 ATE 降到多少」的回归外推。
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation  # noqa: F401
from scipy.stats import pearsonr, spearmanr

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CHAINS = (("fused", "fusion_current/tight/trajectory_fused.csv"),
          ("vins_raw", "docker2_slam/vio_raw.csv"))


def load(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, plt, qlt = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], Qe[inside][valid], plt[:, 1:4], qlt


def metrics(Pe, Qe, Pg, Qg):
    """→ (ATE mm, 门, 姿态口径)。直接取验收脚本自己的字段，避免复实现走样。"""
    m = E.pose_errors(Pe, Qe, Pg, Qg, 30)
    return (m["ate_translation_rmse_m"] * 1000.0,
            m["ate_rotation_rmse_deg"],
            m["attitude_aligned_ate_rotation_rmse_deg"])


def report(tag, ate, gate, att):
    if len(ate) < 4:
        print(f"  {tag}: 样本不足（{len(ate)}）")
        return
    print(f"  {tag}（n={len(ate)}，ATE {ate.min():.1f}–{ate.max():.1f}mm）")
    for name, y in (("门（位置口径）", gate), ("姿态口径", att)):
        r, pr = pearsonr(ate, y)
        s, ps = spearmanr(ate, y)
        print(f"    {name:<14} Pearson r={r:+.3f} (p={pr:.4f})   "
              f"Spearman ρ={s:+.3f} (p={ps:.4f})")
        if name.startswith("门") and abs(r) > 0.3:
            k, b = np.polyfit(ate, y, 1)
            if k > 0:
                print(f"      ⇒ 线性外推：门 = 2.0° 对应 ATE ≈ {(2.0 - b) / k:.1f}mm")


def main():
    print("证伪检验：门应随 ATE 上升（位置参与对齐），姿态口径应**不随** ATE 变化\n")
    print("=" * 90)
    for tag, rel in CHAINS:
        ate, gate, att = [], [], []
        for g in sorted({f.parents[2] for f in
                         ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
            est, gt = g / rel, g / "lighthouse_body_ground_truth.csv"
            if not (est.exists() and gt.exists()):
                continue
            try:
                a, gv, av = metrics(*load(est, gt))
            except Exception as e:                                   # noqa: BLE001
                print(f"    (跳过 {g.name}: {e})")
                continue
            ate.append(a)
            gate.append(gv)
            att.append(av)
        report(f"[{tag}]", np.array(ate), np.array(gate), np.array(att))
        print()

    # 两种链合并 —— 杠杆臂最长，最能分辨
    ate, gate, att = [], [], []
    for g in sorted({f.parents[2] for f in
                     ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
        gt = g / "lighthouse_body_ground_truth.csv"
        for _, rel in CHAINS:
            est = g / rel
            if not (est.exists() and gt.exists()):
                continue
            try:
                a, gv, av = metrics(*load(est, gt))
            except Exception:                                        # noqa: BLE001
                continue
            ate.append(a)
            gate.append(gv)
            att.append(av)
    print("=" * 90)
    report("[两链合并]", np.array(ate), np.array(gate), np.array(att))

    print("\n判据：门随 ATE 显著上升 且 姿态口径不随 ⇒ 位置污染假设成立；")
    print("      两者都上升 ⇒ 假设被证伪（是「位置差的估计姿态也差」）。")


if __name__ == "__main__":
    main()
