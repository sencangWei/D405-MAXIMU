#!/usr/bin/env python3
"""通用前端 A/B：给定基线 tag 与若干变体 tag，逐格报**绝对口径**的场指标。

从 `c_weight_stage_a.py` 抽出来（那份把 ref/variants/cells 写死了）。口径与判据不变：

* **判据 1（§13 尺子）**：变体必须在基线峰帧上移动 **≥3mm** 才值得评估。
* **判据 2（§23.5 边界②）**：报**对真值**的变好/变差，不报 inter-arm 差。
* **场口径（§23.3 教训）**：MAX 是单帧极值、误差一重分配就换位置 ⇒ 同时报
  p95 与超标帧占比；`proj@峰` = 把位移投影到「误差减小方向」（正值=往好的方向走）。

⚠ 前端是 MASt3R 单位（≈2.25× 真值）⇒ 必须 Sim(3) 对齐，rigid 会算成 ~1m 假残差。
⚠ 落笔前先在 `fusion89_sweep/cells.txt` 的 22 臂上普查（[[survey-22-arms-before-concluding]]）。

用法: ab_frontend.py <ref> <tag1> [tag2 ...] [--arms tight,sparse] [--cells N]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent
# 有 w0.6 基线的格（第4格 batch5/g4 无基线）
CELLS = [("20260914_validation_v10_batch", "group1"),
         ("20260914_validation_v11_holdout_batch3", "group2"),
         ("20260915_batch5_four_videos", "group3"),
         ("20260914_validation_v10_batch", "group2"),
         ("20260915_batch5_four_videos", "group2"),
         ("20260915_batch5_four_videos", "group4")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--arms", default="tight,sparse")
    ap.add_argument("--cells", type=int, default=len(CELLS))
    a = ap.parse_args()
    arms = a.arms.split(",")

    rows = []
    print(f"前端 A/B（基线 {a.ref}）\n")
    print(f"{'cell':<40}{'arm':>7}{'tag':>8}{'MAX':>9}{'@帧':>7}{'RMSE':>8}"
          f"{'p95':>8}{'>10mm':>7}{'ΔMAX':>9}{'ΔRMSE':>9}{'Δp95':>9}"
          f"{'Δ>10mm':>9}{'proj@峰':>9}{'|Δ|@峰':>9}")
    print("-" * 140)
    for batch, group in CELLS[: a.cells]:
        gt_f = ROOT / batch / group / "lighthouse_body_ground_truth.csv"
        if not gt_f.exists():
            print(f"{batch[-30:]:<40}  (无真值)")
            continue
        tg, Pg, Qg = E.load_trajectory(gt_f)
        for arm in arms:
            # ★ 基线产物必须先存在，否则 base/ref 沿用上一格陈旧值（踩过）。
            if not (HERE / "out" / batch / group / arm / a.ref
                    / "trajectory_frames.csv").exists():
                print(f"{batch[-30:]:<40}{arm:>7}   (无基线)")
                continue
            base = None
            ref = {}
            for tag in [a.ref] + a.tags:
                f = HERE / "out" / batch / group / arm / tag / "trajectory_frames.csv"
                if not f.exists():
                    print(f"{batch[-30:]:<40}{arm:>7}{tag:>8}   (缺产物)")
                    continue
                t, P, Q = E.load_trajectory(f)
                ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
                P, Q, t = P[ins][val], Q[ins][val], t[ins][val]
                gt = plt_[:, 1:4]
                s, R, tt = E.similarity_align(P, gt)
                evec = (s * (P @ R.T) + tt - gt) * 1000.0
                err = np.linalg.norm(evec, axis=1)
                rmse, mx = float(np.sqrt((err ** 2).mean())), float(err.max())
                p95 = float(np.percentile(err, 95))
                over10 = float((err > 10.0).mean())
                if base is None:
                    base = (P, evec, Q)
                    ref95, ref10 = p95, over10
                    k = int(np.argmax(err))
                ref[tag] = (mx, rmse)
                n = min(len(P), len(base[0]))
                D = (P[:n] - base[0][:n]) * s * 1000.0
                u = base[1][:n] / np.maximum(
                    np.linalg.norm(base[1][:n], axis=1), 1e-9)[:, None]
                proj, mag = (D * (-u)).sum(axis=1), np.linalg.norm(D, axis=1)
                dmx, drm = mx - ref[a.ref][0], rmse - ref[a.ref][1]
                star = "  ★动了" if (tag != a.ref and mag[k] >= 3.0) else ""
                print(f"{batch[-30:] + '/' + group:<40}{arm:>7}{tag:>8}{mx:>9.2f}"
                      f"{k:>7}{rmse:>8.2f}{p95:>8.2f}{over10 * 100:>6.1f}%"
                      f"{dmx:>+9.2f}{drm:>+9.2f}{p95 - ref95:>+9.2f}"
                      f"{(over10 - ref10) * 100:>+8.1f}%"
                      f"{proj[k]:>+9.2f}{mag[k]:>9.2f}{star}")
                rows.append(dict(batch=batch, group=group, arm=arm, tag=tag, mx=mx,
                                 rmse=rmse, p95=p95, over10=over10, k=k,
                                 d_max=dmx, d_rmse=drm, d_p95=p95 - ref95,
                                 d_over10=over10 - ref10, proj_peak=float(proj[k]),
                                 mag_peak=float(mag[k]), scale=s))
            print()

    if not rows:
        return
    print("=" * 120)
    print("■ 判据 1：必须在基线峰帧上移动 ≥3mm（§13 尺子，缺口中位 2.6mm）")
    for arm in arms:
        for tag in a.tags:
            g = [r for r in rows if r["arm"] == arm and r["tag"] == tag]
            if not g:
                continue
            m = np.array([r["mag_peak"] for r in g])
            d = np.array([r["d_max"] for r in g])
            print(f"  {arm:>6} {tag:>7}  |Δ|@峰 中位 {np.median(m):6.2f}mm（最大 {m.max():6.2f}）"
                  f"   ΔMAX 中位 {np.median(d):+6.2f}mm   MAX 改善 {int((d < 0).sum())}/{len(d)}"
                  f"   {'✅ 动了' if np.median(m) >= 3 else '❌ <3mm'}")
    print("\n■ 判据 2（§23.5 边界②）：绝对口径 —— 对真值变好还是变差")
    for tag in a.tags:
        g = [r for r in rows if r["tag"] == tag]
        if len(g) < 3:
            continue
        d = np.array([r["d_max"] for r in g])
        dr = np.array([r["d_rmse"] for r in g])
        dp = np.array([r["d_p95"] for r in g])
        do = np.array([r["d_over10"] for r in g])
        pp = np.array([r["proj_peak"] for r in g])
        print(f"  {tag:>7} n={len(g)}  MAX 改善 {int((d < 0).sum())}/{len(g)}"
              f"（中位 {np.median(d):+.2f}mm）  RMSE 改善 {int((dr < 0).sum())}/{len(g)}"
              f"（中位 {np.median(dr):+.2f}mm）  p95 改善 {int((dp < 0).sum())}/{len(g)}"
              f"（中位 {np.median(dp):+.2f}mm）")
        print(f"  {'':>7}       超标>10mm 帧占比 改善 {int((do < 0).sum())}/{len(g)}"
              f"（中位 {np.median(do) * 100:+.1f}pp）   proj@峰 为正 {int((pp > 0).sum())}/{len(g)}"
              f"（中位 {np.median(pp):+.2f}mm）")

    out = HERE / f"ab_frontend_{a.ref}_{'_'.join(a.tags)}.json"
    out.write_text(json.dumps(rows, indent=1))
    print(f"\n落盘 {out.name}")


if __name__ == "__main__":
    main()