#!/usr/bin/env python3
"""把融合链的 ATE **在时间上定位**：误差是集中在开头/结尾，还是全程均匀？

## 为什么现在做

§四 的证伪检验把目标变成了一个数字：**门 = 2.0° ⟺ ATE ≈ 3–4mm**
（实测 ATE ≥ 10mm 的 8 个 cell 门 0/8 过）。当前 `fused` 的 ATE 是 2.8–9.1mm，
所以要过门就得知道**这 3–9mm 花在哪里** —— 是开头一段没收敛（可修），
还是全程均匀（只能靠更大的回路）。

已知线索（既有记录）：`batch5/g3` **开头 7.53s 相对后面被挪开 15–18mm**，
且补开头关键帧空洞「把段间互差减半但整体指标变差」⇒ 机理成立但不是修法。
这里换成**不预设结论的普查**：对每个 cell 报误差平方沿时间的分布。

## 怎么读

  * `前10%占比` / `前25%占比` 远高于 10% / 25% ⇒ 误差集中在**开头**；
  * `前半占比` ≈ 50% ⇒ 全程均匀（无局部病灶）；
  * `Gini`（基尼系数）接近 0 ⇒ 均匀；越大越集中。
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def load(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, plt, qlt = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return te[inside][valid], Pe[inside][valid], plt[:, 1:4]


def gini(x):
    x = np.sort(np.abs(x))
    n = len(x)
    if n == 0 or x.sum() == 0:
        return float("nan")
    return float((2 * np.arange(1, n + 1) - n - 1) @ x / (n * x.sum()))


def main():
    print("融合链 ATE 的时间分布（对齐后的逐帧位置误差²）\n")
    print(f"{'cell':<46}{'ATE':>6}{'前10%':>8}{'前25%':>8}{'前半':>7}"
          f"{'末25%':>8}{'Gini':>7}  峰值时刻")
    print("-" * 104)
    rows = []
    for g in sorted({f.parents[2] for f in
                     ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
        gt = g / "lighthouse_body_ground_truth.csv"
        est = g / "fusion_current/tight/trajectory_fused.csv"
        if not (gt.exists() and est.exists()):
            continue
        t, Pe, Pg = load(est, gt)
        R, tr = E.rigid_align(Pe, Pg)
        e2 = np.linalg.norm(Pe @ R.T + tr - Pg, axis=1) ** 2
        ate = float(np.sqrt(e2.mean()) * 1000)
        n = len(e2)
        frac = lambda k: 100 * e2[:max(1, int(n * k))].sum() / e2.sum()  # noqa: E731
        half = 100 * e2[:n // 2].sum() / e2.sum()
        tail = 100 * e2[int(n * 0.75):].sum() / e2.sum()
        pk = t[int(np.argmax(e2))] - t[0]
        name = str(g).replace(str(ROOT) + "/", "")
        print(f"{name:<46}{ate:>6.1f}{frac(0.10):>7.0f}%{frac(0.25):>7.0f}%"
              f"{half:>6.0f}%{tail:>7.0f}%{gini(e2):>7.3f}  +{pk:>5.1f}s")
        rows.append((name, ate, frac(0.10), frac(0.25), half, gini(e2)))

    if not rows:
        return
    A = np.array([[r[1], r[2], r[3], r[4], r[5]] for r in rows])
    print(f"\n=== 汇总（{len(rows)} cell，中位）===")
    print(f"  ATE                 {np.median(A[:,0]):.1f} mm")
    print(f"  误差² 中前 10% 占    {np.median(A[:,1]):.0f}%   （均匀时应为 10%）")
    print(f"  误差² 中前 25% 占    {np.median(A[:,2]):.0f}%   （均匀时应为 25%）")
    print(f"  误差² 中前半占       {np.median(A[:,3]):.0f}%   （均匀时应为 50%）")
    print(f"  Gini                {np.median(A[:,4]):.3f}  （0 = 完全均匀）")
    print(f"\n  前 25% 占比 > 50% 的 cell（开头明显偏重）：{int((A[:,2] > 50).sum())}/{len(rows)}")
    print(f"  前半占比在 40–60% 的 cell（全程均匀）：{int(((A[:,3] >= 40) & (A[:,3] <= 60)).sum())}/{len(rows)}")
    print("\n  ⚠「开头偏重」不等于「开头没收敛」：轨迹本身是桌面往返，")
    print("    开头段若恰好是快速/遮挡段，误差大也可能只是那段本来就难。")


if __name__ == "__main__":
    main()
