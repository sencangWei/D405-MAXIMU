#!/usr/bin/env python3
"""前端 `[1/8]` 的差异，到底有多少能传到融合后 `[8/9]`？—— 把 §18.6 从 3 格扩到全格。

## 为什么问这个

§18.6（README）量到：前端差 3–18mm 只映射成融合后 0.9–2mm（≈3.6× / ≈12× 压缩），
并据此给出「§12 关键帧密度 / §17 weight / §18 noimu / §20.4 C-as-weight 这些
**前端杠杆在门上全部像死掉**」的最简解释。但那一节只有 2–3 个 cell，
原作者自己标了「**只能算嫌疑，不算判决**」。

这个判决很贵：如果前端差真的传不过去，那么**整族前端杠杆按下限就要前端动 ~3mm
× 压缩比 ≈ 数十 mm** 才够填融合后 2.6mm 的缺口 —— 等于把 §20.4 也一起否掉。
所以它值一次穷举。

## 为什么便宜

`fusion/<arm>/mast3r/` 下四个阶段的轨迹**都已经在盘上**，且 43/44 个臂齐全：

    [1/8]  mast3r/trajectory_frames.csv     （已转换，MASt3R 单位）
    [6/8]  mast3r/trajectory_imu_metric.csv
    [7/8]  mast3r/trajectory_graph.csv
    [8/9]  trajectory_fused.csv

⇒ **零新跑**，全格一次性算完。

## 判据（预先定死）

1. **配对 Δ-Δ**（cells 内有 tight 与 sparse 两臂的）：`Δfrontend` vs `Δfused`。
   看符号一致率与斜率 —— §18.6 说「序反转」⇒ 期望符号一致率 ≈ 掷硬币。
2. **跨格相关**：`corr(MAX_frontend, MAX_fused)`。若前端质量不预测融合质量 ⇒ 近 0。
3. **压缩比**：中位 `|Δfused| / |Δfrontend|`。

⚠ 口径：这是**四条不同的链**，不混。`[1/8]` 是前端（需 Sim(3)），其余已是米制。

用法: frontend_transfer.py [--family fusion|fusion_current]
"""
import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent
CELLS = HERE.parent / "fusion89_sweep" / "cells.txt"

STAGES = ["frames", "imu_metric", "graph", "fused"]


def stage_path(arm_dir: Path, stage: str) -> Path:
    if stage == "fused":
        return arm_dir / "trajectory_fused.csv"
    return arm_dir / "mast3r" / f"trajectory_{stage}.csv"


def max_err(f: Path, tg, Pg, Qg):
    """Sim(3) 对齐到真值后的 MAX(mm)。前端是 MASt3R 单位，必须 similarity。"""
    t, P, Q = E.load_trajectory(f)
    ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    if ins.sum() < 10:
        return None
    P, t = P[ins][val], t[ins][val]
    gt = plt_[:, 1:4]
    s, R, tt = E.similarity_align(P, gt)
    ev = (s * (P @ R.T) + tt - gt) * 1000.0
    err = np.linalg.norm(ev, axis=1)
    return dict(rmse=float(np.sqrt((err ** 2).mean())), mx=float(err.max()),
                n=int(len(P)), scale=float(s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="fusion",
                    help="fusion = 09-14 产线；fusion_current = 09-20 现役重跑")
    a = ap.parse_args()

    # cells.txt 每格两行（tight/sparse 各一行）⇒ 必须按 (batch, group) 去重，
    # 否则每格算两遍、Δ-Δ 判决的 n 翻倍（第一版就踩了）。
    cells = sorted({(p[0], p[1]) for p in
                    (ln.split() for ln in CELLS.read_text().splitlines() if ln.strip())})
    rows = []
    for b, g in cells:
        d = WF / b / g
        gt_f = d / "lighthouse_body_ground_truth.csv"
        if not gt_f.exists():
            print(f"⚠ 无真值 {b}/{g}")
            continue
        tg, Pg, Qg = E.load_trajectory(gt_f)
        for arm in ("tight", "sparse"):
            base = d / a.family / arm if a.family != "fusion" else d / "fusion" / arm
            if not base.exists():
                continue
            r = dict(batch=b, group=g, arm=arm)
            ok = False
            for st in STAGES:
                f = stage_path(base, st)
                if not f.exists():
                    r[st] = None
                    continue
                v = max_err(f, tg, Pg, Qg)
                r[st] = None if v is None else v["mx"]
                r[st + "_rmse"] = None if v is None else v["rmse"]
                ok = ok or (v is not None)
            if ok:
                rows.append(r)

    # ---- 1) 全格表 ----
    print(f"族 = {a.family}    格 × 臂 = {len(rows)}\n")
    print(f"{'cell':<44}{'arm':>7}{'[1/8]f':>9}{'[6/8]m':>9}{'[7/8]g':>9}{'[8/9]u':>9}{'u/f':>7}")
    print("-" * 94)
    for r in rows:
        v = [r[s] for s in STAGES]
        ratio = (v[3] / v[0]) if (v[0] and v[3]) else None
        print(f"{r['batch'][-26:] + '/' + r['group']:<44}{r['arm']:>7}"
              + "".join(f"{x:>9.2f}" if x else f"{'—':>9}" for x in v)
              + (f"{ratio:>7.2f}" if ratio else f"{'—':>7}"))

    # ---- 2) 配对 Δ-Δ（tight vs sparse 同格）----
    print(f"\n{'='*94}\n■ 判决 1：配对 Δ-Δ —— 前端差 Δf，融合后差 Δu（同格 tight − sparse）")
    pairs = []
    for b, g in cells:
        rt = next((r for r in rows if r["batch"] == b and r["group"] == g and r["arm"] == "tight"), None)
        rs = next((r for r in rows if r["batch"] == b and r["group"] == g and r["arm"] == "sparse"), None)
        if not rt or not rs:
            continue
        if None in (rt["frames"], rs["frames"], rt["fused"], rs["fused"]):
            continue
        df = rt["frames"] - rs["frames"]
        du = rt["fused"] - rs["fused"]
        pairs.append((b, g, df, du))
        print(f"  {b[-24:] + '/' + g:<32}Δf {df:+8.2f}mm   Δu {du:+8.2f}mm"
              f"   压缩 {abs(du)/abs(df) if df else float('nan'):7.2f}×"
              f"   {'同号' if df*du > 0 else '反号'}")
    if pairs:
        df = np.array([p[2] for p in pairs])
        du = np.array([p[3] for p in pairs])
        same = int((df * du > 0).sum())
        comp = np.abs(du) / np.maximum(np.abs(df), 1e-9)
        print(f"\n  n={len(pairs)}  同号 {same}/{len(pairs)}")
        print(f"  corr(Δf, Δu) = {np.corrcoef(df, du)[0,1]:+.3f}")
        print(f"  压缩比 |Δu|/|Δf|  中位 {np.median(comp):.2f}×   "
              f"范围 {comp.min():.2f}–{comp.max():.2f}×")
        print(f"  |Δf| 中位 {np.median(np.abs(df)):.2f}mm  →  |Δu| 中位 {np.median(np.abs(du)):.2f}mm")

    # ---- 2b) ★ 逐段分解：前端差在哪一段被吸收掉 ----
    # ⚠ `fusion_current` 只重跑了 [7/8] 之后（前面是共享的）⇒ 起点用两端都有的最早阶段。
    active = [s for s in STAGES if any(r[s] for r in rows)]
    print(f"\n{'='*94}\n■ 判决 1b：逐段分解 —— |Δ| 在每一段还剩多少（本族可用阶段 = {active}）")
    hdr = "".join(f"{'@' + s:>10}" for s in active)
    print(f"  {'cell':<34}{hdr}" + "".join(f"{'→' + active[i+1]:>8}" for i in range(len(active) - 1)))
    step_ratio = {f"{active[i]}→{active[i+1]}": [] for i in range(len(active) - 1)}
    for b, g in cells:
        rt = next((r for r in rows if r["batch"] == b and r["group"] == g
                   and r["arm"] == "tight"), None)
        rs = next((r for r in rows if r["batch"] == b and r["group"] == g
                   and r["arm"] == "sparse"), None)
        if not rt or not rs:
            continue
        if any(rt[s] is None or rs[s] is None for s in active):
            continue
        d = [abs(rt[s] - rs[s]) for s in active]
        rr = []
        for i in range(len(active) - 1):
            if d[i] > 1e-9:
                k = f"{active[i]}→{active[i+1]}"
                step_ratio[k].append(d[i + 1] / d[i])
                rr.append(f"{d[i+1]/d[i]:8.2f}")
            else:
                rr.append(f"{'—':>8}")
        print(f"  {b[-22:] + '/' + g:<34}" + "".join(f"{x:>10.2f}" for x in d) + "".join(rr))
    for k, v in step_ratio.items():
        if v:
            print(f"  ★ {k} 传递率 中位 {np.median(v):.2f}×   "
                  f"范围 {min(v):.2f}–{max(v):.2f}×  (n={len(v)})")

    # ---- 3) 跨格相关：前端质量预不预测融合质量 ----
    print(f"\n{'='*94}\n■ 判决 2：跨格相关 —— 前端 MAX 预不预测融合 MAX")
    fr = np.array([r["frames"] for r in rows if r["frames"] and r["fused"]])
    fu = np.array([r["fused"] for r in rows if r["frames"] and r["fused"]])
    if len(fr) > 3:
        print(f"  n={len(fr)}  corr(前端MAX, 融合MAX) = {np.corrcoef(fr, fu)[0,1]:+.3f}")
        # 逐阶段相关：看每一步还剩多少
        for i, s in enumerate(STAGES[:-1]):
            nxt = STAGES[i + 1]
            x = np.array([r[s] for r in rows if r[s] and r[nxt]])
            y = np.array([r[nxt] for r in rows if r[s] and r[nxt]])
            if len(x) > 3:
                print(f"    corr([{i+1}/8] MAX, [{i+2}/8] MAX) = {np.corrcoef(x, y)[0,1]:+.3f}  (n={len(x)})")

    out = HERE / f"frontend_transfer_{a.family}.json"
    import json
    out.write_text(json.dumps(rows, indent=1))
    print(f"\n落盘 {out.name}")


if __name__ == "__main__":
    main()