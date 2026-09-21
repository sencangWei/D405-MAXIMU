#!/usr/bin/env python
"""C1 判据: 前端 trajectory_frames.csv 的「形状位移」。

ATE 在全局刚体对齐之后才算, 所以**整体平移/旋转不构成误差** —— 唯一有意义的是
轨迹的**内部形状**变了多少。本脚本对每条臂的前端输出做:

  1. global : 用相似变换(含尺度)把整条臂轨迹对齐到现役前端输出 => 逐帧残差
  2. seg     : 按帧序切 K 段, 每段**各自**对齐 => 每段的平移/旋转量; 段间离散度
              即「段与段之间被挪开多少」, 正是历史失败模式的量 ([[point-inspection-and-stitch-20260919]])

单位: MASt3R 单位 × scale_m_per_mast3r_unit × 1000 => mm (取该格 [6/8] 的 selected 尺度常数)。
判据(计划): global.p95 或 seg.spread 需 >= 8-26 mm 才可能传到 fused ATE。
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np


def read_traj(p: Path):
    a = np.genfromtxt(p, delimiter=",", names=True)
    return a["t_sec"], np.stack([a["x"], a["y"], a["z"]], axis=1)


def umeyama_sim(src: np.ndarray, dst: np.ndarray):
    """返回 (R, s, t) 使 s*R@src + t ≈ dst (含尺度, Umeyama 1991)。"""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    S, D = src - mu_s, dst - mu_d
    C = D.T @ S / len(src)
    U, sv, Vt = np.linalg.svd(C)
    d = np.ones(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        d[-1] = -1.0
    R = U @ np.diag(d) @ Vt
    s = (sv * d).sum() / (S ** 2).sum() * len(src)
    return R, s, mu_d - s * R @ mu_s


def apply(T, X):
    R, s, t = T
    return (s * (R @ X.T)).T + t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", required=True, help="cell 相对路径")
    ap.add_argument("--arms", nargs="+", required=True)
    ap.add_argument("--segments", type=int, default=10)
    ap.add_argument("--json-out", type=Path)
    a = ap.parse_args()

    root = Path("/home/robot/ego_vio_humble")
    G = root / "reports/lighthouse_umi_workflow" / a.cell
    SRC = G / "fusion/sparse/mast3r"
    SWEEP = G / "frontend_matching_sweep_20260922"

    rep = json.loads((SRC / "graph_fusion_report.json").read_text())
    scale_mm = rep["metric_scale_consistency"]["selected_scale_m_per_mast3r_unit"] * 1000.0

    t_ref, P = read_traj(SRC / "trajectory_frames.csv")
    print(f"cell {a.cell}   参考=现役前端 {len(P)} 帧   MASt3R->mm 因子 {scale_mm:.3f}")
    print(f"{'arm':<16}{'n':>6}{'全局max':>10}{'全局p95':>10}{'全局rmse':>10}"
          f"{'段t离散':>10}{'段rot离散':>11}"
          f"   {'间距max':>8}{'间距p95':>9}{'间距mean':>9}")

    rows = {}
    for arm in a.arms:
        f = SWEEP / arm / "trajectory_frames.csv"
        if not f.exists():
            print(f"{arm:<16}  <缺失> {f}")
            continue
        t, A = read_traj(f)
        # 按时间戳对齐 (两跑同 dataset, 正常情况下逐帧一致)
        if len(t) != len(t_ref) or not np.allclose(t, t_ref, atol=1e-6):
            common, ia, ib = np.intersect1d(t, t_ref, return_indices=True)
            A, P2, t_ref2 = A[ia], P[ib], t_ref[ib]
            warn = f"  ⚠ 帧集不同({len(t)} vs {len(t_ref)}), 取交集 {len(common)}"
        else:
            P2, warn = P, ""

        R, s, tr = umeyama_sim(A, P2)
        resid = np.linalg.norm(apply((R, s, tr), A) - P2, axis=1) * scale_mm

        # 分段各自对齐 => 段间离散度。
        # ⚠ K 大时（~30 帧/段）逐段相似对齐**病态**（短段约束不住变换），
        # 这个数会假性爆到几十 mm —— 只作 K 小时的粗看。
        K = a.segments
        edges = np.linspace(0, len(A), K + 1).astype(int)
        ts, rots = [], []
        for k in range(K):
            sl = slice(edges[k], edges[k + 1])
            if edges[k + 1] - edges[k] < 10:
                continue
            Rk, sk, tk = umeyama_sim(A[sl], P2[sl])
            ts.append(np.linalg.norm(tk) * scale_mm)
            # 相对全局对齐的额外旋转
            dR = Rk @ R.T
            ang = np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1)))
            rots.append(ang)
        ts, rots = np.array(ts), np.array(rots)

        # ★ 良态的局部度量：全局对齐后，比较**相邻段质心的间距向量**。
        # 不逐段对齐 => 无病态；直接量「段与段之间被挪开多少」（历史失败模式）。
        Ac = (s * (R @ A.T)).T + tr          # 臂，已进参考系
        cA = np.stack([Ac[edges[k]:edges[k + 1]].mean(0) for k in range(K)])
        cR = np.stack([P2[edges[k]:edges[k + 1]].mean(0) for k in range(K)])
        dA, dR = np.diff(cA, axis=0), np.diff(cR, axis=0)
        gap = np.linalg.norm(dA - dR, axis=1) * scale_mm

        rows[arm] = dict(
            n=int(len(A)), global_max_mm=float(resid.max()),
            global_p95_mm=float(np.percentile(resid, 95)),
            global_rmse_mm=float(np.sqrt((resid ** 2).mean())),
            seg_t_spread_mm=float(ts.max() - ts.min()) if len(ts) else 0.0,
            seg_t_max_mm=float(ts.max()) if len(ts) else 0.0,
            seg_rot_spread_deg=float(rots.max() - rots.min()) if len(rots) else 0.0,
            gap_max_mm=float(gap.max()), gap_p95_mm=float(np.percentile(gap, 95)),
            gap_mean_mm=float(gap.mean()),
            warn=warn,
        )
        r = rows[arm]
        print(f"{arm:<16}{r['n']:>6}{r['global_max_mm']:>10.3f}{r['global_p95_mm']:>10.3f}"
              f"{r['global_rmse_mm']:>10.3f}{r['seg_t_spread_mm']:>10.3f}"
              f"{r['seg_rot_spread_deg']:>11.3f}   {r['gap_max_mm']:>8.3f}{r['gap_p95_mm']:>9.3f}"
              f"{r['gap_mean_mm']:>9.3f}{warn}")

    if a.json_out:
        a.json_out.parent.mkdir(parents=True, exist_ok=True)
        a.json_out.write_text(json.dumps(
            {"cell": a.cell, "scale_mm_per_mast3r_unit": scale_mm, "arms": rows}, indent=1))
        print(f"\n-> {a.json_out}")


if __name__ == "__main__":
    sys.exit(main())