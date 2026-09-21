#!/usr/bin/env python3
"""§22 候选机理：`[6/8]→[7/8]` 的 0.33× 衰减，是不是「约束密度」决定的？

## 假设

位姿图优化吃两样东西：**初值**（`[6/8]` 的位姿，继承前端）与**约束边**
（双目尺度边 + IMU 预积分边 + 相对运动边）。输出被谁主导，就决定了传递率：

* 约束主导 ⇒ 输出由边决定，初值差被重解掉 ⇒ **传递率 → 0**（§21.3 的「杀掉」簇）
* 初值主导（边少/弱）⇒ 输出 ≈ 初值 ⇒ **传递率 → 1**（§21.3 的「直通」簇）

⇒ 可证伪的预测：**传递率应与「每节点边数」负相关**。

## 为什么值得测

§21 定位了衰减器在 `[7/8]`，但没说**为什么**。没有机理就去扫 `[7/8]` 参数，
就是重犯 §19 的错（在没有机理的地方穷举 1008 跑）。
若这个假设成立，`[7/8]` 的靶子就明确成「哪些边把它拖走了」——
而 §21.2 已指出：双目尺度边**继承前端点图**（`align_mast3r_scale_with_stereo.py`
读的就是 MASt3R 的 pointmap），所以它拖的方向**可能正是鼓包的方向**。

数据全在盘上（`tail/graph_fusion_report.json`），零新跑。

用法: graph_attenuation.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

# 从 frontend_transfer 复用已算好的逐段 |Δ|
FT = HERE / "frontend_transfer_fusion.json"
WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")

FIELDS = {
    "stereo_rot_edges": ("rotation_fusion", "stereo_rotation_edges"),
    "align_edges": ("trajectory_frame_alignment", "edges"),
    "kf_nodes": ("keyframe_graph", "keyframe_nodes"),
    "corr_nodes": ("keyframe_graph", "correction_nodes"),
    "gap_frames": ("keyframe_graph", "maximum_gap_frames"),
    "overlap_ratio": ("relative_motion_alignment", "overlap_ratio"),
    "downweighted": ("rotation_fusion", "visual_downweighted_nodes"),
    "opt_cost": ("rotation_fusion", "optimizer_cost"),
    "scale_disagree": ("metric_scale_consistency", "relative_difference"),
}


def dig(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def main():
    rows = json.loads(FT.read_text())
    by = {(r["batch"], r["group"], r["arm"]): r for r in rows}

    print("§22 约束密度 vs [6/8]→[7/8] 传递率\n")
    recs = []
    for b, g, arm in sorted(by):
        if arm != "tight":
            continue
        rt, rs = by.get((b, g, "tight")), by.get((b, g, "sparse"))
        if not rt or not rs:
            continue
        if any(rt[s] is None or rs[s] is None for s in ("imu_metric", "graph")):
            continue
        d6 = abs(rt["imu_metric"] - rs["imu_metric"])
        d7 = abs(rt["graph"] - rs["graph"])
        if d6 < 1e-9:
            continue
        transfer = d7 / d6

        # ⚠ 报告在**工作流格**里（`fusion/<arm>/mast3r/graph_fusion_report.json`），
        #   不是在本地 `imu_rot_w/out/`（那里只有 run_tail.sh 跑过的 4 个臂）。
        rep = (WF / b / g / "fusion" / "tight" / "mast3r"
               / "graph_fusion_report.json")
        if not rep.exists():
            continue
        d = json.loads(rep.read_text())
        rec = dict(cell=f"{b[-18:]}/{g}", transfer=transfer, d6=d6, d7=d7)
        for name, path in FIELDS.items():
            rec[name] = dig(d, path)
        recs.append(rec)

    if len(recs) < 4:
        print(f"⚠ 样本只有 {len(recs)}，不下结论")
        return

    hdr = f"{'cell':<26}{'传递率':>8}{'|Δ|@6/8':>9}" + "".join(f"{k:>12}" for k in FIELDS)
    print(hdr)
    print("-" * len(hdr))
    for r in recs:
        print(f"{r['cell']:<26}{r['transfer']:>8.2f}{r['d6']:>9.2f}"
              + "".join(f"{r[k]:>12.3f}" if isinstance(r[k], (int, float))
                        else f"{'—':>12}" for k in FIELDS))

    tr = np.array([r["transfer"] for r in recs])
    print(f"\n■ 与传递率的相关（n={len(tr)}）—— 假设预测：边数/节点 → **负**相关")
    for name in FIELDS:
        v = np.array([r[name] if isinstance(r[name], (int, float)) else np.nan
                      for r in recs], dtype=float)
        if np.isnan(v).all() or np.nanstd(v) < 1e-12:
            print(f"  {name:<18} (无变化)")
            continue
        m = ~np.isnan(v)
        c = np.corrcoef(v[m], tr[m])[0, 1] if m.sum() > 3 and np.std(v[m]) > 1e-12 else float("nan")
        print(f"  {name:<18} r = {c:+.3f}   (n={m.sum()})")

    # 每节点边数
    print("\n■ 派生量：每节点边数")
    for r in recs:
        n = r["corr_nodes"] or r["kf_nodes"]
        e = r["stereo_rot_edges"]
        r["edges_per_node"] = (e / n) if (e and n) else None
        print(f"  {r['cell']:<26} 传递率 {r['transfer']:5.2f}   "
              f"边 {e} / 节点 {n} = {r['edges_per_node']:.2f}")
    v = np.array([r["edges_per_node"] for r in recs if r["edges_per_node"]], dtype=float)
    t2 = np.array([r["transfer"] for r in recs if r["edges_per_node"]], dtype=float)
    if len(v) > 3 and np.std(v) > 1e-12:
        print(f"  ★ corr(每节点边数, 传递率) = {np.corrcoef(v, t2)[0,1]:+.3f}  (n={len(v)})")

    (HERE / "graph_attenuation_rows.json").write_text(json.dumps(recs, indent=1))
    print(f"\n落盘 graph_attenuation_rows.json")


if __name__ == "__main__":
    main()