#!/usr/bin/env python3
"""那个「可消常量」是**固定外参误差**（跨 take 可迁移）还是每次跑都变的随机量？

## 判据

对每个 cell 解出使门指标最小的常量**体轴**旋转 C_i（用 GT，只作 oracle）。
体轴旋转与轨迹无关，同一套硬件的 C 应当可迁移。于是留一验证：

    C_transfer(i) = 其余 cell 的 C_j 的平均      （不含 i，纯跨 take 迁移）
    门_基线(i) → 门_oracle(i)（用 C_i）→ 门_transfer(i)（用 C_transfer）

若 门_transfer ≈ 门_oracle ⇒ **固定误差**，标定/在线外参能修掉，而且可以离线标一次。
若 门_transfer ≈ 门_基线 ⇒ 每次跑都变的随机量，标定无用，只有在线估计才行。

同时报到「眼睛」：cross-take 的 C_j 之间夹角的散布。散布小 = 固定。
"""
import glob
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
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


def gate(Pe, Qe, Pg, Qg, C=None):
    Q = Qe if C is None else (Rotation.from_quat(Qe) * C).as_quat()
    R, _ = E.rigid_align(Pe, Pg)
    d = Rotation.from_quat(Qg).inv() * (Rotation.from_matrix(R) * Rotation.from_quat(Q))
    return float(np.sqrt(np.mean(np.degrees(d.magnitude()) ** 2)))


def solve_C(Pe, Qe, Pg, Qg):
    f = lambda x: gate(Pe, Qe, Pg, Qg, Rotation.from_rotvec(x))  # noqa: E731
    r = minimize(f, np.zeros(3), method="Nelder-Mead",
                 options=dict(xatol=1e-9, fatol=1e-10, maxiter=20000))
    return Rotation.from_rotvec(r.x), f(np.zeros(3)), f(r.x)


def main():
    cells = []
    for fused in sorted(ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")):
        if not (Path(fused).parents[2] / "lighthouse_body_ground_truth.csv").exists():
            continue
        name = str(Path(fused).parent.parent).replace(str(ROOT) + "/", "")
        Pe, Qe, Pg, Qg = load(fused)
        C, base, opt = solve_C(Pe, Qe, Pg, Qg)
        cells.append(dict(name=name, Pe=Pe, Qe=Qe, Pg=Pg, Qg=Qg,
                          C=C, base=base, opt=opt))
    print(f"{len(cells)} 个 cell\n")

    # —— 每 cell 的 oracle C 在体轴里的分量（度）——
    print(f"{'cell':<44}{'门':>7}{'oracle后':>9}{'C_x':>7}{'C_y':>7}{'C_z':>7}{'|C|':>7}")
    print("-" * 90)
    vecs = []
    for c in cells:
        v = np.degrees(c["C"].as_rotvec())
        vecs.append(v)
        print(f"{c['name']:<44}{c['base']:>7.2f}{c['opt']:>9.2f}"
              f"{v[0]:>7.2f}{v[1]:>7.2f}{v[2]:>7.2f}"
              f"{np.degrees(c['C'].magnitude()):>7.2f}")
    vecs = np.array(vecs)
    print(f"\n  分量均值 {vecs.mean(axis=0).round(2)}  标准差 {vecs.std(axis=0).round(2)}")
    print(f"  |C| 均值 {np.degrees([c['C'].magnitude() for c in cells]).mean():.2f}°")
    # 各 cell 的 C 两两夹角（C_i 相对于 C_j）
    ang = [np.degrees((cells[i]["C"] * cells[j]["C"].inv()).magnitude())
           for i in range(len(cells)) for j in range(i + 1, len(cells))]
    print(f"  C_i 两两夹角：中位 {np.median(ang):.2f}°  p90 {np.percentile(ang, 90):.2f}°")

    # —— 留一：跨 take 迁移的 C ——
    print(f"\n=== 留一跨 take 迁移 ===")
    print(f"{'cell':<44}{'基线':>8}{'oracle':>9}{'迁移后':>9}{'迁移/可消':>11}")
    print("-" * 84)
    good = 0
    for i, c in enumerate(cells):
        others = [cells[j]["C"] for j in range(len(cells)) if j != i]
        Cm = Rotation.concatenate(others).mean()
        gt_ = gate(c["Pe"], c["Qe"], c["Pg"], c["Qg"], Cm)
        removable = c["base"] - c["opt"]
        got = c["base"] - gt_
        frac = 100 * got / removable if removable > 1e-9 else float("nan")
        good += frac > 50
        print(f"{c['name']:<44}{c['base']:>8.2f}{c['opt']:>9.2f}{gt_:>9.2f}{frac:>10.0f}%")
    print(f"\n  迁移能吃掉 oracle 可消量的 >50% 的 cell：{good}/{len(cells)}")

    # —— 一个全局常量：所有 cell 共用一个 C ——
    Cg = Rotation.concatenate([c["C"] for c in cells]).mean()
    print(f"\n=== 全局单一常量 C（所有 cell 的 oracle C 的均值，|C|="
          f"{np.degrees(Cg.magnitude()):.2f}°）===")
    base = np.array([c["base"] for c in cells])
    glob_ = np.array([gate(c["Pe"], c["Qe"], c["Pg"], c["Qg"], Cg) for c in cells])
    opt = np.array([c["opt"] for c in cells])
    print(f"  基线中位 {np.median(base):.2f}°  全局 C 后中位 {np.median(glob_):.2f}°  "
          f"oracle 中位 {np.median(opt):.2f}°")
    print(f"  改善的 cell：{int((glob_ < base).sum())}/{len(cells)}  "
          f"中位改善 {np.median(base - glob_):.2f}°  "
          f"（oracle 中位改善 {np.median(base - opt):.2f}°）")
    print(f"  用全局 C 后仍 >2.0° 门的 cell：{int((glob_ > 2.0).sum())}/{len(cells)}"
          f"   （基线 {int((base > 2.0).sum())}/{len(cells)}）")


if __name__ == "__main__":
    main()
