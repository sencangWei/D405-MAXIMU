#!/usr/bin/env python3
"""第三列（位置系×姿态系不一致）会不会是**文件内部**的位置列与姿态列时间错位。

## 为什么这条没被前面的 τ 扫描排掉

`rotation_gate_decomposition.py` 的 ④ 把**整条估计**在时间上平移 τ 再评，
第三列纹丝不动。但那只排除了「估计整体 vs 真值」的错位，
**没有**排除「同一个 CSV 里位置列与姿态列彼此错位」。

若 VINS 把 `Ps` 与 `Rs` 从不同时刻的状态写出（或 CSV 转换时用了不同的插值），
姿态相对位置就整体转过 ω·Δt。峰值角速度 ~25°/s 时，Δt=50ms 就是 1.25°，
正好是第三列的量级 —— **而且它与「常量」在度量上同形**。

## 做法

只把姿态列重采样到 `t - Δt`，位置列不动，扫 Δt 看第三列怎么变。
若某个 **跨 take 一致** 的 Δt 把第三列压到 ≈0，就抓到了。
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
TARGETS = [("vins_raw", "docker2_slam/vio_raw.csv"),
           ("fused", "fusion_current/tight/trajectory_fused.csv")]


def three(Pe, Qe, Pg, Qg):
    m = E.pose_errors(Pe, Qe, Pg, Qg, 30)
    return (m["ate_rotation_rmse_deg"],
            m["attitude_aligned_ate_rotation_rmse_deg"],
            m["position_vs_attitude_alignment_rotation_deg"])


def shift_attitude(t, Q, dt):
    """把姿态列重采样到 t - dt（位置列不动）。"""
    return Slerp(t, Rotation.from_quat(Q))(np.clip(t - dt, t[0], t[-1])).as_quat()


def main():
    groups = [f.parents[2] for f in sorted(
        ROOT.glob("**/fusion_current/tight/trajectory_fused.csv"))]
    deltas = np.arange(-0.10, 0.10 + 1e-9, 0.01)

    for label, rel in TARGETS:
        print(f"\n{'='*90}\n### {label}\n{'='*90}")
        print(f"{'cell':<40}" + "".join(f"{d*1e3:>7.0f}" for d in deltas))
        print(f"{'(Δt ms →)':<40}" + "".join("  第三列" for _ in deltas))
        print("-" * 90)
        bests = []
        for g in groups:
            est = g / rel
            gt = g / "lighthouse_body_ground_truth.csv"
            if not (est.exists() and gt.exists()):
                continue
            name = str(g).replace(str(ROOT) + "/", "")
            te, Pe, Qe = E.load_trajectory(est)
            tg, Pg, Qg = E.load_trajectory(gt)
            inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
            P = Pe[inside][valid]
            Q = Qe[inside][valid]
            t = ctx[:, 0]
            Pgt, Qgt = ctx[:, 1:4], iq
            vals, row = [], []
            for d in deltas:
                Qs = shift_attitude(t, Q, d)
                v = three(P, Qs, Pgt, Qgt)[2]
                vals.append(v)
                row.append(f"{v:>7.2f}")
            vals = np.array(vals)
            bests.append((name, deltas[int(np.argmin(vals))] * 1e3,
                          vals[0], vals.min()))
            print(f"{name:<40}" + "".join(row))
        print("\n  最优 Δt：")
        for name, bdt, v0, vmin in bests:
            print(f"    {name:<40} Δt*={bdt:>6.0f}ms  第三列 {v0:.2f} → {vmin:.2f}")

    # 汇总：把 Δt 当成一个全局常数，取各 take 最优 Δt 的分布
    print(f"\n{'='*90}\n### 结论\n{'='*90}")
    for label, rel in TARGETS:
        bds = []
        for g in groups:
            est, gt = g / rel, g / "lighthouse_body_ground_truth.csv"
            if not (est.exists() and gt.exists()):
                continue
            te, Pe, Qe = E.load_trajectory(est)
            tg, Pg, Qg = E.load_trajectory(gt)
            inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
            P, Q, t = Pe[inside][valid], Qe[inside][valid], ctx[:, 0]
            v = [three(P, shift_attitude(t, Q, d), ctx[:, 1:4], iq)[2] for d in deltas]
            bds.append(deltas[int(np.argmin(v))] * 1e3)
        bds = np.array(bds)
        print(f"  {label:<10} 各 take 最优 Δt：中位 {np.median(bds):+.0f}ms  "
              f"范围 {bds.min():+.0f}–{bds.max():+.0f}ms  "
              f"同号的 take {int((np.sign(bds) == np.sign(np.median(bds))).sum())}/{len(bds)}")


if __name__ == "__main__":
    main()
