#!/usr/bin/env python3
"""`correction_cap_mode`（global→per-node）到底改变了什么？

## 结论

**什么都不改变，除非发生裁切** —— 而裁切只在 `req_max > correction_limit` 时发生。

逐个 cell 比 09-14（`fusion/`）与 09-20（`fusion_current/`）的 [7/8] 产物：
`trajectory_graph.csv` 的 sha256，以及报告里的 `correction_clipped_frames`。
两者**完美相关**：`clipped == 0` 的 cell 产物逐字节相同。

两种模式的裁切语义差别很大（这解释了为什么两个越界 cell 会不一样）：
* `global`     —— 把**整个修正场等比缩小**直到最大值等于上限 ⇒ 1799 帧全中；
* `per-node`   —— 只裁越界的节点 ⇒ 只有几十帧。

## 顺带得到的代际判决

09-14 的图（global）经 lw=0 融合 = 5.72mm；09-20 的图（per-node）经 lw=0 = 5.46mm
⇒ **per-node 那一代是改进**（见 README §六）。
"""
import hashlib
import json
from pathlib import Path

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main():
    print("correction_cap_mode 的产物影响（09-14 fusion/ vs 09-20 fusion_current/）\n")
    print(f"{'cell':<46}{'cap_mode':>18}{'clipped':>12}{'req_max':>10}{'产物':>12}")
    print("-" * 98)
    same = diff = 0
    for c in sorted({f.parents[3] for f in ROOT.glob("**/fusion/tight/mast3r/trajectory_graph.csv")}):
        o = c / "fusion/tight/mast3r"
        n = c / "fusion_current/tight/mast3r"
        if not (o / "trajectory_graph.csv").exists() or not (n / "trajectory_graph.csv").exists():
            continue
        do = json.loads((o / "graph_fusion_report.json").read_text())
        dn = json.loads((n / "graph_fusion_report.json").read_text())
        so, sn = do["stereo_translation_fusion"], dn["stereo_translation_fusion"]
        identical = sha(o / "trajectory_graph.csv") == sha(n / "trajectory_graph.csv")
        same += identical
        diff += not identical
        name = str(c).replace(str(ROOT) + "/", "")
        print(f"{name:<46}"
              f"{so['correction_cap_mode']+'→'+sn['correction_cap_mode']:>18}"
              f"{str(so['correction_clipped_frames'])+' / '+str(sn['correction_clipped_frames']):>12}"
              f"{so['position_correction_requested_max_m']*1000:>9.2f}m"
              f"{'逐字节相同' if identical else '★ 不同':>12}")
    print(f"\n  逐字节相同 {same}/{same+diff}   不同 {diff}/{same+diff}")
    print("\n判据：产物不同 ⟺ clipped > 0 ⟺ req_max > correction_limit(25mm)。")
    print("      两者若完美一致 ⇒ cap mode 在未裁切的 cell 上是**空转**。")


if __name__ == "__main__":
    main()