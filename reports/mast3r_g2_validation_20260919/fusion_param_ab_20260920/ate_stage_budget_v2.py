#!/usr/bin/env python3
"""ATE 阶段预算 **v2：修掉坐标系错配**。

## v1 错在哪

v1 把 `mast3r/trajectory_*.csv`（**相机系**）直接拿去和
`lighthouse_body_ground_truth.csv`（**body 系**）比。两者差一个
`camera_to_body` 变换，而该变换**不是刚性的**：杆臂（29mm）是逐帧按姿态减掉的
⇒ 轨迹半径变 **6.1%**（172.3→161.8mm）。

后果：v1 量出「MASt3R 全程 倍率≈0.94，不是度量轨迹」——**那是坐标系错配的假象**。
验算 `0.9401 / 0.9383 = 1.0019` ≈ fused 实测 `1.0016` ⇒ 图优化轨迹本来就是度量的。

## v2 口径

相机系阶段先过 `camera_to_body_with_body_orientation_prior`
（= 工作流实际用的那条，`--use-docker2-orientation-for-lever-arm`）再评测；
body 系阶段（fused / VINS）直接评测。姿态一律先 Slerp 到 GT 栅格。
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
CAMERA_FRAME = ("trajectory_frames.csv", "trajectory_online_frames.csv",
                "trajectory_stereo_bidirectional.csv", "trajectory_stereo_long_hops.csv",
                "trajectory_stereo_dense10hz.csv", "trajectory_stereo_multisecond.csv",
                "trajectory_imu_metric.csv", "trajectory_graph.csv")
BODY_FRAME = (("fusion_unsmoothed", "fusion_current/tight/trajectory_fused_unsmoothed.csv"),
              ("fusion_final", "fusion_current/tight/trajectory_fused.csv"),
              ("VINS_corrected", "docker2_slam/vio_corrected_stream.csv"))


def on_gt_grid(t, P, Q, tg, tg_quat=None):
    """位置线性插值、姿态 Slerp 到 GT 栅格。"""
    P = np.column_stack([np.interp(tg, t, P[:, k]) for k in range(3)])
    Qr = E.Slerp(t, Rotation.from_quat(Q))(tg)
    return P, Qr


def main():
    T = F.load_body_t_camera(CONFIG)
    print("ATE 阶段预算 v2（相机系阶段先过 camera_to_body_with_body_orientation_prior）")
    print(f"杆臂 |t| = {np.linalg.norm(T[:3,3])*1000:.1f} mm\n")
    summary, gate = {}, {}
    cells = sorted({f.parents[2] for f in
                    ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")})
    for c in cells:
        gt = c / "lighthouse_body_ground_truth.csv"
        md = c / "fusion_current/tight/mast3r"
        vins = c / "docker2_slam/vio_corrected_stream.csv"
        if not (gt.exists() and md.exists() and vins.exists()):
            continue
        tg, Pg, Qg = E.load_trajectory(gt)
        tv, Pv, Qv = E.load_trajectory(vins)
        Qv_at_gt = E.Slerp(tv, Rotation.from_quat(Qv))(tg)
        print(f"### {str(c).replace(str(ROOT)+'/', '')}")
        print(f"    {'阶段':<40}{'ATE':>8}{'p95':>7}{'max':>7}{'倍率':>8}{'门':>7}")
        for s in CAMERA_FRAME:
            p = md / s
            if not p.exists():
                continue
            t, P, Q = E.load_trajectory(p)
            ins = (t >= tg[0]) & (t <= tg[-1])
            if ins.sum() < 10:
                continue
            tt, PP, QQ = t[ins], P[ins], Q[ins]
            prior = E.Slerp(tv, Rotation.from_quat(Qv))(tt)   # VINS 姿态到估计时刻
            bp, br, _ = F.camera_to_body_with_body_orientation_prior(
                PP, Rotation.from_quat(QQ), T, prior)
            # 注意：该函数返回的 body 姿态是【对齐后的 VINS 姿态】，
            # 视觉姿态被丢弃 ⇒ 相机系阶段的门无意义，只看 ATE。
            _, _, plt_, qlt_ = E.interpolate_ground_truth(tt, tg, Pg, Qg, 0.1)
            m = E.pose_errors(bp, br.as_quat(), plt_[:, 1:4], qlt_, 30)
            summary.setdefault(s + " (→body)", []).append(m['ate_translation_rmse_m'] * 1000)
            gate.setdefault(s + " (→body)", []).append(m['ate_rotation_rmse_deg'])
            print(f"    {s+' (→body)':<40}{m['ate_translation_rmse_m']*1000:>8.1f}"
                  f"{m['ate_translation_p95_m']*1000:>7.1f}{m['ate_translation_max_m']*1000:>7.1f}"
                  f"{m['sim3_diagnostic_scale_gt_per_estimate']:>8.3f}"
                  f"{m['ate_rotation_rmse_deg']:>7.2f}")
        for tag, rel in BODY_FRAME:
            p = c / rel
            if not p.exists():
                continue
            t, P, Q = E.load_trajectory(p)
            ins = (t >= tg[0]) & (t <= tg[-1])
            tt, PP, QQ = t[ins], P[ins], Q[ins]
            _, _, plt_, qlt_ = E.interpolate_ground_truth(tt, tg, Pg, Qg, 0.1)
            m = E.pose_errors(PP, QQ, plt_[:, 1:4], qlt_, 30)
            summary.setdefault(tag, []).append(m['ate_translation_rmse_m'] * 1000)
            gate.setdefault(tag, []).append(m['ate_rotation_rmse_deg'])
            print(f"    {tag:<40}{m['ate_translation_rmse_m']*1000:>8.1f}"
                  f"{m['ate_translation_p95_m']*1000:>7.1f}{m['ate_translation_max_m']*1000:>7.1f}"
                  f"{m['sim3_diagnostic_scale_gt_per_estimate']:>8.3f}"
                  f"{m['ate_rotation_rmse_deg']:>7.2f}")
        print()
    print("=== 汇总（中位）===")
    for k in summary:
        a, g = np.array(summary[k]), np.array(gate[k])
        print(f"  {k:<44} ATE {np.median(a):6.2f} mm | 门 {np.median(g):5.2f}° "
              f"| 门过 {int((g<2.0).sum())}/{len(g)}")


if __name__ == "__main__":
    main()
