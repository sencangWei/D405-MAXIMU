#!/usr/bin/env python3
"""**09-14 的 fused 是不是就是 MASt3R 图优化轨迹本身？**

## 推导（读代码得到，不是猜）

`complementary_positions`：
    fused = scaled_base + local_weight · (disagreement − low_frequency)
`scale_ratio = docker2_scale_ratio ** docker2_scale_weight`
09-14 的 report 记着 `local_weight=0.0`、`docker2_scale_weight=0.0`
⇒ `scale_ratio = x**0 = 1.0` ⇒ `scaled_base = base` ⇒ `fused = base`
而 `base = camera_to_body_with_body_orientation_prior(mast3r_positions, …)`
⇒ **09-14 的 fused 应当逐位等于 `mast3r/trajectory_graph.csv` 过 camera_to_body。**

## 判据

  * 两者位置差 RMS ≪ 0.1mm  ⇒ 推导成立（"09-14 好结果"= 图优化轨迹）；
  * 若差得远 ⇒ 推导错，融合确实改了东西，得另找。
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402
import fuse_docker2_mast3r_complementary as F  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
              "formal_runtime_calibration/vins_config.yaml")


def main():
    T = F.load_body_t_camera(CONFIG)
    print("09-14 fused  vs  MASt3R 图优化轨迹（过 camera_to_body 后）\n")
    print(f"{'cell':<44}{'RMS差':>9}{'max差':>8}   {'graph ATE/门':>14}{'0914 ATE/门':>14}{'now ATE/门':>13}")
    print("-" * 106)
    rows = []
    for c in sorted({f.parents[2] for f in
                     ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
        gt = c / "lighthouse_body_ground_truth.csv"
        g0 = c / "fusion/tight/trajectory_fused.csv"
        g1 = c / "fusion_current/tight/trajectory_fused.csv"
        md = c / "fusion_current/tight/mast3r/trajectory_graph.csv"
        vins = c / "docker2_slam/vio_corrected_stream.csv"
        if not all(p.exists() for p in (gt, g0, g1, md, vins)):
            continue
        tg, Pg, Qg = E.load_trajectory(gt)
        tv, Pv, Qv = E.load_trajectory(vins)

        def official(t, P, Q):
            _, _, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
            m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
            return m['ate_translation_rmse_m'] * 1000, m['ate_rotation_rmse_deg']

        # graph → body（用 VINS 姿态先验，与工作流一致）
        t, P, Q = E.load_trajectory(md)
        ins = (t >= tg[0]) & (t <= tg[-1])
        tt, PP, QQ = t[ins], P[ins], Q[ins]
        prior = E.Slerp(tv, Rotation.from_quat(Qv))(tt)
        bp, br, _ = F.camera_to_body_with_body_orientation_prior(PP, Rotation.from_quat(QQ), T, prior)
        a_g, r_g = official(tt, bp, br.as_quat())

        # 09-14 fused 应当 ≈ bp（但在 common_times 上）
        t0, P0, Q0 = E.load_trajectory(g0)
        ins0 = (t0 >= tt[0]) & (t0 <= tt[-1])
        ref = np.column_stack([np.interp(t0[ins0], tt, bp[:, k]) for k in range(3)])
        d = np.linalg.norm(P0[ins0] - ref, axis=1)
        a_0, r_0 = official(t0[ins0], P0[ins0], Q0[ins0])
        t1, P1, Q1 = E.load_trajectory(g1)
        a_1, r_1 = official(t1, P1, Q1)
        name = str(c).replace(str(ROOT) + "/", "")
        print(f"{name:<44}{np.sqrt((d**2).mean())*1000:>8.3f}{d.max()*1000:>8.3f}"
              f"   {a_g:>6.2f}/{r_g:<6.2f}{a_0:>7.2f}/{r_0:<6.2f}{a_1:>7.2f}/{r_1:<6.2f}")
        rows.append((a_g, r_g, a_0, r_0, a_1, r_1, np.sqrt((d**2).mean()) * 1000))
    if not rows:
        return
    A = np.array(rows)
    med = lambda k: float(np.median(A[:, k]))  # noqa: E731
    print(f"\n=== 汇总（{len(rows)} cell，中位）===")
    print(f"  09-14fused 与 graph(→body) 位置差 RMS  {med(6):.4f} mm   ⇒ "
          f"{'逐位相同，推导成立' if med(6) < 0.1 else '不等，推导错'}")
    for tag, i in (("graph(→body)", 0), ("09-14 fused", 2), ("now fused", 4)):
        print(f"  {tag:<14} ATE {med(i):6.2f} mm | 门 {med(i+1):5.2f}° | "
              f"门过 {int((A[:, i+1] < 2.0).sum())}/{len(A)}")


if __name__ == "__main__":
    main()
