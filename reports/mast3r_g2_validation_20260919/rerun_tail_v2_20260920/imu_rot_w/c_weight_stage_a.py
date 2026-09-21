#!/usr/bin/env python3
"""§24.4 候选 A/B：把匹配置信度 C 从**硬门**变成**比例权重**（前端 `[1/8]`）。

## 为什么是它

§24.2 用绝对误差口径定案：尾部每级都在变好，**误差全是前端继承来的**（30.6 → 17.7mm）。
要过 10mm 门，前端得从 30.6 降到 ~17mm（**−45%**），而 §20.3 那道硬门只动了 0.71mm。
穷举已封死 `[8/9]` 参数族（§19）、`[8/9]` 后处理加权（§14）、`[7/8]` 运行时旋钮、
前端布点族（§12/§17）⇒ 只剩 §20.4 这条被代码直接指着的机理：

    tracker.py `sqrt_info = 1/sigma · valid · sqrt(Qk)` —— C 从没进过权重。
    实测 36% 的匹配坐在 C≤1.02 的尾部，却和好匹配**等权全速**在拉。

⚠ 与 §20.3 的**二值硬门**不是一回事：硬门丢掉匹配 ⇒ match_frac 崩 ⇒ 重定位风暴；
软权重保留匹配只降权，失败模式不同，不可互推。

## 实现与判据

`C_weight_floor`（tracker.py，纯 Python，不用重建）：
`w = floor + (1−floor)·clip((C−1.0)/0.5, 0, 1)`。
`floor=1.0` 逐位等价上游；`floor<1.0` 才生效。

**预先登记的尺子**（§13/§14 定死的，不事后改）：
「只失败在 max」那批格的**缺口中位 2.6mm** ⇒ 候选杠杆必须在那个 30–50 帧宽的
**鼓包上移动 ≥3mm** 才值得评估。

本脚本同时报**绝对口径**（对真值的 MAX/RMSE 变好还是变差）——§23.5 边界②的教训：
inter-arm 差动了不等于对真值变好。

用法: c_weight_stage_a.py
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent
REF = "w0.6"                                   # 同 harness 基线（= floor 1.0，逐字节已证）
VARIANTS = ["Cw050", "Cw000"]
CELLS = [("20260914_validation_v10_batch", "group1"),
         ("20260914_validation_v11_holdout_batch3", "group2"),
         ("20260915_batch5_four_videos", "group3"),
         ("20260915_batch5_four_videos", "group4")]


def main():
    rows = []
    print(f"前端 C-as-weight A/B（基线 {REF}=floor 1.0，逐字节=上游）\n")
    print(f"{'cell':<44}{'arm':>7}{'tag':>7}{'MAX':>9}{'@帧':>7}{'RMSE':>8}"
          f"{'ΔMAX':>9}{'ΔRMSE':>9}{'proj@峰':>9}{'|Δ|@峰':>9}{'Δrot°':>8}")
    print("-" * 122)
    for batch, group in CELLS:
        gt_f = ROOT / batch / group / "lighthouse_body_ground_truth.csv"
        if not gt_f.exists():
            print(f"{batch[-30:]:<44}  (无真值)")
            continue
        tg, Pg, Qg = E.load_trajectory(gt_f)
        for arm in ("tight", "sparse"):
            # ★ 基线产物必须先存在，否则 base/ref 会沿用上一格的陈旧值（踩过）。
            if not (HERE / "out" / batch / group / arm / REF
                    / "trajectory_frames.csv").exists():
                print(f"{batch[-30:]:<44}{arm:>7}   (无基线，整格跳过)")
                continue
            base = None
            ref = {}
            for tag in [REF] + VARIANTS:
                f = HERE / "out" / batch / group / arm / tag / "trajectory_frames.csv"
                if not f.exists():
                    print(f"{batch[-30:]:<44}{arm:>7}{tag:>7}   (缺产物)")
                    continue
                t, P, Q = E.load_trajectory(f)
                ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
                P, Q, t = P[ins][val], Q[ins][val], t[ins][val]
                gt = plt_[:, 1:4]
                # 前端是 MASt3R 单位（≈2.25× 真值）⇒ 必须 Sim(3)，rigid 会算成 ~1m 假残差。
                s, R, tt = E.similarity_align(P, gt)
                evec = (s * (P @ R.T) + tt - gt) * 1000.0
                err = np.linalg.norm(evec, axis=1)
                rmse, mx = float(np.sqrt((err ** 2).mean())), float(err.max())
                if base is None:
                    base = (P, evec, Q)
                    k = int(np.argmax(err))
                ref[tag] = (mx, rmse)
                n = min(len(P), len(base[0]), len(base[1]))
                D = (P[:n] - base[0][:n]) * s * 1000.0
                # 单位误差方向：把位移投影到「当前误差方向」= 沿误差走多少（§13 口径）
                u = base[1][:n] / np.maximum(
                    np.linalg.norm(base[1][:n], axis=1), 1e-9)[:, None]
                proj = (D * (-u)).sum(axis=1)
                mag = np.linalg.norm(D, axis=1)
                drot = float(np.median(2 * np.degrees(np.arccos(np.clip(
                    np.abs((Q[:n] * base[2][:n]).sum(axis=1)), 0, 1)))))
                dmx, drm = mx - ref[REF][0], rmse - ref[REF][1]
                star = "  ★动了" if (tag != REF and mag[k] >= 3.0) else ""
                print(f"{batch[-30:] + '/' + group:<44}{arm:>7}{tag:>7}{mx:>9.2f}"
                      f"{k:>7}{rmse:>8.2f}{dmx:>+9.2f}{drm:>+9.2f}"
                      f"{proj[k]:>+9.2f}{mag[k]:>9.2f}{drot:>8.3f}{star}")
                rows.append(dict(batch=batch, group=group, arm=arm, tag=tag, mx=mx,
                                 rmse=rmse, k=k, d_max=dmx, d_rmse=drm,
                                 proj_peak=float(proj[k]), mag_peak=float(mag[k]),
                                 drot=drot, scale=s))
            print()
    (HERE / "c_weight_stage_a_rows.json").write_text(json.dumps(rows, indent=1))

    if not rows:
        return
    print(f"{'='*122}\n■ 判据 1：鼓包上必须移动 ≥3mm 才值得评估（缺口中位 2.6mm）")
    for arm in ("tight", "sparse"):
        for tag in VARIANTS:
            g = [r for r in rows if r["arm"] == arm and r["tag"] == tag]
            if not g:
                continue
            m = np.array([r["mag_peak"] for r in g])
            d = np.array([r["d_max"] for r in g])
            print(f"  {arm:>6} {tag:>6}  |Δ|@峰 中位 {np.median(m):6.2f}mm（最大 {m.max():6.2f}）"
                  f"   ΔMAX 中位 {np.median(d):+6.2f}mm   "
                  f"MAX 改善 {int((d < 0).sum())}/{len(d)}   "
                  f"{'✅ 动了' if np.median(m) >= 3 else '❌ <3mm'}")

    print(f"\n■ 判据 2（§23.5 边界②）：绝对口径 —— 对真值到底是变好还是变差")
    allr = [r for r in rows if r["tag"] in VARIANTS]
    for tag in VARIANTS:
        g = [r for r in allr if r["tag"] == tag]
        if len(g) < 3:
            continue
        d = np.array([r["d_max"] for r in g]); dr = np.array([r["d_rmse"] for r in g])
        print(f"  {tag:>6}  n={len(g)}  MAX 改善 {int((d < 0).sum())}/{len(g)}"
              f"（中位 {np.median(d):+.2f}mm）   RMSE 改善 {int((dr < 0).sum())}/{len(g)}"
              f"（中位 {np.median(dr):+.2f}mm）")


if __name__ == "__main__":
    main()