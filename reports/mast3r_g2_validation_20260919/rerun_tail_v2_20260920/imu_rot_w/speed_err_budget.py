#!/usr/bin/env python3
"""Q4 逐帧 ATE 按**瞬时 GT 速度**分层 —— 无混淆地检验「缺口是快速转向现象」这个前提。

§25.3 说剩余缺口是「快速转向瞬间约10帧横向偏离块」。但那是在少数几个峰帧上量的；
一旦换成固定弧长窗口，窗口时长会随速度变 12 倍（慢段横跨折返），指标就被污染。
这里换成**最干净的口径**：误差与速度都是**逐帧**量，不引入任何窗口。

分层：按该帧的瞬时 GT 速度放进速度档，档内取 |误差| 的中位/p90。
⇒ 若误差随速度单调上升 ⇒ 缺口确实是运动学现象；若平坦 ⇒ 前提要重判。

同时报「ATE 峰帧落在哪个速度档」，回答「最大误差是不是快段事件」。

用法: speed_err_budget.py
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent
EDGES = [0, 20, 50, 120, 240, 1e9]


def main():
    cells = [l.split("/") for l in Path("/tmp/cells22.txt").read_text().split()
             if l.count("/") == 2]
    out, acc = {}, {i: [] for i in range(len(EDGES) - 1)}
    peak_bucket = []
    for batch, group, arm in cells:
        gtdir = WF / batch / group
        tg, Pg, Qg = E.load_trajectory(gtdir / "lighthouse_body_ground_truth.csv")
        p = gtdir / "fusion" / arm / "trajectory_fused.csv"
        if not p.exists():
            continue
        t, P, Q = E.load_trajectory(p)
        ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
        t, P, G = t[ins][val], P[ins][val], plt_[:, 1:4]
        s, R, tt = E.similarity_align(P, G)
        # ⚠ 与评测器一致：Sim(3) 只用来定 gauge，误差用**刚体**口径报会不同，
        #   但本表只做**档间相对比较**，同一 gauge 内自洽即可。
        A = s * (P @ R.T) + tt
        err = np.linalg.norm(A - G, axis=1) * 1000
        dt = np.gradient(t)
        v = np.linalg.norm(np.gradient(G, axis=0), axis=1) / np.maximum(dt, 1e-9) * 1000
        cell = f"{batch}/{group}/{arm}"
        d = {}
        for k in range(len(EDGES) - 1):
            lo, hi = EDGES[k], EDGES[k + 1]
            m = (v >= lo) & (v < hi)
            lab = f"{lo:g}-{hi:g}" if hi < 1e8 else f"{lo:g}+"
            d[lab] = {"n": int(m.sum()), "frac": float(m.mean()),
                      "err_p50": float(np.median(err[m])) if m.any() else None,
                      "err_p90": float(np.percentile(err[m], 90)) if m.any() else None,
                      "err_max": float(err[m].max()) if m.any() else None}
            if m.any():
                acc[k].append((float(np.median(err[m])), float(np.percentile(err[m], 90)),
                               ert := float(err[m].max())))
        kpk = int(np.argmax(err))
        d["peak"] = {"err_mm": float(err[kpk]), "v_mm_s": float(v[kpk]),
                     "frame": kpk, "t": float(t[kpk])}
        peak_bucket.append(float(v[kpk]))
        out[cell] = d

    (HERE / "speed_err_budget.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))

    print("=== 全 22 臂合并：逐帧 ATE vs 瞬时 GT 速度 ===")
    print(f"{'速度档 mm/s':<12}{'帧占比':>8}{'err_p50':>10}{'err_p90':>10}{'err_max中位':>12}")
    for k in range(len(EDGES) - 1):
        lo, hi = EDGES[k], EDGES[k + 1]
        lab = f"{lo:g}-{hi:g}" if hi < 1e8 else f"{lo:g}+"
        a = acc[k]
        fr = np.median([out[c][lab]["frac"] for c in out])
        if a:
            a = np.array(a)
            print(f"{lab:<12}{fr:>8.1%}{np.median(a[:,0]):>10.2f}"
                  f"{np.median(a[:,1]):>10.2f}{np.median(a[:,2]):>12.2f}")

    pb = np.array(peak_bucket)
    print(f"\n=== ATE 峰帧落在哪个速度档（n={len(pb)}）===")
    for k in range(len(EDGES) - 1):
        lo, hi = EDGES[k], EDGES[k + 1]
        lab = f"{lo:g}-{hi:g}" if hi < 1e8 else f"{lo:g}+"
        m = (pb >= lo) & (pb < hi)
        print(f"  {lab:<12}{m.sum():>3} 臂  {m.mean():>6.1%}   "
              f"峰帧速度中位 {np.median(pb[m]) if m.any() else 0:8.1f} mm/s")
    print(f"  （GT 速度 p50 全臂中位 = "
          f"{np.median([np.median([out[c][l]['frac'] for l in out[c] if 'frac' in str(type(out[c][l]))]) for c in []] or [0]):.1f}）"
          if False else "")
    print(f"\n落盘 speed_err_budget.json")


if __name__ == "__main__":
    main()
