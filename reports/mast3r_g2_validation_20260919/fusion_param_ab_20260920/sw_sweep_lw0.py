#!/usr/bin/env python3
"""判决实验 1b：在 `lw=0` 下扫 `--docker2-scale-weight`（多组对照）

上一轮我把 `sw` 判成「效应量 0.56mm，逐 cell 胜负 4:4，不足以当结论」并据此
用了 `--auto-docker2-scale-weight`（在本语料上恒选 0）。但 17 组对照显示
`sw=0.475` 比 `sw=0` 好（4.99 vs 5.26）⇒ 那个判断是**样本太少**下的误判。

本脚本在**同一个 graph、`lw=0` 固定**的条件下扫 sw，其余参数不动。
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
WEIGHTS = ("0", "0.15", "0.25", "0.35", "0.475", "0.65")


def cells():
    out = []
    for arm in ("tight", "sparse"):
        for p in sorted(ROOT.glob(f"**/fusion/{arm}/mast3r/trajectory_graph.csv")):
            c = p.parents[3]
            a, v = c / "docker2_slam/run_acceptance.json", c / "docker2_slam/vio_corrected_stream.csv"
            if not (a.exists() and v.exists() and (c / "lighthouse_body_ground_truth.csv").exists()):
                continue
            d = json.loads(a.read_text())
            if d.get("result") == "PASS" and not d.get("runtime_watchdog", {}).get("failures"):
                out.append((c, arm))
    return out


def run_one(cell, arm, sw, out, rep):
    md = cell / f"fusion/{arm}/mast3r"
    subprocess.run(
        [sys.executable, str(SCRIPT),
         "--mast3r", str(md / "trajectory_graph.csv"),
         "--docker2", str(cell / "docker2_slam/vio_corrected_stream.csv"),
         "--docker2-report", str(cell / "docker2_slam/run_acceptance.json"),
         "--body-t-camera-yaml", str(CONFIG),
         "--graph-report", str(md / "graph_fusion_report.json"),
         "--scale-horizon-s", "1", "--smoothing-s", "15",
         "--docker2-local-weight", "0",
         "--docker2-scale-weight", sw,
         "--roughness-threshold-mm", "9",
         "--use-docker2-orientation-for-lever-arm",
         "--output", str(out), "--report", str(rep)],
        check=True, capture_output=True)


def main():
    cs = cells()
    print(f"`lw=0` 固定，扫 `--docker2-scale-weight`；{len(cs)} 组（同一 graph，只动 sw）\n")
    print(f"{'cell':<44}{'arm':>7}" + "".join(f"{w:>15}" for w in WEIGHTS))
    print("-" * (51 + 15 * len(WEIGHTS)))
    acc = {w: [] for w in WEIGHTS}
    gate = {w: [] for w in WEIGHTS}
    for cell, arm in cs:
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        row = []
        for w in WEIGHTS:
            with tempfile.TemporaryDirectory() as td:
                out, rep = Path(td) / "f.csv", Path(td) / "r.json"
                try:
                    run_one(cell, arm, w, out, rep)
                    t, P, Q = E.load_trajectory(out)
                    _, _, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
                    m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
                    a, r = m["ate_translation_rmse_m"] * 1000, m["ate_rotation_rmse_deg"]
                except Exception as e:                                # noqa: BLE001
                    a, r = float("nan"), float("nan")
            acc[w].append(a)
            gate[w].append(r)
            row.append(a)
        best = np.nanmin(row)
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name:<44}{arm:>7}"
              + "".join(f"{a:>10.2f}{'*' if a == best else ' '}{'':>4}" for a in row))
    print("-" * (51 + 15 * len(WEIGHTS)))
    print(f"{'中位 ATE (mm)':<44}{'':>7}" + "".join(f"{np.nanmedian(acc[w]):>15.2f}" for w in WEIGHTS))
    print(f"{'中位 门 (°)':<44}{'':>7}" + "".join(f"{np.nanmedian(gate[w]):>15.2f}" for w in WEIGHTS))
    print(f"{'门过 (<2.0°)':<44}{'':>7}"
          + "".join(f"{int((np.array(gate[w])<2.0).sum()):>10}/{len(gate[w]):<4}" for w in WEIGHTS))
    ref = "0"
    print(f"{'vs sw=0 的逐 cell 胜负':<44}{'':>7}"
          + "".join(f"{int(np.nansum(np.array(acc[w])<np.array(acc[ref]))):>10}"
                    f"/{int(np.nansum(np.array(acc[w])>np.array(acc[ref]))):<4}" for w in WEIGHTS))
    print("\n判读：中位 ATE 与门过同时最优的那个 sw ⇒ 就是该用的值。")


if __name__ == "__main__":
    main()