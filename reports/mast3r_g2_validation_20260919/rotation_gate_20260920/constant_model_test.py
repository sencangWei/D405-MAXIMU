#!/usr/bin/env python3
"""能不能用**单一常量体轴姿态偏移 C** 解释整条旋转门（含两列的分配）。

## 动机

约定探针（`alignment_conditioning.py` ⓪）说：在**姿态分布铺满球面**时，
一个常量体轴偏移 C 会【全部】落进「纯漂移」列，第三列（位置×姿态）≈ 0。
但真实数据的姿态范围很窄（桌面小轨迹，姿态摆幅只有几十度），
此时 `orientation_align = mean(R_gt · R_est⁻¹)` 里那个共轭
`R_gt C⁻¹ R_gt⁻¹` **平均不掉**，于是 C 会**同时漏进两列**。

若成立 ⇒ 门、漂移、常量失配三个数其实是**同一个 C 的三种投影**，
「三个指标」是一个病；修法只有一个：把 C 消掉。

## 做法

用**真实 GT 的姿态分布**（窄），位置取完美，姿态 = Q_gt 右乘一个常量 C，
扫 |C|，看三列怎么随 |C| 变；再和真实的（漂移, 常量, 门）对照。
另外测一个「随时间漂移的姿态误差」作对照，看它能不能也凑出同样的分配。
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def load(fused):
    gt = Path(fused).parents[2] / "lighthouse_body_ground_truth.csv"
    te, Pe, Qe = E.load_trajectory(fused)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], Qe[inside][valid], ctx[:, 1:4], iq


def three(Pe, Qe, Pg, Qg):
    m = E.pose_errors(Pe, Qe, Pg, Qg, 30)
    return (m["ate_rotation_rmse_deg"],
            m["attitude_aligned_ate_rotation_rmse_deg"],
            m["position_vs_attitude_alignment_rotation_deg"])


def spread(Qg):
    """姿态分布有多宽：相对质心姿态的最大/中位夹角。"""
    R = Rotation.from_quat(Qg)
    c = R.mean()
    return np.degrees((R * c.inv()).magnitude())


def main():
    cells = [f for f in sorted(ROOT.glob("**/fusion_current/tight/trajectory_fused.csv"))
             if (Path(f).parents[2] / "lighthouse_body_ground_truth.csv").exists()]
    print(f"{len(cells)} 个 cell\n")

    print("=== ① 真实数据的姿态摆幅（决定常量能不能被 mean 平均掉）===")
    print(f"  {'cell':<44}{'姿态摆幅中位':>14}{'p90':>8}")
    for f in cells:
        _, _, Pg, Qg = load(f)
        s = spread(Qg)
        name = str(Path(f).parent.parent).replace(str(ROOT) + "/", "")
        print(f"  {name:<44}{np.median(s):>14.1f}{np.percentile(s, 90):>8.1f}")

    print("\n=== ② 合成：完美位置 + 恒常量体轴偏移 C（用真实姿态分布）===")
    for f in cells[:4]:
        Pe, Qe, Pg, Qg = load(f)
        name = str(Path(f).parent.parent).replace(str(ROOT) + "/", "")
        real = three(Pe, Qe, Pg, Qg)
        print(f"\n  --- {name} ---")
        print(f"    真实: 门 {real[0]:.2f}  漂移 {real[1]:.2f}  常量 {real[2]:.2f}")
        print(f"    {'|C|(°)':>8}{'门':>8}{'漂移':>8}{'常量':>8}   比对")
        for mag in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
            # C 取真实数据 oracle 常量的大致方向：体轴 +Y 为主
            C = Rotation.from_rotvec(np.radians([0.3, 0.75, -0.6]) / 0.9517 * mag)
            Qc = (Rotation.from_quat(Qg) * C).as_quat()
            g, d, c = three(Pg, Qc, Pg, Qg)
            print(f"    {mag:>8.1f}{g:>8.2f}{d:>8.2f}{c:>8.2f}")

    print("\n=== ③ 对照：随时间**漂移**的姿态误差（同样幅度）===")
    for f in cells[:2]:
        Pe, Qe, Pg, Qg = load(f)
        name = str(Path(f).parent.parent).replace(str(ROOT) + "/", "")
        rng = np.random.default_rng(0)
        n = len(Qg)
        ramp = np.linspace(-1, 1, n)[:, None]
        print(f"\n  --- {name} ---")
        print(f"    {'幅度(°)':>8}{'门':>8}{'漂移':>8}{'常量':>8}")
        for mag in (1.0, 2.0, 3.0, 4.0):
            ax = np.array([0.3, 0.75, -0.6]) / 0.9517
            err = Rotation.from_rotvec(np.radians(mag) * ramp * ax)
            Qe2 = (Rotation.from_quat(Qg) * err).as_quat()
            g, d, c = three(Pg, Qe2, Pg, Qg)
            print(f"    {mag:>8.1f}{g:>8.2f}{d:>8.2f}{c:>8.2f}")


if __name__ == "__main__":
    main()
