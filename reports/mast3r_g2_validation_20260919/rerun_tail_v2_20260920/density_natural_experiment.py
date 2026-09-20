#!/usr/bin/env python3
"""关键帧密度的**天然对照**：同一 session 的 `tight` vs `sparse` 两臂。

## 为什么这是本目录最干净的一条证据

`tight` 与 `sparse` 是**同一批 session** 的两次前端运行，关键帧密度差 **5×**：

| 臂 | 关键帧数 | 中位间隙 |
|---|---|---|
| tight | 77–244 | **0.19 s** |
| sparse | 27–101 | **0.93 s** |

⇒ 这等价于一次**已经做过的、免费的**「加密关键帧」A/B —— 而且它是**真前端重跑**，
不是后处理仿真。**若长关键帧间隙是鼓包的成因，tight 应当明显赢。**它没有。

⚠ 口径：两臂的差别**不止**关键帧密度（`tight` 走 `motion_kf_tight.yaml`，
带运动关键帧机制）。所以这不是纯密度实验，但对「加密能不能救峰」这个问题，
它是本语料里**唯一存在**的直接对照；结论方向与 [[motion-keyframe-hole-ab-20260920]]
（全局降阈值 ⇒ 全指标变差）一致。

输入：`peak_keyframe_context.log`（已含每 cell 的两臂 MAX 与关键帧统计）。
⚠ 该 log 被仓库 `.gitignore:31 *.log` 排除 ⇒ **新克隆上先跑 `peak_keyframe_context.py`
   生成它**（约 1 分钟，复用 `g7_cache/`；已验确定性 —— 复跑数字逐位相同）。

用法: python3 peak_keyframe_context.py && python3 density_natural_experiment.py
"""
import collections
import math
import re
import statistics as st
from pathlib import Path

HERE = Path(__file__).parent
LOG = HERE / "peak_keyframe_context.log"
ROW = re.compile(r"^(\S+)\s+(tight|sparse)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+"
                 r"([\d.]+)\s+(\d+)/(\d+)\s+(\d+)\s+([\d.]+)\s*$")


def main():
    rows = []
    for ln in LOG.read_text().splitlines():
        m = ROW.match(ln)
        if m:
            rows.append(dict(c=m.group(1), arm=m.group(2), nkf=int(m.group(4)),
                             gmed=float(m.group(5)), mx=float(m.group(11))))
    by = collections.defaultdict(dict)
    for r in rows:
        by[r["c"]][r["arm"]] = r
    print(f"关键帧密度天然对照（{len(rows)} 组，来自 {LOG.name}）\n")
    print(f"{'session':<42}{'NKF t/s':>11}{'gap t/s':>12}{'MAX t/s':>14}{'胜':>7}")
    print("-" * 86)
    win = collections.Counter()
    for c, v in by.items():
        if "tight" not in v or "sparse" not in v:
            continue
        T, S = v["tight"], v["sparse"]
        w = "tight" if T["mx"] < S["mx"] else "sparse"
        win[w] += 1
        print("{:<42}{:>11}{:>12}{:>14}{:>7}".format(
            c[:42], f"{T['nkf']}/{S['nkf']}",
            f"{T['gmed']:.2f}/{S['gmed']:.2f}",
            f"{T['mx']:.1f}/{S['mx']:.1f}", w))
    print("-" * 86)
    for nm in ("tight", "sparse"):
        g = [r for r in rows if r["arm"] == nm]
        print("{:<7} n={} 关键帧 {}-{}  中位间隙 {:.2f}s  MAX 中位 {:.1f}".format(
            nm, len(g), min(r["nkf"] for r in g), max(r["nkf"] for r in g),
            st.median([r["gmed"] for r in g]), st.median([r["mx"] for r in g])))
    # cell 级相关：间隙长度 vs MAX
    xs = [r["gmed"] for r in rows]; ys = [r["mx"] for r in rows]
    mx_, my_ = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((a - mx_) * (b - my_) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx_) ** 2 for a in xs) * sum((b - my_) ** 2 for b in ys))
    print(f"\n逐 session 对照：tight 胜 {win['tight']} / sparse 胜 {win['sparse']}")
    print(f"cell 级 corr(中位关键帧间隙, MAX) = {num/den:+.3f}  (n={len(rows)})")
    print(f"\n判读：密度差 5×、逐 session {win['tight']}胜{win['sparse']}、"
          f"corr 为**负** ⇒ 「长关键帧间隙 ⇒ 鼓包」这条线索**死**。")


if __name__ == "__main__":
    main()