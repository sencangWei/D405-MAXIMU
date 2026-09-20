#!/usr/bin/env python3
"""全语料：把每条轨迹的误差**按航向分解**，看能不能把过关/不过关的 cell 分开。

## 动机

`bump_shift_analysis.py` 在 v10b/g1/tight 上定过：峰处 13.25/13.46mm **垂直航向**、
最优时移压不掉 ⇒ 是横向**真实位移**，不是时间对齐。

把同一套分解搬到全语料后，出现**两种形状**（见 `peak_decomposition` 输出）：
* 鼓包只有 2–5 帧、垂直占比 68–100% 的（v10b/g1 两臂、v11b3/g2 tight、batch5/g1 sparse、batch5/g4 tight）
* 鼓包 13–47 帧、**沿航向**占 72–79% 的（v10b/g2 两臂、batch5/g2 两臂、coll4 两条）

**沿航向 = 走快了/走慢了 ⇒ 速度或尺度型**；**垂直航向 = 横向被推离 ⇒ 形状/位移型**。
这正是 [[gate-failure-taxonomy-20260919]] 里「A 类 vs B 类…机制仍未定位」想找的东西。

## 注意（这条决定了怎么做才不算错）

真值速度接近 0 时**航向本身是噪声**，沿/垂直分解无意义。
所以本脚本只在 `真值速度 > --min-speed`（默认 30mm/s）的帧上做分解，
并**单独报告被排除的帧占比**——排除得太多时该结论对那条 cell 不成立。

用法: heading_decomposition.py [cell/arm ...]（不给则扫全部 fusion_v2）
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def one(est, gt_path, min_speed=0.03):
    tg, Pg, Qg = E.load_trajectory(gt_path)
    t, P, Q = E.load_trajectory(est)
    ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    P, t = P[ins][val], t[ins][val]
    g = plt_[:, 1:4]
    R, tt = E.rigid_align(P, g)
    est_a = P @ R.T + tt
    err = np.linalg.norm(est_a - g, axis=1)
    dt = np.diff(t, prepend=t[0]); dt[dt <= 0] = np.median(dt[dt > 0])
    v = np.diff(g, axis=0, prepend=g[:1]) / dt[:, None]
    sp = np.linalg.norm(v, axis=1)
    fast = sp > min_speed
    # 沿/垂直分解（只在有航向的帧上）
    vh = np.zeros_like(v); vh[fast] = v[fast] / sp[fast, None]
    d = est_a - g
    along = np.einsum("ij,ij->i", d, vh)
    cross = np.sqrt(np.maximum(err ** 2 - along ** 2, 0.0))
    i = int(np.argmax(err))
    lo, hi = max(0, i - 25), min(len(err), i + 26)
    return {
        "n": len(err),
        "rmse": float(np.sqrt((err ** 2).mean()) * 1000),
        "max": float(err.max() * 1000),
        "rot": None,
        "rms_along_fast": float(np.sqrt((along[fast] ** 2).mean()) * 1000) if fast.any() else float("nan"),
        "rms_cross_fast": float(np.sqrt((cross[fast] ** 2).mean()) * 1000) if fast.any() else float("nan"),
        "fast_frac": float(fast.mean()),
        "peak_speed": float(sp[i] * 1000),
        "peak_cross_frac": float(cross[i] / max(err[i], 1e-12)),
        "wide": int((err[lo:hi] > 0.010).sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cells", nargs="*")
    ap.add_argument("--min-speed", type=float, default=0.03)
    a = ap.parse_args()
    sel = set(a.cells)
    rows = []
    for p in sorted(ROOT.glob("*/group*/fusion_v2/*/trajectory_fused.csv")):
        cell = p.parents[2]
        key = f"{cell.relative_to(ROOT)}/{p.parent.name}"
        if sel and key not in sel:
            continue
        gt = cell / "lighthouse_body_ground_truth.csv"
        if not gt.exists():
            continue
        rows.append((str(cell.relative_to(ROOT)), p.parent.name,
                     one(p, gt, a.min_speed)))
    print(f"按航向分解（只统计真值速度 > {a.min_speed*1000:.0f}mm/s 的帧）；{len(rows)} 条\n")
    print(f"{'cell':<42}{'臂':>7}{'RMSE':>7}{'MAX':>7}{'沿RMS':>8}{'垂直RMS':>9}"
          f"{'沿占比':>8}{'峰速':>8}{'峰垂直':>8}{'宽帧':>6}{'快帧占比':>9}")
    print("-" * 122)
    for c, arm, r in rows:
        tot = np.hypot(r["rms_along_fast"], r["rms_cross_fast"])
        print(f"{c:<42}{arm:>7}{r['rmse']:>7.2f}{r['max']:>7.2f}{r['rms_along_fast']:>8.2f}"
              f"{r['rms_cross_fast']:>9.2f}{r['rms_along_fast']/max(tot,1e-9)*100:>7.1f}%"
              f"{r['peak_speed']:>6.0f}mm/s{r['peak_cross_frac']*100:>7.1f}%"
              f"{r['wide']:>6}{r['fast_frac']*100:>8.1f}%")
    ok = np.array([r["max"] <= 10.0 for _, _, r in rows])
    wide = np.array([r["wide"] for _, _, r in rows], dtype=float)
    along = np.array([r["rms_along_fast"] / max(np.hypot(r["rms_along_fast"],
                    r["rms_cross_fast"]), 1e-9) for _, _, r in rows])
    mx = np.array([r["max"] for _, _, r in rows])
    print("-" * 122)
    print(f"  max≤10mm 的条数: {int(ok.sum())}/{len(ok)}")
    if len(mx) > 2:
        print(f"  corr(鼓包宽度, MAX)      = {np.corrcoef(wide, mx)[0,1]:+.3f}")
        print(f"  corr(沿航向占比, MAX)    = {np.corrcoef(along, mx)[0,1]:+.3f}")
        print(f"  corr(鼓包宽度, 沿航向占比) = {np.corrcoef(wide, along)[0,1]:+.3f}")
    print("\n判读：沿航向占比高的 cell 是「速度/尺度型」，垂直占比高的是「横向位移型」。"
          "两类应该用不同的药方：前者动尺度/速度，后者动局部形状。")


if __name__ == "__main__":
    main()