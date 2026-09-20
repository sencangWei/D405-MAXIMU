#!/usr/bin/env python3
"""把融合链的位置误差按**轨迹自身的主平面**拆成「面内（水平）」与「法向（垂直）」。

## 为什么

既有记录里有一条从未追下去的线索：误差 **87% 是垂直航向**。
桌面轨迹近似共面 ⇒ 用 GT 轨迹的 PCA 最小主成分方向当"垂直"（不依赖外部重力定义，
因为 lighthouse 真值框架里重力方向要另外标定）。若垂直确实占绝对多数，
那么要压 ATE 就该去查 **z / 重力 / 沿重力方向的尺度**，而不是面内形状。

## 口径

误差向量 `e_i = P_est,i - P_gt,i`（先 rigid_align），
`n` = GT 轨迹 PCA 最小特征向量（平面法向）。
垂直分量 `e·n`，面内分量 `‖e - (e·n)n‖`。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CHAINS = (("fused", "fusion_current/tight/trajectory_fused.csv"),
          ("mast3r_graph", "fusion_current/tight/mast3r/trajectory_graph.csv"),
          ("vins_corr", "docker2_slam/vio_corrected_stream.csv"))


def load(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, plt, qlt = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], plt[:, 1:4]


def split(Pe, Pg):
    R, t = E.rigid_align(Pe, Pg)
    e = Pe @ R.T + t - Pg
    C = Pg - Pg.mean(0)
    _, _, Vt = np.linalg.svd(C, full_matrices=False)
    n = Vt[-1]                                       # 最小方差方向 = 平面法向
    v = e @ n                                        # 垂直分量（带符号）
    h = np.linalg.norm(e - np.outer(v, n), axis=1)   # 面内分量
    def rms(x):
        """⚠ (N,3) 数组必须按【行】取模再平均 —— `np.mean(x**2)` 是按分量平均
        （除以 3N），会把总量压低 √3 倍，曾误得 1.59 而非 2.76。"""
        x = np.asarray(x, dtype=float)
        sq = (x ** 2).sum(axis=1) if x.ndim == 2 else x ** 2
        return float(np.sqrt(np.mean(sq)) * 1000)
    # 面内再拆成沿航向 / 侧向（用 GT 逐帧速度方向）
    d = np.gradient(Pg, axis=0)
    d = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
    inplane = e - np.outer(v, n)
    along = np.einsum("ij,ij->i", inplane, d)
    lat = np.linalg.norm(inplane - along[:, None] * d, axis=1)
    return rms(e), rms(v), rms(h), rms(along), rms(lat)


def main():
    print("位置误差的轴向分解（法向 = GT 轨迹 PCA 最小主成分 ≈ 桌面法向）\n")
    for tag, rel in CHAINS:
        print(f"### [{tag}]  {rel}")
        print(f"    {'cell':<42}{'总':>7}{'垂直':>7}{'面内':>7}{'沿航向':>8}{'侧向':>7}{'垂直占比':>9}")
        rows = []
        for c in sorted({f.parents[2] for f in ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
            p, gt = c / rel, c / "lighthouse_body_ground_truth.csv"
            if not (p.exists() and gt.exists()):
                continue
            try:
                Pe, Pg = load(p, gt)
                r = split(Pe, Pg)
            except Exception as e:                                   # noqa: BLE001
                print(f"    {'':<42}  ! {e}")
                continue
            name = str(c).replace(str(ROOT) + "/", "")
            print(f"    {name:<42}{r[0]:>7.2f}{r[1]:>7.2f}{r[2]:>7.2f}{r[3]:>8.2f}{r[4]:>7.2f}"
                  f"{100*r[1]**2/r[0]**2:>8.0f}%")
            rows.append(r)
        if rows:
            A = np.array(rows)
            print(f"    {chr(45)*42}{np.median(A[:,0]):>7.2f}{np.median(A[:,1]):>7.2f}"
                  f"{np.median(A[:,2]):>7.2f}{np.median(A[:,3]):>8.2f}{np.median(A[:,4]):>7.2f}"
                  f"{100*np.median(A[:,1]**2/A[:,0]**2):>8.0f}%  ← 中位")
        print()


if __name__ == "__main__":
    main()
