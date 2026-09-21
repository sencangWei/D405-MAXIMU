#!/usr/bin/env python3
"""§31 剩余缺口（快速转向约10帧横向偏离块）的**航向通道**普查 —— 零新跑，22 臂 × 4 阶段。

§25.3 已把这条缺口的形状查清：**步长对、方向错**
（估计步长 p99 8.61 / max 9.50，真值 p99 8.79 / max 10.12，两边都无跳变；
 污染在航向：估计与真值步长夹角在 k=461/467/470 跳 56.7°/34.3°/23.7°，其余帧 1–16°；
 误差 87% 堆在垂直航向的 Y 轴，约 10 帧横向偏离块）。

本脚本把「方向错」做成**可逐阶段追踪的标量**，回答：
  Q1 航向跳变在哪一级出现？`[1/8]` 前端就有，还是 `[7/8]` 图优化引入？
  Q2 它是否**共模**（同 take 两臂在同一时刻跳）？共模 ⇒ 结构性，权重类杠杆必无效。
  Q3 它是否**只在快速转向时**出现（随速度单调）？

⚠⚠ 两处口径陷阱（第一版都踩了，见 [[mast3r-rerun-tail-v2-20260920]] §29.2 的同源教训）：
  1. **逐帧有限差分的方向在静止段是纯噪音**：实测 GT 速度 p10 = 0.14mm/s、约 40% 帧
     几乎静止 ⇒ 第一版报出 θp95 = 130°+（速度向量近反向），是假的。
     ⇒ 本版一律用**沿 GT 弧长累积 L 的窗口步进**（默认 25mm），固定位移下的方向
     才是有物理意义的「航向」。
  2. **各阶段行数不同**（前端 ~1799 / 融合 1743）⇒ 帧索引不可配对，
     「同一时刻」一律用**时间戳**判定。
  ⚠ 前端是 MASt3R 单位（≈2.25× 真值）⇒ 必须 Sim(3) 对齐；跨口径数字不要引用。

用法: heading_census.py [--L 25] [--vmin 20] [batch group arm]
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent
STAGES = [("1_frontend", "mast3r/trajectory_frames.csv"),
          ("6_imu_metric", "mast3r/trajectory_imu_metric.csv"),
          ("7_graph", "mast3r/trajectory_graph.csv"),
          ("8_fused", "trajectory_fused.csv")]
VBUCKETS = [(50, 120), (120, 240), (240, 1e9)]
TMAX = 0.5


def load_aligned(p, tg, Pg, Qg):
    t, P, Q = E.load_trajectory(p)
    ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    t, P, G = t[ins][val], P[ins][val], plt_[:, 1:4]
    if len(t) < 50:
        return None
    s, R, tt = E.similarity_align(P, G)
    return t, s * (P @ R.T) + tt, G


def window_steps(t, G, L, Tmax):
    """沿 GT 弧长累积到 L 的窗口步进 ⇒ 每个锚点一根方向明确的「真值航向」。

    返回 (锚点索引 i, 终点索引 j)。固定位移 L 把「方向」的信噪比钉住：
    定位噪声 2mm / L 25mm ⇒ 角度分辨 ~6.5°，足以看见 20°+ 的跳变。

    ⚠⚠ **必须同时限时**（第二版踩的坑）：只限弧长时，慢段要 12.5s 才走完 25mm，
    窗口横跨一个"折返"行程 ⇒ 真值航向本身可差 ~115°（实测 v11b3/g1 前端 θp90=115.1
    而该窗口速度只有 2mm/s）。那不是航向误差，是长窗口的折返假象。
    ⇒ 只保留**在 Tmax 秒内走完 L** 的锚点，等价于速度下限 L/Tmax。
    """
    seg = np.linalg.norm(np.diff(G, axis=0), axis=1)   # ⚠ G 是**米**
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    j = np.searchsorted(arc, arc + L * 1e-3, side="left")  # L 是 mm ⇒ 换成米
    ok = j < len(arc)
    i = np.arange(len(arc))[ok]
    j = j[ok]
    dur = t[j] - t[i]
    keep = dur <= Tmax
    return i[keep], j[keep]


def heading_metrics(t, A, G, L, Tmax):
    i, j = window_steps(t, G, L, Tmax)
    ev, gv = A[j] - A[i], G[j] - G[i]
    el = np.linalg.norm(ev, axis=1)
    gl = np.linalg.norm(gv, axis=1)
    ok = gl > 1e-9
    i, j, ev, gv, el, gl = i[ok], j[ok], ev[ok], gv[ok], el[ok], gl[ok]
    cos = np.clip((ev * gv).sum(1) / np.maximum(el * gl, 1e-18), -1, 1)
    theta = np.degrees(np.arccos(cos))          # 航向夹角 (deg)
    ratio = el / gl                              # 窗口步长比（应≈1）
    # 窗口内 GT 平均速度（mm/s）——用来做「快/慢」分层
    dt = t[j] - t[i]
    v = gl / np.maximum(dt, 1e-9) * 1000
    # 横向误差：位置误差在窗口起点的垂直航向方向上的投影
    gu = gv / gl[:, None]
    err = (A - G)[i]
    lat = np.abs(err - (err * gu).sum(1)[:, None] * gu).sum(1) * 1000
    return dict(theta=theta, ratio=ratio, v=v, lat=lat,
                t_anchor=t[i], t_end=t[j], n=len(theta))


def main():
    a = sys.argv[1:]
    L, vmin, Tmax = 25.0, 20.0, TMAX
    if "--L" in a:
        L = float(a[a.index("--L") + 1])
    if "--vmin" in a:
        vmin = float(a[a.index("--vmin") + 1])
    if "--Tmax" in a:
        Tmax = float(a[a.index("--Tmax") + 1])
    a = [x for x in a if not x.startswith("--") and x.lstrip("-").replace(".", "").isdigit() is False]
    cells = [l.split("/") for l in Path("/tmp/cells22.txt").read_text().split()
             if l.count("/") == 2]
    if len(a) >= 3:
        cells = [a[:3]]

    out = {}
    for batch, group, arm in cells:
        gtdir = WF / batch / group
        tg, Pg, Qg = E.load_trajectory(gtdir / "lighthouse_body_ground_truth.csv")
        base = gtdir / "fusion" / arm
        cell = f"{batch}/{group}/{arm}"
        out[cell] = {}
        for tag, rel in STAGES:
            p = base / rel
            if not p.exists():
                continue
            r = load_aligned(p, tg, Pg, Qg)
            if r is None:
                continue
            t, A, G = r
            m = heading_metrics(t, A, G, L, Tmax)
            if m["n"] < 20:
                continue
            d = {"n": m["n"], "L_mm": L, "Tmax_s": Tmax,
                 "theta_p50": float(np.median(m["theta"])),
                 "theta_p90": float(np.percentile(m["theta"], 90)),
                 "theta_max": float(m["theta"].max()),
                 "ratio_p50": float(np.median(m["ratio"])),
                 "lat_p50_mm": float(np.median(m["lat"])),
                 "lat_p90_mm": float(np.percentile(m["lat"], 90))}
            # 按窗口平均 GT 速度分层
            d["by_v"] = {}
            for lo, hi in VBUCKETS:
                k = (m["v"] >= lo) & (m["v"] < hi)
                d["by_v"][f"{lo:g}-{hi:g}"] = {
                    "n": int(k.sum()),
                    "theta_p50": float(np.median(m["theta"][k])) if k.any() else None,
                    "lat_p50": float(np.median(m["lat"][k])) if k.any() else None,
                    "ratio_p50": float(np.median(m["ratio"][k])) if k.any() else None,
                }
            # 跳变帧（θ>vmin 的那些窗口的锚点时刻）——共模判定用
            sel = m["theta"] > vmin
            d["n_jump"] = int(sel.sum())
            d["jump_frac"] = float(sel.mean())
            d["jump_times"] = [round(float(x), 1) for x in m["t_anchor"][sel]]
            d["jump_v_p50"] = float(np.median(m["v"][sel])) if sel.any() else None
            d["v_p50"] = float(np.median(m["v"]))
            out[cell][tag] = d

    p = HERE / "heading_census.json"
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False))

    print(f"L={L:g}mm 窗口步进（且须 ≤{Tmax:g}s 内走完 ⇒ 速度≥{L/Tmax:g}mm/s）  跳变判据 θ>{vmin:g}°\n")
    hdr = (f"{'cell':<46}{'stage':<13}{'n':>5}{'θp50':>7}{'θp90':>7}{'θmax':>7}"
           f"{'步长比':>7}{'横向p50':>8}{'跳%':>6}{'v@跳':>7}")
    print(hdr)
    for cell, st in out.items():
        for tag, d in st.items():
            print(f"{cell[-44:]:<46}{tag:<13}{d['n']:>5}{d['theta_p50']:>7.1f}"
                  f"{d['theta_p90']:>7.1f}{d['theta_max']:>7.1f}{d['ratio_p50']:>7.3f}"
                  f"{d['lat_p50_mm']:>8.2f}{d['jump_frac']*100:>6.1f}"
                  f"{d['jump_v_p50'] or 0:>7.0f}")

    # ---- Q3: 航向误差随速度分层（融合级，全 22 臂合并）----
    print("\n=== Q3 航向误差 vs 窗口平均速度（融合级，全臂合并）===")
    print(f"{'速度档 mm/s':<14}{'n':>8}{'θp50':>8}{'横向p50 mm':>12}{'步长比':>9}")
    for lo, hi in VBUCKETS:
        acc_t, acc_l, acc_r = [], [], []
        for st in out.values():
            d = st.get("8_fused", {}).get("by_v", {}).get(f"{lo:g}-{hi:g}")
            if d and d["n"]:
                acc_t.append(d["theta_p50"]); acc_l.append(d["lat_p50"])
                acc_r.append(d["ratio_p50"])
        if acc_t:
            lab = f"{lo:g}-{hi:g}" if hi < 1e8 else f"{lo:g}+"
            print(f"{lab:<14}{len(acc_t):>8}"
                  f"{np.median(acc_t):>8.1f}{np.median(acc_l):>12.2f}"
                  f"{np.median(acc_r):>9.3f}")

    # ---- Q2: 共模判定 ----
    print("\n=== Q2 共模：同 take 两臂在融合级跳变时刻的 Jaccard ===")
    bytake = {}
    for cell, st in out.items():
        if "8_fused" not in st:
            continue
        bytake.setdefault(cell.rsplit("/", 1)[0], {})[cell.rsplit("/", 1)[1]] = \
            set(st["8_fused"]["jump_times"])
    for k, arms in sorted(bytake.items()):
        if len(arms) < 2:
            print(f"  {k[-46:]:<48} (只有一臂)")
            continue
        a_, b_ = list(arms.values())[:2]
        j = len(a_ & b_) / max(len(a_ | b_), 1)
        print(f"  {k[-46:]:<48} Jaccard {j:5.1%}   n={len(a_)}/{len(b_)}")

    # ---- Q1: 前端 vs 融合，跳变是否同一时刻 ----
    print("\n=== Q1 跳变时刻是否从 `[1/8]` 就存在（前端的跳变时刻能否覆盖融合的）===")
    print(f"{'cell':<48}{'#front':>8}{'#fused':>8}{'融合⊂前端':>11}")
    for cell, st in out.items():
        f, u = st.get("1_frontend"), st.get("8_fused")
        if not f or not u or not u["jump_times"]:
            continue
        fs, us = set(f["jump_times"]), set(u["jump_times"])
        cov = len(fs & us) / len(us)
        print(f"{cell[-46:]:<48}{len(fs):>8}{len(us):>8}{cov:>11.1%}")
    print(f"\n落盘 {p.name}")


if __name__ == "__main__":
    main()