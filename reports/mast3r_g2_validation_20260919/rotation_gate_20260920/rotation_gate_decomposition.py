#!/usr/bin/env python3
"""旋转门的三项分解 + 时间偏移扫描 + 数据解外参。

## 为什么做这个

用户的痛点：「旋转侧怎么把轨迹做精准」。门指标 `max_rotation_rmse_deg = 2.0`，
一批 cell 卡在 1.9–3.2°。此前只知道「融合不贡献姿态」，不知道这 2–3° 由什么构成。

## 口径（关键）

`evaluate_slam_ground_truth.pose_errors` 给三个量：

  ate_rotation_rmse_deg                    ← 门指标。对齐旋转 R 来自【位置】的 rigid_align
  attitude_aligned_ate_rotation_rmse_deg   ← 对齐旋转来自【姿态】的 orientation_align ⇒ 去掉常量后的纯漂移
  position_vs_attitude_alignment_rotation_deg ← 上面两个对齐旋转的夹角 ⇒ 常量失配

**门指标用的是位置导出的对齐旋转**，所以一个常量体轴姿态偏移不会被吸收，
会全额进到门里；而纯漂移那个量会被吸收掉。

## 结论（2026-09-20）

1. 度量本身零地板：把 GT 自己做随机刚体变换再评 ⇒ 0.0000°。
2. 纯漂移在 18/18 个 cell 里是 1.04–1.34°，**全部低于 2.0° 的门**。
3. 把门顶出去的是 `position_vs_attitude_alignment` = 1.41–2.98°，即一个近常量的姿态失配。
4. 时间偏移解释不了它：τ 在 ±40ms 扫，这个量纹丝不动。
5. 最优常量体轴旋转能消掉门指标的 10–55%（v10/g2: 3.20→1.44；batch5/g1: 1.76→0.94）。
6. 该常量在 18/18 个 cell 的 Y 分量同号（均值 +1.09° ± 0.44°）。
7. 用「视觉↔IMU 相对旋转」纯旋转手眼解出的相机-IMU 外参，与配置值在 10/10 个
   take 上都差 0.86–2.19°（均值 1.51°），但方向逐 take 漂移 ⇒ 不是单一固定外参误差，
   而是「每次运行都有一个 ~1.5° 的常量姿态偏移」。
8. VINS 运行时配置 `estimate_extrinsic: 0` —— 相机-IMU 旋转**从不在线优化**。

⇒ 旋转门主要由一个「非漂移、近常量、逐 take 变向」的姿态失配决定，
  这类误差不是靠提高里程计精度能消掉的。
"""
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
VINS_CFG = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release"
    "/formal_runtime_calibration/vins_config.yaml"
)


def prepare(est, gt):
    """载入估计与 GT，并把 GT 插值到估计的时刻上（评测器的标准口径）。"""
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], Qe[inside][valid], ctx[:, 1:4], iq


def rotation_rmse(Pe, Qe, Pg, Qg, C=None):
    """门指标：位置对齐 + 姿态残差 RMSE。C 是附加的常量体轴旋转。"""
    Q = Qe if C is None else (Rotation.from_quat(Qe) * C).as_quat()
    R, _ = E.rigid_align(Pe, Pg)
    delta = Rotation.from_quat(Qg).inv() * (
        Rotation.from_matrix(R) * Rotation.from_quat(Q)
    )
    return float(np.sqrt(np.mean(np.degrees(delta.magnitude()) ** 2))), delta


def best_constant_rotation(Pe, Qe, Pg, Qg):
    """优化一个常量体轴旋转 C 使门指标最小；返回 (C, 原值, 最优值)。"""
    def objective(x):
        return rotation_rmse(Pe, Qe, Pg, Qg, Rotation.from_rotvec(x))[0]

    result = minimize(
        objective, np.zeros(3), method="Nelder-Mead",
        options=dict(xatol=1e-8, fatol=1e-8, maxiter=8000),
    )
    return Rotation.from_rotvec(result.x), objective(np.zeros(3)), objective(result.x)


def metric_floor(gt, n_random=3, noise_levels=(0.5, 1.0, 2.0)):
    """① 度量地板：GT 自己做刚体变换 + 加噪，看这个度量能不能读出 0。"""
    _, Pg, Qg = E.load_trajectory(gt)
    rng = np.random.default_rng(0)
    print("=== ① 度量地板自测 ===")
    for i in range(n_random):
        R = Rotation.random(random_state=rng).as_matrix()
        Pr = Pg @ R.T + rng.normal(scale=0.05, size=3)
        Qr = (Rotation.from_matrix(R) * Rotation.from_quat(Qg)).as_quat()
        print(f"  随机刚体变换{i}: 门指标 {rotation_rmse(Pr, Qr, Pg, Qg)[0]:.4f}°")
    for sigma_mm in noise_levels:
        Pn = Pg + rng.normal(scale=sigma_mm / 1000.0, size=Pg.shape)
        Qn = (Rotation.from_rotvec(
            rng.normal(scale=np.radians(sigma_mm * 0.3), size=(len(Qg), 3))
        ) * Rotation.from_quat(Qg)).as_quat()
        m = E.pose_errors(Pn, Qn, Pg, Qg, 30)
        print(f"  加噪 {sigma_mm}mm: 门 {m['ate_rotation_rmse_deg']:.3f}°  "
              f"纯漂移 {m['attitude_aligned_ate_rotation_rmse_deg']:.3f}°  "
              f"常量失配 {m['position_vs_attitude_alignment_rotation_deg']:.3f}°")


def time_offset_sweep(est, gt, span_ms=40, step_ms=2):
    """④ 常量失配是不是时间偏移造成的？把估计在时间上平移 τ 再评。"""
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    print(f"\n=== ④ 时间偏移扫描 ({est.name}) ===")
    print(f"  {'τ(ms)':>7}{'门指标':>9}{'纯漂移':>9}{'常量失配':>10}{'ATE_T(mm)':>11}")
    for tau in np.arange(-span_ms / 1000.0, span_ms / 1000.0 + 1e-9, step_ms / 1000.0):
        t_new = tg - tau
        keep = (t_new >= te[0]) & (t_new <= te[-1])
        tn = t_new[keep]
        P = np.column_stack([np.interp(tn, te, Pe[:, i]) for i in range(3)])
        Q = Slerp(te, Rotation.from_quat(Qe))(tn).as_quat()
        m = E.pose_errors(P, Q, Pg[keep], Qg[keep], 30)
        print(f"  {tau*1e3:>7.1f}{m['ate_rotation_rmse_deg']:>9.2f}"
              f"{m['attitude_aligned_ate_rotation_rmse_deg']:>9.2f}"
              f"{m['position_vs_attitude_alignment_rotation_deg']:>10.2f}"
              f"{m['ate_translation_rmse_m']*1e3:>11.2f}")


def solve_camera_imu_rotation(frames_csv, vins_csv, body_t_cam0):
    """⑦ 纯旋转手眼：由两侧的相对旋转解出 R_body←camera。

    ΔR_body = R_bc · ΔR_cam · R_bc⁻¹  （相对旋转与世界系无关）
    """
    tc, _, Qc = E.load_trajectory(frames_csv)
    tv, _, Qv = E.load_trajectory(vins_csv)
    keep = (tc >= tv[0]) & (tc <= tv[-1])
    t = tc[keep]
    Qv_i = Slerp(tv, Rotation.from_quat(Qv))(t)
    Rc = Rotation.from_quat(Qc[keep])
    pairs = []
    for dt in (0.3, 0.5, 1.0, 2.0):
        n = max(1, int(round(dt / np.median(np.diff(t)))))
        d_cam = Rc[:-n].inv() * Rc[n:]
        d_body = Qv_i[:-n].inv() * Qv_i[n:]
        angle = np.degrees(d_cam.magnitude())
        pairs.append((d_cam[angle > np.percentile(angle, 50)],
                      d_body[angle > np.percentile(angle, 50)]))
    d_cam = Rotation.concatenate([p[0] for p in pairs])
    d_body = Rotation.concatenate([p[1] for p in pairs])

    def objective(x):
        R_bc = Rotation.from_rotvec(x)
        residual = d_body.inv() * (R_bc * d_cam * R_bc.inv())
        return float(np.mean(np.degrees(residual.magnitude()) ** 2))

    result = minimize(objective, np.zeros(3), method="Nelder-Mead",
                      options=dict(xatol=1e-10, fatol=1e-13, maxiter=40000))
    R_bc = Rotation.from_rotvec(result.x)
    difference = Rotation.from_matrix(body_t_cam0.T @ R_bc.as_matrix())
    return (np.degrees(difference.as_rotvec()), np.degrees(difference.magnitude()),
            float(np.sqrt(objective(result.x))), len(d_cam))


def read_body_t_cam0():
    import re
    text = VINS_CFG.read_text(encoding="utf-8")
    block = re.search(r"^body_T_cam0\s*:(.*?)(?=^\w|\Z)", text, re.M | re.S)
    values = [float(x) for x in re.findall(r"-?\d+\.\d+", block.group(1))][:16]
    return np.array(values).reshape(4, 4)[:3, :3]


def main():
    import glob
    groups = sorted(glob.glob(str(ROOT / "**/fusion_current/tight/trajectory_fused.csv"),
                              recursive=True))
    print(f"扫描 {len(groups)} 个 fusion_current/tight cell\n")
    print(f"{'cell':<48}{'门指标':>8}{'纯漂移':>8}{'常量失配':>10}{'常量可消':>10}")
    print("-" * 86)
    for fused in groups:
        gt = Path(fused).parents[2] / "lighthouse_body_ground_truth.csv"
        if not gt.exists():
            continue
        name = str(Path(fused).parent.parent).replace(str(ROOT) + "/", "")
        Pe, Qe, Pg, Qg = prepare(Path(fused), gt)
        m = E.pose_errors(Pe, Qe, Pg, Qg, 30)
        _, base, opt = best_constant_rotation(Pe, Qe, Pg, Qg)
        print(f"{name:<48}{m['ate_rotation_rmse_deg']:>8.2f}"
              f"{m['attitude_aligned_ate_rotation_rmse_deg']:>8.2f}"
              f"{m['position_vs_attitude_alignment_rotation_deg']:>10.2f}"
              f"{100*(base-opt)/base:>9.0f}%")
    g1 = ROOT / "20260914_validation_v10_batch/group1"
    metric_floor(g1 / "lighthouse_body_ground_truth.csv")
    time_offset_sweep(g1 / "fusion_current/tight/trajectory_fused.csv",
                      g1 / "lighthouse_body_ground_truth.csv")
    print("\n=== ⑦ 数据解 相机-IMU 外参 vs 配置（10 个 take）===")
    R_c0 = read_body_t_cam0()
    print(f"  {'take':<46}{'Δx':>7}{'Δy':>7}{'Δz':>7}{'|Δ|':>7}{'残差':>7}")
    deltas = []
    for frames in sorted(glob.glob(str(ROOT / "**/fusion/tight/mast3r/trajectory_frames.csv"),
                                   recursive=True)):
        group = Path(frames).parents[3]
        vins = group / "docker2_slam/vio_corrected_stream.csv"
        if not vins.exists():
            continue
        name = str(group).replace(str(ROOT) + "/", "")
        try:
            d, magnitude, residual, count = solve_camera_imu_rotation(frames, vins, R_c0)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:<46} 失败 {exc}")
            continue
        deltas.append(d)
        print(f"  {name:<46}{d[0]:>7.2f}{d[1]:>7.2f}{d[2]:>7.2f}"
              f"{magnitude:>7.2f}{residual:>7.2f}")
    if deltas:
        deltas = np.array(deltas)
        magnitude = np.linalg.norm(deltas, axis=1)
        print(f"\n  |Δ| 均值 {magnitude.mean():.2f}° 标准差 {magnitude.std():.2f}° "
              f"范围 [{magnitude.min():.2f}, {magnitude.max():.2f}]  ({len(deltas)}/{len(deltas)} 非零)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
