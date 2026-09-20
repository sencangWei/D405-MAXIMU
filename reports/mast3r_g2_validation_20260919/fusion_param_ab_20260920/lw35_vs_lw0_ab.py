#!/usr/bin/env python3
"""★ 判决实验 1：`lw=0.35`（09-14 tight 臂）vs `lw=0`（现役复原）—— **多组对照**

## 为什么必须重做

`graph_vs_fusion.py` 得出「09-14 的 fused = 图优化直通」是**错的**：
它只看了 `tight` 一条臂，且拿 09-14 的 fused 去比 **09-20** 的 graph。
用 09-14 的**原始输入**重跑后证实：tight 臂是 `lw=0.35 + adaptive + smooth15 + sw0.475`，
**真在注入修正**（复现盘上产物 0.236mm/1.171mm，而 lw=0 差 2.069mm/3.552mm）。

所以「`lw=0.35` 到底帮了还是害了」必须用**受控 A/B** 直接量，而不是从脚本推。

## 设计（关键：**同一个 graph，只动融合参数**）

* **graph 固定** = 该 cell 的 `fusion/<arm>/mast3r/trajectory_graph.csv`（09-14 原始产物）
  ⇒ 前端/图优化的差异被完全排除，只剩融合段。
* 四套配置：

  | 代号 | 说明 | `lw` | adaptive | `smooth` | `sw` |
  |---|---|---|---|---|---|
  | **A** | 09-14 tight 配方 | **0.35** | 是 | 15 | 0.475 |
  | **B** | 现役复原配方 | 0 | 否 | 8 | auto |
  | **C** | A 去掉尺度权重 | 0.35 | 是 | 15 | 0 |
  | **D** | B 加上尺度权重 | 0 | 否 | 15 | 0.475 |

  A vs C 隔离 `sw`；A vs D 隔离 `lw`；B 是实际生产配置。
* 两条臂（`tight`/`sparse`）都跑 ⇒ 组数翻倍。
* 评测用官方 `evaluate_slam_ground_truth.pose_errors`（SE3 无尺度对齐），
  门 = `ate_rotation_rmse_deg` < 2.0。

## ⚠ 一个必须先讲的机制

`fuse_docker2_mast3r_complementary.py:508`
```python
local_weight = (args.docker2_local_weight
                if position_policy["local_translation_allowed"] else 0.0)
```
**VINS 验收不过 ⇒ lw 被强制归零**，CLI 传什么都不算。所以只有验收 PASS 的 cell
（本语料 10 个）才能做这个 A/B；其余会被静默变成 lw=0，A/B 无意义。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
              "formal_runtime_calibration/vins_config.yaml")
SCRIPT = Path("/home/robot/ego_vio_humble/scripts/fuse_docker2_mast3r_complementary.py")

CONFIGS = {
    "A lw.35 s15 sw.475": dict(lw="0.35", ada=True, smooth="15", sw="0.475"),
    "B lw0 s8 auto":      dict(lw="0", ada=False, smooth="8", sw=None),
    "C lw.35 s15 sw0":    dict(lw="0.35", ada=True, smooth="15", sw="0"),
    "D lw0 s15 sw.475":   dict(lw="0", ada=False, smooth="15", sw="0.475"),
}


def acceptance_ok(cell):
    p = cell / "docker2_slam/run_acceptance.json"
    v = cell / "docker2_slam/vio_corrected_stream.csv"
    if not v.exists():
        return False, "无 VINS 轨迹"
    if not p.exists():
        return False, "无验收报告"
    d = json.loads(p.read_text())
    f = list(d.get("runtime_watchdog", {}).get("failures", []))
    if d.get("result") == "PASS" and not f:
        return True, "PASS"
    return False, f"验收 {d.get('result')}: {','.join(f) or '?'}（lw 会被强制归零）"


def run_one(cell, arm, cfg, out, rep):
    md = cell / f"fusion/{arm}/mast3r"
    cmd = [sys.executable, str(SCRIPT),
           "--mast3r", str(md / "trajectory_graph.csv"),
           "--docker2", str(cell / "docker2_slam/vio_corrected_stream.csv"),
           "--docker2-report", str(cell / "docker2_slam/run_acceptance.json"),
           "--body-t-camera-yaml", str(CONFIG),
           "--graph-report", str(md / "graph_fusion_report.json"),
           "--scale-horizon-s", "1",
           "--smoothing-s", cfg["smooth"],
           "--docker2-local-weight", cfg["lw"],
           "--roughness-threshold-mm", "9",
           "--use-docker2-orientation-for-lever-arm",
           "--output", str(out), "--report", str(rep)]
    if cfg["sw"] is None:
        cmd += ["--docker2-scale-weight", "0", "--auto-docker2-scale-weight"]
    else:
        cmd += ["--docker2-scale-weight", cfg["sw"]]
    if cfg["ada"]:
        cmd += ["--adaptive-local-weight", "--adaptive-weight-strength", "0.45"]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    cells = []
    for arm in ("tight", "sparse"):
        for p in sorted(ROOT.glob(f"**/fusion/{arm}/mast3r/trajectory_graph.csv")):
            cell = p.parents[3]
            ok, why = acceptance_ok(cell)
            cells.append((cell, arm, ok, why))
    usable = [c for c in cells if c[2]]
    print(f"候选 {len(cells)} 组；因 VINS 验收门不可用于 A/B 的 {len(cells)-len(usable)} 组"
          "（lw 会被静默归零）：")
    for c, arm, ok, why in cells:
        if not ok:
            print(f"   ✗ {str(c).replace(str(ROOT)+'/', ''):<46}{arm:<7}{why}")
    print(f"\n实测 {len(usable)} 组 × {len(CONFIGS)} 套配置\n")

    names = list(CONFIGS)
    print(f"{'cell':<44}{'arm':>7}" + "".join(f"{n:>19}" for n in names))
    print(f"{'':<51}" + "".join(f"{'ATE mm / 门 °':>19}" for _ in names))
    print("-" * (51 + 19 * len(names)))
    acc = {n: ([], []) for n in names}
    d_ate = {n: [] for n in names}          # 相对 B 的逐 cell 差
    meta = {n: {"sw": [], "inj": []} for n in names}
    pairs = {n: [0, 0] for n in names}      # 相对 D 的 (更好, 更差) 计数
    for cell, arm, _, _ in usable:
        gt = cell / "lighthouse_body_ground_truth.csv"
        tg, Pg, Qg = E.load_trajectory(gt)
        row, base = [], None
        for n in names:
            with tempfile.TemporaryDirectory() as td:
                out, rep = Path(td) / "f.csv", Path(td) / "r.json"
                try:
                    run_one(cell, arm, CONFIGS[n], out, rep)
                    t, P, Q = E.load_trajectory(out)
                    _, _, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
                    m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
                    a, r = (m["ate_translation_rmse_m"] * 1000,
                            m["ate_rotation_rmse_deg"])
                    rd = json.loads(rep.read_text())
                    sw_used = rd.get("scale", {}).get("docker2_scale_weight")
                    inj = rd.get("fusion", {}).get("injected_correction_max_mm")
                except Exception as e:                                # noqa: BLE001
                    a, r, sw_used, inj = (float("nan"),) * 2 + (None, None)
                    print(f"   ! {cell.name}/{arm} {n}: {e}")
            acc[n][0].append(a)
            acc[n][1].append(r)
            row.append((a, r))
            if sw_used is not None:
                meta[n]["sw"].append(sw_used)
            if inj is not None:
                meta[n]["inj"].append(inj)
            if n.startswith("B"):
                base = a
        for n, (a, _) in zip(names, row):
            if base is not None and not np.isnan(a):
                d_ate[n].append(a - base)
        dv = row[names.index("D lw0 s15 sw.475")][0]
        for n, (a, _) in zip(names, row):
            if not np.isnan(a) and not np.isnan(dv):
                pairs[n][0 if a < dv else 1] += 1
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name:<44}{arm:>7}"
              + "".join(f"{a:>10.2f}/{r:<8.2f}" for a, r in row))

    print("-" * (51 + 19 * len(names)))
    print(f"{'中位 ATE (mm)':<44}{'':>7}"
          + "".join(f"{np.nanmedian(acc[n][0]):>19.2f}" for n in names))
    print(f"{'中位 门 (°)':<44}{'':>7}"
          + "".join(f"{np.nanmedian(acc[n][1]):>19.2f}" for n in names))
    print(f"{'门过 (<2.0°)':<44}{'':>7}"
          + "".join(f"{int((np.array(acc[n][1])<2.0).sum()):>10}/{len(acc[n][1]):<8}"
                    for n in names))
    print(f"{'vs B 的逐 cell ATE 改善(中位)':<44}{'':>7}"
          + "".join(f"{np.median(d_ate[n]):>19.2f}" if d_ate[n] else f"{'n/a':>19}"
                    for n in names))
    print(f"{'实际用的 docker2_scale_weight(中位)':<41}{'':>7}"
          + "".join(f"{np.median(meta[n]['sw']):>19.3f}" if meta[n]["sw"] else f"{'n/a':>19}"
                    for n in names))
    print(f"{'实际注入修正 max(中位 mm)':<44}{'':>7}"
          + "".join(f"{np.median(meta[n]['inj']):>19.2f}" if meta[n]["inj"] else f"{'n/a':>19}"
                    for n in names))
    print(f"{'vs D 的逐 cell 胜负 (更好/更差)':<44}{'':>7}"
          + "".join(f"{pairs[n][0]:>10}/{pairs[n][1]:<8}" for n in names))
    print("\n判读：若 A 的中位 ATE 明显低于 B ⇒ 09-14 的 lw=0.35 是**真收益**，"
          "复原应包含它；\n      若 A 高于 B ⇒ 现役的 lw=0 是对的，只是当年不是这么定的。")


if __name__ == "__main__":
    main()