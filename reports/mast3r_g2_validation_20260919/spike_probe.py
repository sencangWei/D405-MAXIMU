#!/usr/bin/env python3
"""探针: 分析 09-14 融合轨迹里的 ATE 刺峰结构, 并测试无真值(自一致性)检出能力.

只读, 不写任何产物.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402


def per_sample_ate(est_csv: Path, gt_csv: Path, max_gap=0.1, delta=30):
    est_t, est_p, est_q = E.load_trajectory(est_csv)
    gt_t, gt_p, gt_q = E.load_trajectory(gt_csv)
    inside, valid, interp, interp_q = E.interpolate_ground_truth(
        est_t, gt_t, gt_p, gt_q, max_gap
    )
    sel_p = est_p[inside][valid]
    sel_q = est_q[inside][valid]
    gt_sel = interp[:, 1:]
    R, t = E.rigid_align(sel_p, gt_sel)
    aligned = sel_p @ R.T + t
    ate = np.linalg.norm(aligned - gt_sel, axis=1)
    # 原始估计里被选中的下标与时间
    idx = np.where(inside)[0][valid]
    return est_t[idx], ate, sel_p, idx


def loo_residual(pos: np.ndarray, k: int) -> np.ndarray:
    """留一法: 用 i-k 与 i+k 线性插值预测 i, 返回残差(米). 端点用单侧外推."""
    n = len(pos)
    r = np.full(n, np.nan)
    for i in range(n):
        a, b = i - k, i + k
        if a >= 0 and b < n:
            w = (i - a) / (b - a)
            pred = pos[a] * (1 - w) + pos[b] * w
        elif a < 0 and b < n:
            pred = pos[0] + (pos[b] - pos[0]) * (i / b) if b else pos[0]
        elif a >= 0 and b >= n:
            pred = pos[n - 1] + (pos[a] - pos[n - 1]) * ((n - 1 - i) / (n - 1 - a)) if n - 1 - a else pos[n - 1]
        else:
            continue
        r[i] = np.linalg.norm(pos[i] - pred)
    return r


def main():
    B = Path(sys.argv[1] if len(sys.argv) > 1 else
             "/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/"
             "20260914_validation_v11_holdout_batch3/group1")
    est = B / "fusion" / "trajectory_fused.csv"
    gt = B / "lighthouse_body_ground_truth.csv"
    t, ate, pos, idx = per_sample_ate(est, gt)

    print(f"=== {B.name} ===")
    print(f"样本数 {len(ate)}   时间跨度 {t[0]:.3f}..{t[-1]:.3f} s "
          f"(≈{(t[-1]-t[0])/(len(t)-1):.4f} s/帧)")

    order = np.argsort(ate)[::-1]
    print("\n--- ATE 最大的 12 个点 ---")
    print(f"{'排名':>4} {'原下标':>7} {'时间(s)':>9} {'相对t':>7} {'ATE(mm)':>8}")
    for rank, i in enumerate(order[:12], 1):
        rel = t[i] - t[0]
        print(f"{rank:>4} {idx[i]:>7} {t[i]:>9.3f} {rel:>7.3f} {ate[i]*1000:>8.3f}")

    over = np.where(ate > 0.010)[0]
    print(f"\n超 10mm 的点数: {len(over)} / {len(ate)}  "
          f"({100*len(over)/len(ate):.4f}%)")
    print(f"超限点原下标: {list(idx[over])}")
    # 是否相邻(连续段) 还是孤立
    gaps = np.diff(over) if len(over) > 1 else np.array([])
    print(f"超限点之间的下标间隔: {list(gaps)}")

    print("\n--- 无真值自一致性检出(留一法线性插值残差) ---")
    for k in (1, 2, 3, 5):
        r = loo_residual(pos, k)
        fin = r[np.isfinite(r)]
        med = np.median(fin)
        mad = np.median(np.abs(fin - med))
        sigma = 1.4826 * mad
        print(f"\n k={k}: 中位残差 {med*1000:.4f} mm, 稳健σ {sigma*1000:.4f} mm")
        for mult in (3, 5, 10):
            thr = med + mult * sigma
            flag = np.where(np.isfinite(r) & (r > thr))[0]
            hit = len(set(flag.tolist()) & set(over.tolist()))
            print(f"   阈值=中位+{mult}σ={thr*1000:7.3f} mm -> 标记 {len(flag):3d} 个, "
                  f"其中命中真超限点 {hit}/{len(over)}"
                  + (f"   标记下标={list(idx[flag])[:20]}" if len(flag) <= 20 else ""))
        # 残差最大的几个
        top = np.argsort(np.where(np.isfinite(r), r, -1))[::-1][:6]
        print(f"   残差最大的6个: "
              + ", ".join(f"#{idx[i]}({r[i]*1000:.2f}mm)" for i in top))


if __name__ == "__main__":
    main()
