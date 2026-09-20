#!/usr/bin/env python3
"""鼓包的**性质**判定：是「时间滞后/超前」还是「真实位移」？

## 为什么

`max_frame_forensics.py` 在 v10b/g1/tight 上量到：超标那两帧的误差 ≈
**96ms 的真值位移**（真值速度 129mm/s、误差 12.4mm）。这个量级有两种完全不同的解释：

* **时间对齐问题** —— 估计在某处相对真值滞后/超前一个 Δt，误差 ≈ v·Δt，
  方向**沿航向**。修法是时间轴，不是空间。
* **真实位移** —— 估计被推离了真值，方向**主要是垂直航向**的。

已排除的相邻证据：09-14 的 `diagnostic_smoothing_*` 四点扫描里，
σ 0.025→0.150 让 **MAX 从 13.84 单调变差到 14.99**、RMSE 3.17→3.92
⇒ 「加重平滑」不是药方，而且**平滑反而抬高最坏点**，这本身就偏向「不是空间鼓包」。

## 做法

对每个超标帧算误差矢量 `d = gt - aligned_est`，再分解：

* `along = d·v̂`（v̂ = 真值速度单位向量）—— 沿航向分量
* `cross = ‖d − along·v̂‖` —— 垂直航向分量
* 并给出**该帧的等效时移** `along / ‖v‖`

再在鼓包窗口内做一次**最优刚性时移**搜索：把估计在 ±0.5s 内平移，
看误差能被压掉多少 —— 若能压到很小，就是时间问题。

用法: bump_shift_analysis.py <estimate.csv> <ground_truth.csv> [--peak N]
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402


def load(est, gt_path):
    tg, Pg, Qg = E.load_trajectory(Path(gt_path))
    t, P, Q = E.load_trajectory(Path(est))
    inside, valid, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    P, t = P[inside][valid], t[inside][valid]
    gt = plt_[:, 1:4]
    R, tt = E.rigid_align(P, gt)
    return t, P @ R.T + tt, gt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("estimate")
    ap.add_argument("ground_truth")
    ap.add_argument("--peak", type=int, default=-1)
    a = ap.parse_args()
    t, est, gt = load(a.estimate, a.ground_truth)
    err = np.linalg.norm(est - gt, axis=1)
    dt = np.diff(t, prepend=t[0]); dt[dt <= 0] = np.median(dt[dt > 0])
    vel = np.diff(gt, axis=0, prepend=gt[:1]) / dt[:, None]
    speed = np.linalg.norm(vel, axis=1)

    peak = a.peak if a.peak >= 0 else int(np.argmax(err))
    print(f"{a.estimate}\n  帧数 {len(err)}  RMSE {np.sqrt((err**2).mean())*1000:.3f}mm  "
          f"MAX {err.max()*1000:.2f}mm @ idx {peak}  rot 无关\n")
    print(f"  峰附近 15 帧的误差方向分解（真值速度 {speed[peak]*1000:.1f} mm/s）")
    print(f"{'idx':>6}{'err(mm)':>9}{'沿航向':>9}{'垂直航向':>10}"
          f"{'等效时移(ms)':>13}{'真值速度':>11}")
    print("-" * 62)
    lo, hi = max(0, peak - 7), min(len(err), peak + 8)
    for i in range(lo, hi):
        v = vel[i]
        n = np.linalg.norm(v)
        if n > 1e-6:
            along = float(np.dot(est[i] - gt[i], v / n))
        else:
            along = 0.0
        cross = float(np.sqrt(max(err[i] ** 2 - along ** 2, 0.0)))
        shift = along / n * 1000 if n > 1e-6 else float("nan")
        star = " ◀" if i == peak else ""
        print(f"{i:>6}{err[i]*1000:>9.2f}{along*1000:>9.2f}{cross*1000:>10.2f}"
              f"{shift:>13.1f}{n*1000:>9.1f}mm/s{star}")

    # 窗口内最优整体时移
    print(f"\n  窗口 [{lo},{hi}) 内把估计整体平移 ±0.5s 的最优解：")
    best = None
    win = np.arange(lo, hi)
    for shift_s in np.arange(-0.5, 0.5001, 0.005):
        # 在时间轴上平移估计：est(t) → est(t - shift)
        e = np.array([est[np.argmin(np.abs(t - (t[i] - shift_s)))] for i in win])
        e_all = np.array([est[np.argmin(np.abs(t - (t[i] - shift_s)))]
                          for i in range(len(t))])
        w = float(np.sqrt(((e - gt[win]) ** 2).sum(axis=1).mean()) * 1000)
        g = float(np.sqrt(((e_all - gt) ** 2).sum(axis=1).mean()) * 1000)
        if best is None or w < best[1]:
            best = (shift_s, w, g)
    w0 = float(np.sqrt(((est[win] - gt[win]) ** 2).sum(axis=1).mean()) * 1000)
    g0 = float(np.sqrt((err ** 2).mean()) * 1000)
    print(f"    窗口 RMS：不移 {w0:.2f}mm  →  最优时移 {best[0]*1000:+.0f}ms 时 {best[1]:.2f}mm")
    print(f"    整条 RMS：不移 {g0:.2f}mm  →  同一时移 {best[2]:.2f}mm")
    frac = max(0.0, 1 - best[1] / max(w0, 1e-9))
    print("\n  判读：")
    if best[1] < 0.5 * w0:
        print(f"    ⇒ 一个**全局时移**就能把鼓包压掉 {frac*100:.0f}% ⇒ 是**时间对齐**问题，"
              "不是空间误差；药方在时间轴。")
    else:
        print(f"    ⇒ 时移最多只压掉 {frac*100:.0f}% ⇒ 不是简单时移，"
              "鼓包是**真实位移**（或时变的时间误差）。")
    r = best[0] * speed[peak] * 1000
    print(f"    参照：该峰处最优时移 {best[0]*1000:+.0f}ms 只对应 "
          f"{abs(r):.2f}mm 位移（真值速度 {speed[peak]*1000:.0f}mm/s），"
          f"而峰值误差 {err[peak]*1000:.2f}mm")


if __name__ == "__main__":
    main()