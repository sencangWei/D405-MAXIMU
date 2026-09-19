#!/usr/bin/env python3
"""常量失配(1.4–3.0°)到底是【真姿态误差】还是【位置对齐的病态放大】。

## 为什么问这个

`rotation_gate_decomposition.py` 把门指标拆成两项：

    门 ≈ sqrt( 纯漂移² + 常量失配² )      纯漂移 1.04–1.34° / 常量失配 1.41–2.98°

然后我把常量失配解释成「一个 ~1.5° 的常量**体轴姿态**偏移」，并去找外参。
但门指标的对齐旋转 R_a 是**从位置** `rigid_align` 出来的。轨迹只有 ~16cm、
ATE 有 ~5mm —— 在这种几何下，位置误差本身就会让 R_a 偏掉。**偏掉的 R_a 与
R_q 的夹角，和「真的存在一个常量体轴姿态偏移」在度量上完全同形，分不开。**

本脚本用一个合成实验把它们分开：

    构造一个【姿态绝对正确、只有位置带真实量级误差】的估计，
    看它的门指标是多少。

若合成门 ≈ 实测常量失配 ⇒ 常量项由位置误差解释，姿态侧根本没病，
                      那么「修外参 / 修姿态」这条路是空的。
若合成门 << 实测 ⇒ 真的存在常量姿态偏移。

## 方法

设 GT 世界系与估计世界系重合（M = I，不做任何世界旋转）。那么：

  - 姿态完美 ⇒ Q_est = Q_gt ⇒ R_q = orientation_align = I（恒等）
  - 位置 = Pg + n，n 取真实残差量级 ⇒ R_a = rigid_align(Pg+n, Pg) ≈ I + ε
  - 门 = rmse angle(R_a · R_est, R_gt) = rmse angle(R_a · R_gt, R_gt) = |R_a| ≈ ε
  - 常量失配 = angle(R_a, R_q) = angle(R_a, I) = |R_a| ≈ ε

⇒ **合成门 = 合成常量失配 = ε**，即「纯位置误差能把对齐旋转推歪多少」。

n 的取法三档（从最贴近真实到最保守）：
  A. 真实残差整体随机旋转后再用（保留幅度与空间结构）
  B. 真实残差逐样本随机重排（保留幅度与边缘分布，破坏空间相关）
  C. 各向同性高斯，σ = 实测 ATE_RMSE
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def load_cell(fused):
    gt = Path(fused).parents[2] / "lighthouse_body_ground_truth.csv"
    te, Pe, Qe = E.load_trajectory(fused)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], Qe[inside][valid], ctx[:, 1:4], iq


def extent(P):
    """轨迹的空间尺度：以质心为中心的点云半径 RMS（决定对齐的条件数）。"""
    return float(np.sqrt(np.mean(np.sum((P - P.mean(axis=0)) ** 2, axis=1))))


def probe_convention():
    """钉死度量约定：世界旋转 M 与常量体轴偏移 C 各自进门多少。"""
    rng = np.random.default_rng(7)
    n = 400
    Pg = rng.normal(scale=0.08, size=(n, 3))
    Qg = Rotation.random(n, random_state=rng).as_quat()
    M = Rotation.from_rotvec([0.2, -0.3, 0.1])             # 世界系旋转
    C = Rotation.from_rotvec(np.radians([0.0, 1.5, 0.0]))  # 常量体轴姿态偏移

    print("=== ⓪ 约定探针 ===")
    for label, MM, CC in (("只有世界旋转 M", M, Rotation.identity()),
                          ("只有常量体轴偏移 C", Rotation.identity(), C),
                          ("M 与 C 都有", M, C)):
        Pe = Pg @ MM.as_matrix()                       # 位置换到估计世界系
        Qe = (MM.inv() * Rotation.from_quat(Qg) * CC).as_quat()
        m = E.pose_errors(Pe, Qe, Pg, Qg, 30)
        print(f"  {label:<18} 门 {m['ate_rotation_rmse_deg']:>6.3f}°  "
              f"纯漂移 {m['attitude_aligned_ate_rotation_rmse_deg']:>6.3f}°  "
              f"常量失配 {m['position_vs_attitude_alignment_rotation_deg']:>6.3f}°")
    print("  ⇒ 世界旋转被完全吸收（三项≈0）；常量体轴偏移【全额进门】且只表现为")
    print("    常量失配（纯漂移≈0）⇒ 门 ≈ sqrt(常量² + 漂移²) 的分解成立\n")


def synthetic_gate(Pg, Qg, residual, mode, rng, sigma_mm=None):
    """姿态完美 + 位置带真实误差 ⇒ 返回 (门, 常量失配, 对齐旋转误差)。

    世界系取重合（M=I），所以 R_q ≡ I，三者应当相等。
    """
    if mode == "residual_rotated":
        R = Rotation.random(random_state=rng).as_matrix()
        n = residual @ R.T
    elif mode == "residual_shuffled":
        n = residual[rng.permutation(len(residual))]
    else:
        n = rng.normal(scale=sigma_mm / 1000.0, size=Pg.shape)
    Psyn = Pg + n
    R_a, _ = E.rigid_align(Psyn, Pg)
    rot = Rotation.from_matrix(R_a)
    m = E.pose_errors(Psyn, Qg, Pg, Qg, 30)
    return (m["ate_rotation_rmse_deg"],
            m["position_vs_attitude_alignment_rotation_deg"],
            float(np.degrees(rot.magnitude())))


def main():
    probe_convention()

    cells = sorted(ROOT.glob("**/fusion_current/tight/trajectory_fused.csv"))
    cells = [c for c in cells
             if (c.parents[2] / "lighthouse_body_ground_truth.csv").exists()]
    print(f"扫描 {len(cells)} 个 cell\n")
    print(f"{'cell':<44}{'尺度mm':>8}{'ATE mm':>8}{'实测常量':>9}"
          f"{'合成A':>8}{'合成B':>8}{'合成C':>8}")
    print("-" * 95)

    rng = np.random.default_rng(0)
    rows = []
    for fused in cells:
        name = str(fused.parent.parent).replace(str(ROOT) + "/", "")
        Pe, Qe, Pg, Qg = load_cell(fused)
        m = E.pose_errors(Pe, Qe, Pg, Qg, 30)
        R_a, t_a = E.rigid_align(Pe, Pg)
        residual = Pe @ R_a.T + t_a - Pg          # 真实位置残差（对齐后）
        sigma = float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))

        syn = {"A": [], "B": [], "C": []}
        for _ in range(40):
            syn["A"].append(synthetic_gate(Pg, Qg, residual, "residual_rotated", rng)[0])
            syn["B"].append(synthetic_gate(Pg, Qg, residual, "residual_shuffled", rng)[0])
            syn["C"].append(synthetic_gate(Pg, Qg, residual, "gaussian", rng,
                                           sigma_mm=sigma * 1e3)[0])
        obs = m["position_vs_attitude_alignment_rotation_deg"]
        print(f"{name:<44}{extent(Pg)*1e3:>8.1f}{sigma*1e3:>8.2f}{obs:>9.2f}"
              f"{np.median(syn['A']):>8.2f}{np.median(syn['B']):>8.2f}"
              f"{np.median(syn['C']):>8.2f}")
        rows.append(dict(name=name, ext=extent(Pg) * 1e3, ate=sigma * 1e3, obs=obs,
                         A=float(np.median(syn["A"])), B=float(np.median(syn["B"])),
                         C=float(np.median(syn["C"]))))

    if not rows:
        return
    obs = np.array([r["obs"] for r in rows])
    print("\n=== 汇总 ===")
    for k, label in (("A", "真实残差整体旋转"), ("B", "真实残差重排"),
                     ("C", "高斯同 σ")):
        s = np.array([r[k] for r in rows])
        print(f"  合成{k} ({label:<14}) 中位 {np.median(s):.2f}°  "
              f"实测常量中位 {np.median(obs):.2f}°  "
              f"解释掉 {100*np.median(s)/np.median(obs):.0f}%  "
              f"corr={np.corrcoef(s, obs)[0, 1]:+.2f}")

    print("\n=== 缩放律：位置误差 σ → 对齐旋转误差（取尺度最小的那个 cell）===")
    fused = cells[int(np.argmin([r["ext"] for r in rows]))]
    _, _, Pg, Qg = load_cell(fused)
    print(f"  cell 尺度 {min(r['ext'] for r in rows):.1f}mm")
    print(f"  {'σ(mm)':>7}{'对齐旋转误差(°)':>18}{'σ/尺度(°)':>12}")
    for s_mm in (0.5, 1.0, 2.0, 5.0, 10.0, 20.0):
        vals = [synthetic_gate(Pg, Qg, None, "gaussian", rng, sigma_mm=s_mm)[0]
                for _ in range(60)]
        print(f"  {s_mm:>7.1f}{np.median(vals):>18.2f}"
              f"{np.degrees(s_mm / min(r['ext'] for r in rows)):>12.2f}")


if __name__ == "__main__":
    main()
