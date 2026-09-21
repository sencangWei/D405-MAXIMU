#!/usr/bin/env python
"""C2 判据：臂的 fused ATE **逐帧误差剖面** 与 prod 对照。

为什么需要它：C2 的 `ate_translation_max` 两臂只差 ±0.2–0.4mm，但两条 fused 轨迹
逐帧最大差到 5.2mm（尾部单调累积）。要判「臂有没有碰到那个绑定误差块」，
必须看**两臂的误差峰落在哪一帧**：同帧 ⇒ 臂没碰到绑定误差；异帧 ⇒ 臂换了绑定误差。

对齐法复用 `evaluate_slam_ground_truth.py`（SE3 无尺度）。估计系侧的外参转换
（camera→body）是刚性的，套在算对齐之前不改变残差集合，故此处省略不影响结论。

用法: fused_error_profile.py <prod fused csv> <arm fused csv> <gt csv>
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
from evaluate_slam_ground_truth import (  # noqa: E402
    interpolate_ground_truth,
    load_trajectory,
    rigid_align,
)


def profile(est_csv: Path, gt_csv: Path):
    et, ep, eq = load_trajectory(est_csv)
    gt_t, gt_p, gt_q = load_trajectory(gt_csv)
    inside, valid, gt_sel, _ = interpolate_ground_truth(
        et, gt_t, gt_p, gt_q, 0.1
    )
    # 与 interpolate_ground_truth 内部同一套筛选：inside -> valid
    keep = np.zeros(len(et), bool)
    keep[np.flatnonzero(inside)] = valid
    e_t, e_p = et[keep], ep[keep]
    gt_p_sel = gt_sel[:, 1:]
    R, t = rigid_align(e_p, gt_p_sel)
    err = np.linalg.norm(e_p @ R.T + t - gt_p_sel, axis=1)
    return e_t, err


def main():
    prod, arm, gt = (Path(a) for a in sys.argv[1:4])
    for label, p in (("对照(prod)", prod), ("本臂(arm)", arm)):
        t, err = profile(p, gt)
        k = int(np.argmax(err))
        print(f"{label:<12} n={len(err)}  max={err.max()*1000:7.3f}mm @ t={t[k]:.2f}s (idx={k})  "
              f"p95={np.percentile(err, 95)*1000:6.3f}mm  rmse={np.sqrt((err**2).mean())*1000:6.3f}mm")
        # 误差峰邻域占比：前 10% / 前 25% 的样本贡献了多少平方和
        s = np.sort(err)[::-1] ** 2
        tot = s.sum()
        for frac in (0.10, 0.25):
            m = max(1, int(len(s) * frac))
            print(f"             前{int(frac*100)}%样本贡献 {s[:m].sum()/tot*100:5.1f}% 平方误差")


if __name__ == "__main__":
    main()