#!/usr/bin/env python3
"""把旋转误差拆成 **yaw（绕重力轴）** 与 **tilt（滚转/俯仰）** 两个分量。

## 为什么要拆

VIO 的规范自由度是 **4 维**（位置 3 + 绕重力轴的 yaw 1）——重力把旋转对称性破缺到
只剩绕重力轴的转动，所以 **yaw 在 VIO 里不可观测**（UCSD geometric reduction；
OpenVINS `StaticInitializer` 原话「A VIO system has 4dof unobservable directions」）。
实现在 rpg_trajectory_evaluation 里就是 `align_type=posyaw`。

⇒ 若门里的误差**主要是 yaw**，那它是**规范自由度**，不是可修的估计误差；
若主要是 tilt，那是真误差，且 tilt 恰好是重力能观测的那两个自由度。

## 关键背景：本台架的双目基线≈0.01mm（伪双目退化，见 [[d405-hardware-facts]]）

双目视差几乎为零 ⇒ 视觉几乎不提供 yaw 约束；再加轨迹只有 16–20cm、平移激励极小
⇒ **yaw 可观测性特别弱**。若拆出来 yaw 占大头，那与这条硬件事实自洽。

## 坐标约定（踩过坑，写死在这里）

`rigid_align(source, target)` 返回的 `R` 满足 `R @ source_center + t = target_center`
⇒ **`R` 把「估计世界系」映到「GT 世界系」**。
`orientation_align(Qe, Qg)` 返回 `mean(Rg · Re⁻¹)`，同样是 **估计世界系 → GT 世界系**。

误差旋转取 `E_w(t) = R_aligned_est(t) · R_gt(t)⁻¹`，它把 **GT 体轴系 → GT 世界系**，
所以 `E_w.as_rotvec()` 的分量是**在 GT 世界系**里。
⇒ 重力轴必须也取 GT 世界系：`u = R_p @ [0,0,1]`（**不是** `R_p⁻¹`）。

## 两列对照

  * **门**：位置导出的对齐（`rigid_align`）—— 这是验收门实际用的口径；
  * **门(姿态对齐)**：姿态导出的对齐（`orientation_align`）—— 位置不参与，
    纯粹是姿态误差。两者之差 = **位置污染**（见 `alignment_bootstrap.py`）。
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
    inside, valid, plt, qlt = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], Qe[inside][valid], plt[:, 1:4], qlt


def split(Pe, Qe, Pg, Qg, R_a):
    """→ (总门 RMS, yaw RMS, tilt RMS)。R_a 是已选定的对齐旋转（估计世界系→GT 世界系）。"""
    u = R_a.apply([0.0, 0.0, 1.0])         # GT 世界系里的重力方向
    Ew = (R_a * Rotation.from_quat(Qe)) * Rotation.from_quat(Qg).inv()
    v = np.degrees(Ew.as_rotvec())          # 每帧误差旋转矢量（GT 世界系）
    yaw = v @ u
    tilt = np.linalg.norm(v - np.outer(yaw, u), axis=1)
    return (float(np.sqrt(np.mean(np.degrees(Ew.magnitude()) ** 2))),
            float(np.sqrt(np.mean(yaw ** 2))),
            float(np.sqrt(np.mean(tilt ** 2))))


def main():
    print("误差旋转拆成 yaw（绕重力轴，VIO 不可观测）与 tilt（滚转/俯仰，可观测）")
    print("门 = 位置导出的对齐（验收口径）；门(姿态对齐) = 位置不参与，纯姿态误差")
    print("两者之差 = 位置污染；yaw/tilt 由误差旋转矢量沿 GT 世界系重力轴投影而来\n")
    print(f"{'cell':<42}{'门':>7}{'yaw':>7}{'tilt':>7}{'yaw占比':>9}│"
          f"{'门(姿态对齐)':>12}{'yaw':>7}{'tilt':>7}")
    print("-" * 104)
    agg = []
    for g in sorted({f.parents[2] for f in
                     ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
        gt = g / "lighthouse_body_ground_truth.csv"
        est = g / "fusion_current/tight/trajectory_fused.csv"
        if not (gt.exists() and est.exists()):
            continue
        name = str(g).replace(str(ROOT) + "/", "")
        Pe, Qe, Pg, Qg = load(est, gt)

        R_p = Rotation.from_matrix(E.rigid_align(Pe, Pg)[0])
        R_q = Rotation.from_matrix(E.orientation_align(Qe, Qg))
        du = np.degrees(np.arccos(np.clip(R_p.apply([0, 0, 1]) @ R_q.apply([0, 0, 1]), -1, 1)))

        gate, y, t = split(Pe, Qe, Pg, Qg, R_p)
        gate2, y2, t2 = split(Pe, Qe, Pg, Qg, R_q)
        print(f"{name:<42}{gate:>7.2f}{y:>7.2f}{t:>7.2f}"
              f"{100 * y / np.hypot(y, t):>8.0f}%│{gate2:>12.2f}{y2:>7.2f}{t2:>7.2f}"
              f"   [两法重力轴差 {du:.2f}°]")
        agg.append((gate, y, t, gate2, y2, t2))

    if not agg:
        return
    A = np.array(agg)
    m = lambda k: float(np.median(A[:, k]))  # noqa: E731
    print(f"\n=== 汇总（{len(agg)} cell，中位）===")
    print(f"  门（位置导出对齐，验收口径）   {m(0):.2f}°")
    print(f"    其中 yaw（绕重力轴）          {m(1):.2f}°   占 {100*m(1)/np.hypot(m(1),m(2)):.0f}%")
    print(f"    其中 tilt（滚转/俯仰）        {m(2):.2f}°")
    print(f"  门（姿态导出对齐，无位置污染） {m(3):.2f}°")
    print(f"    其中 yaw                     {m(4):.2f}°   占 {100*m(4)/np.hypot(m(4),m(5)):.0f}%")
    print(f"    其中 tilt                    {m(5):.2f}°")
    print(f"  位置污染（两者之差）           {m(0) - m(3):.2f}°")
    print(f"\n  yaw > tilt 的 cell：{int((A[:, 1] > A[:, 2]).sum())}/{len(agg)}")
    print(f"  只看 tilt 就过 2.0° 门：{int((A[:, 2] < 2.0).sum())}/{len(agg)}")
    print(f"  只看 yaw  就过 2.0° 门：{int((A[:, 1] < 2.0).sum())}/{len(agg)}")
    print(f"  用姿态对齐就过 2.0° 门：{int((A[:, 3] < 2.0).sum())}/{len(agg)}")


if __name__ == "__main__":
    main()
