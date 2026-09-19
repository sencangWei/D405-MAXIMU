#!/usr/bin/env python3
"""直接量 R_a（位置导出的对齐旋转）在**真实数据**上的不确定度 —— 不做任何噪声模型。

## 为什么不用合成

`alignment_conditioning.py` 的缩放律是**合成**的：给 GT 加各向同性噪声再对齐。
但真实的位置误差是**系统性的形状畸变**，不是噪声；而且真实 R_a 的偏差是那一次
具体实现，不是从分布里随机抽的。所以「中位 0.78°」只能说明**典型**情况。

这里换成一个无模型的问法：**把轨迹切成若干段，每段单独解 R_a，看它们彼此差多少。**

  - 若各段 R_a 彼此只差 ~0.1°  ⇒ R_a 是**良定**的，第三列（1.4–3.0°）不是它造成的，
                                账要记在姿态侧；
  - 若各段 R_a 彼此差 ~2°      ⇒ R_a 本身就**没定住**，「姿态误差」是错觉。

同样处理 R_q（姿态导出的对齐旋转，`orientation_align`）作对照 —— 谁的段间散度大，
谁就是第三列的来源。
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def load(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], Qe[inside][valid], ctx[:, 1:4], iq


def R_a(Pe, Pg):
    return Rotation.from_matrix(E.rigid_align(Pe, Pg)[0])


def R_q(Qe, Qg):
    return Rotation.from_matrix(E.orientation_align(Qe, Qg))


def spread(Rs):
    """一组旋转相对其均值的夹角 RMS（度）。"""
    c = Rotation.concatenate(Rs).mean()
    return float(np.sqrt(np.mean(np.degrees((Rotation.concatenate(Rs) * c.inv())
                                            .magnitude()) ** 2)))


def bootstrap(R_fn, n, k=6, trials=60, seed=0):
    """把序列切成 k 段，每段单独解一次；报段间的散度与各段对全长的夹角。"""
    rng = np.random.default_rng(seed)
    edges = np.linspace(0, n, k + 1).astype(int)
    seg = [R_fn(s, e) for s, e in zip(edges[:-1], edges[1:]) if e - s > 50]
    full = R_fn(0, n)
    if not seg:
        return None
    d_full = [np.degrees((r * full.inv()).magnitude()) for r in seg]
    # 再做一个「随机半长块」重采样，看连续块之外的稳定性
    blocks = []
    for _ in range(trials):
        s = rng.integers(0, n // 2)
        blocks.append(R_fn(s, s + n // 2))
    d_half = [np.degrees((r * full.inv()).magnitude()) for r in blocks]
    return (spread(seg), float(np.median(d_full)), float(np.percentile(d_full, 90)),
            float(np.median(d_half)), float(np.percentile(d_half, 90)), len(seg))


def main():
    groups = [f.parents[2] for f in sorted(
        ROOT.glob("**/fusion_current/tight/trajectory_fused.csv"))]
    print("段间散度 = 6 段各自解出的对齐旋转相对其均值的夹角 RMS")
    print("对全长  = 各段解 vs 全长解 的夹角（中位 / p90）")
    print("对半长  = 60 个随机半长窗口解 vs 全长解 的夹角（中位 / p90）\n")
    for label, rel in (("vins_raw", "docker2_slam/vio_raw.csv"),
                       ("fused", "fusion_current/tight/trajectory_fused.csv")):
        print(f"{'='*104}\n### {label}\n{'='*104}")
        print(f"{'cell':<40}{'段间散度':>9}│{'R_a 对全长':>22}│{'R_q 对全长':>22}")
        print(f"{'':<40}{'':>9}│{'中位':>10}{'p90':>12}│{'中位':>10}{'p90':>12}")
        print("-" * 104)
        for g in groups:
            est, gt = g / rel, g / "lighthouse_body_ground_truth.csv"
            if not (est.exists() and gt.exists()):
                continue
            name = str(g).replace(str(ROOT) + "/", "")
            Pe, Qe, Pg, Qg = load(est, gt)
            n = len(Pe)
            ra = bootstrap(lambda s, e: R_a(Pe[s:e], Pg[s:e]), n)
            rq = bootstrap(lambda s, e: R_q(Qe[s:e], Qg[s:e]), n)
            if ra is None or rq is None:
                continue
            print(f"{name:<40}{ra[0]:>9.2f}│{ra[1]:>10.2f}{ra[2]:>12.2f}"
                  f"│{rq[1]:>10.2f}{rq[2]:>12.2f}")
        print()

    print("=== 对照：第三列（位置系×姿态系）实测值 ===")
    for g in groups:
        gt = g / "lighthouse_body_ground_truth.csv"
        if not gt.exists():
            continue
        name = str(g).replace(str(ROOT) + "/", "")
        Pe, Qe, Pg, Qg = load(g / "fusion_current/tight/trajectory_fused.csv", gt)
        m = E.pose_errors(Pe, Qe, Pg, Qg, 30)
        print(f"  {name:<44}{m['position_vs_attitude_alignment_rotation_deg']:>7.2f}°")


if __name__ == "__main__":
    main()
