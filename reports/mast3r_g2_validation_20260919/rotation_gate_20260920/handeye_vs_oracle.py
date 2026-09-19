#!/usr/bin/env python3
"""能不能**不用真值**量出那个常量体轴偏移，并直接修掉它。

> ## ⚠ 本脚本的结论【已作废】（2026-09-20 当天自我推翻）
>
> 它给出的 |C_he| ≈ 88.8°、手眼拟合残差 0.94°，**是 Nelder-Mead 在退化代价面上
> 跑出来的假解**，不是观测量。`handeye_conditioning.py` 用代价面形状证明：
> 本数据的代价在**配置值处就是局部极小**，±2° 切向变化 ≲0.5 deg²，而噪声底
> 就有 0.7°RMS ⇒ **这段数据分辨不了相机-IMU 外参**，手眼这条路在此数据上不成立。
>
> 保留本文件只为留痕（免得同一件事被重新试一遍）。下面的推导本身没错，
> 错的是「本数据能解出 R_data」这个前提。

## 推导（若外参可辨识，手眼解出来的就是修正量）

VINS 输出的是体轴位姿。设配置的外参 R_bc_cfg（body←camera），真值 R_bc_true，
于是单帧的**诱导误差** C_err 满足

    R_wb_out = R_wb_true · C_err        （右乘 = 体轴常量旋转）

对相对旋转取共轭可知，从输出里量到的相对旋转是
`ΔR_body_out = (C_err⁻¹ R_bc_true) ΔR_cam (C_err⁻¹ R_bc_true)⁻¹`，
所以**手眼解出来的不是 R_bc_true，而是**

    R_bc_data = C_err⁻¹ · R_bc_true  ≈  C_err⁻¹ · R_bc_cfg

⇒ **修正量 C_applied = C_err⁻¹ = R_bc_data · R_bc_cfg⁻¹**（右乘到输出四元数上）。

其中 R_bc_data 由 MASt3R 左目相对旋转 vs VINS 体轴相对旋转解出，**完全不用真值**。

本脚本把它和 oracle（用真值解的最优常量）对比：方向对不对、能吃掉多少门。
"""
import glob
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
VINS_CFG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release"
                "/formal_runtime_calibration/vins_config.yaml")


def read_body_t_cam0():
    text = VINS_CFG.read_text(encoding="utf-8")
    block = re.search(r"^body_T_cam0\s*:(.*?)(?=^\w|\Z)", text, re.M | re.S)
    vals = [float(x) for x in re.findall(r"-?\d+\.\d+", block.group(1))][:16]
    return np.array(vals).reshape(4, 4)[:3, :3]


def load_fused(fused):
    gt = Path(fused).parents[2] / "lighthouse_body_ground_truth.csv"
    te, Pe, Qe = E.load_trajectory(fused)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], Qe[inside][valid], ctx[:, 1:4], iq


def gate(Pe, Qe, Pg, Qg, C=None):
    Q = Qe if C is None else (Rotation.from_quat(Qe) * C).as_quat()
    R, _ = E.rigid_align(Pe, Pg)
    d = Rotation.from_quat(Qg).inv() * (Rotation.from_matrix(R) * Rotation.from_quat(Q))
    return float(np.sqrt(np.mean(np.degrees(d.magnitude()) ** 2)))


def solve_oracle(Pe, Qe, Pg, Qg):
    f = lambda x: gate(Pe, Qe, Pg, Qg, Rotation.from_rotvec(x))  # noqa: E731
    r = minimize(f, np.zeros(3), method="Nelder-Mead",
                 options=dict(xatol=1e-9, fatol=1e-10, maxiter=20000))
    return Rotation.from_rotvec(r.x)


def handeye(frames_csv, vins_csv):
    """由 MASt3R 左目相对旋转 vs VINS 体轴相对旋转解 R_bc_data（不用真值）。"""
    tc, _, Qc = E.load_trajectory(frames_csv)
    tv, _, Qv = E.load_trajectory(vins_csv)
    keep = (tc >= tv[0]) & (tc <= tv[-1])
    t = tc[keep]
    Qv_i = Slerp(tv, Rotation.from_quat(Qv))(t)
    Rc = Rotation.from_quat(Qc[keep])
    dts = (0.3, 0.5, 1.0, 2.0)
    dc, db = [], []
    for dt in dts:
        n = max(1, int(round(dt / np.median(np.diff(t)))))
        a = Rc[:-n].inv() * Rc[n:]
        b = Qv_i[:-n].inv() * Qv_i[n:]
        sel = np.degrees(a.magnitude()) > np.percentile(np.degrees(a.magnitude()), 50)
        dc.append(a[sel])
        db.append(b[sel])
    dc = Rotation.concatenate(dc)
    db = Rotation.concatenate(db)

    def obj(x):
        R = Rotation.from_rotvec(x)
        return float(np.mean(np.degrees((db.inv() * (R * dc * R.inv())).magnitude()) ** 2))

    r = minimize(obj, np.zeros(3), method="Nelder-Mead",
                 options=dict(xatol=1e-10, fatol=1e-13, maxiter=40000))
    return Rotation.from_rotvec(r.x), float(np.sqrt(obj(r.x))), len(dc)


def main():
    R_cfg = read_body_t_cam0()
    R_cfg_rot = Rotation.from_matrix(R_cfg)
    print(f"配置 body_T_cam0 旋转角 {np.degrees(R_cfg_rot.magnitude()):.2f}°\n")
    print(f"{'cell':<42}{'门':>7}{'oracle后':>9}{'|C_ora|':>8}"
          f"{'|C_he|>':>8}{'夹角':>7}{'手眼后':>8}{'吃掉':>7}")
    print("-" * 96)

    rows = []
    for fused in sorted(ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")):
        fused = Path(fused)
        group = fused.parents[2]
        gt = group / "lighthouse_body_ground_truth.csv"
        if not gt.exists():
            continue
        frames = group / "fusion/tight/mast3r/trajectory_frames.csv"
        vins = group / "docker2_slam/vio_corrected_stream.csv"
        if not (frames.exists() and vins.exists()):
            continue
        name = str(group).replace(str(ROOT) + "/", "")
        Pe, Qe, Pg, Qg = load_fused(fused)
        base = gate(Pe, Qe, Pg, Qg)

        C_ora = solve_oracle(Pe, Qe, Pg, Qg)
        g_ora = gate(Pe, Qe, Pg, Qg, C_ora)

        R_data, resid, n = handeye(frames, vins)
        C_he = R_data * R_cfg_rot.inv()          # 推导：C_applied = R_bc_data · R_bc_cfg⁻¹
        g_he = gate(Pe, Qe, Pg, Qg, C_he)
        diff = np.degrees((C_he * C_ora.inv()).magnitude())

        removable = base - g_ora
        got = base - g_he
        frac = 100 * got / removable if removable > 0.05 else float("nan")
        print(f"{name:<42}{base:>7.2f}{g_ora:>9.2f}"
              f"{np.degrees(C_ora.magnitude()):>8.2f}{np.degrees(C_he.magnitude()):>8.2f}"
              f"{diff:>7.2f}{g_he:>8.2f}{frac:>6.0f}%")
        rows.append(dict(name=name, base=base, ora=g_ora, he=g_he,
                         ca=np.degrees(C_ora.as_rotvec()),
                         ch=np.degrees(C_he.as_rotvec()), diff=diff, resid=resid))

    if not rows:
        return
    base = np.array([r["base"] for r in rows])
    ora = np.array([r["ora"] for r in rows])
    he = np.array([r["he"] for r in rows])
    print(f"\n=== 汇总（{len(rows)} 个 cell，手眼全程不用真值）===")
    print(f"  门中位：基线 {np.median(base):.2f}° → oracle {np.median(ora):.2f}° "
          f"→ 手眼 {np.median(he):.2f}°")
    print(f"  手眼修好的 cell：{int((he < base).sum())}/{len(rows)}  "
          f"中位改善 {np.median(base - he):.2f}°")
    print(f"  手眼后仍 >2.0° 门：{int((he > 2.0).sum())}/{len(rows)}"
          f"   （基线 {int((base > 2.0).sum())}/{len(rows)}，"
          f"oracle {int((ora > 2.0).sum())}/{len(rows)}）")
    print(f"  |C_he| 中位 {np.median([np.degrees(np.linalg.norm(r['ch'])) for r in rows]):.2f}°  "
          f"|C_ora| 中位 {np.median([np.degrees(np.linalg.norm(r['ca'])) for r in rows]):.2f}°")
    print(f"  C_he 与 C_ora 夹角 中位 "
          f"{np.median([r['diff'] for r in rows]):.2f}°")
    print(f"  手眼拟合残差中位 {np.median([r['resid'] for r in rows]):.2f}°")
    print("\n  分量（度）：")
    for k, lab in (("ca", "C_oracle"), ("ch", "C_handeye")):
        v = np.array([r[k] for r in rows])
        print(f"    {lab:<10} 均值 {v.mean(axis=0).round(2)}  "
              f"标准差 {v.std(axis=0).round(2)}")


if __name__ == "__main__":
    main()
