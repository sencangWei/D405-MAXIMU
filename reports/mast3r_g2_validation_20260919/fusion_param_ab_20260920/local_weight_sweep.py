#!/usr/bin/env python3
"""扫 `--docker2-local-weight`：把 VINS 局部注入拧到多少最好？

## 背景

`graph_vs_fusion.py` 证明 **local_weight=0（09-14 的设定）⇒ 融合是直通**，
而现役 0.25 使门从中位 2.06° 升到 2.38°、门过从 4/8 掉到 3/8。
所以「最优 local_weight 是多少」是个**直接可执行**的问题。

## 做法

逐 cell 调**真实脚本**（subprocess，不用自己复实现，避免走样），
只改 `--docker2-local-weight`，其余参数与现役工作流一致；输出喂官方评测器。
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
WEIGHTS = (0.0, 0.05, 0.10, 0.15, 0.25, 0.40)


def run_one(cell, lw, out, rep, adaptive):
    md = cell / "fusion_current/tight/mast3r"
    cmd = [sys.executable, str(SCRIPT),
           "--mast3r", str(md / "trajectory_graph.csv"),
           "--docker2", str(cell / "docker2_slam/vio_corrected_stream.csv"),
           "--docker2-report", str(cell / "docker2_slam/run_acceptance.json"),
           "--body-t-camera-yaml", str(CONFIG),
           "--graph-report", str(md / "graph_fusion_report.json"),
           "--scale-horizon-s", "1", "--smoothing-s", "8",
           "--docker2-local-weight", str(lw),
           "--docker2-scale-weight", "0.475",
           "--roughness-threshold-mm", "9",
           "--use-docker2-orientation-for-lever-arm",
           "--output", str(out), "--report", str(rep)]
    if adaptive:
        cmd += ["--adaptive-local-weight", "--adaptive-weight-strength", "0.45"]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    for adaptive in (False, True):
        print(f"\n{'='*94}\nlocal_weight 扫描（adaptive={'on' if adaptive else 'off'}）\n")
        print(f"{'cell':<44}" + "".join(f"{w:>15.2f}" for w in WEIGHTS))
        print(f"{'':<44}" + "".join(f"{'ATE / 门':>15}" for _ in WEIGHTS))
        print("-" * 94)
        acc = {w: ([], []) for w in WEIGHTS}
        for c in sorted({f.parents[2] for f in
                         ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
            gt = c / "lighthouse_body_ground_truth.csv"
            if not gt.exists():
                continue
            tg, Pg, Qg = E.load_trajectory(gt)
            row = []
            for w in WEIGHTS:
                with tempfile.TemporaryDirectory() as td:
                    out, rep = Path(td) / "f.csv", Path(td) / "r.json"
                    try:
                        run_one(c, w, out, rep, adaptive)
                        t, P, Q = E.load_trajectory(out)
                        _, _, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
                        m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
                        a, r = m['ate_translation_rmse_m'] * 1000, m['ate_rotation_rmse_deg']
                    except Exception as e:                               # noqa: BLE001
                        print(f"   ! {c.name} lw={w}: {e}")
                        a, r = float('nan'), float('nan')
                acc[w][0].append(a)
                acc[w][1].append(r)
                row.append((a, r))
            name = str(c).replace(str(ROOT) + "/", "")
            print(f"{name:<44}" + "".join(f"{a:>7.2f}/{r:<7.2f}" for a, r in row))
        print(f"{'-'*44}" + "".join(f"{'':>15}" for _ in WEIGHTS))
        line_a = "".join(f"{np.nanmedian(acc[w][0]):>15.2f}" for w in WEIGHTS)
        line_r = "".join(f"{np.nanmedian(acc[w][1]):>15.2f}" for w in WEIGHTS)
        line_p = "".join(f"{int((np.array(acc[w][1])<2.0).sum()):>8}/{len(acc[w][1]):<6}" for w in WEIGHTS)
        print(f"{'中位 ATE (mm)':<44}{line_a}")
        print(f"{'中位 门 (°)':<44}{line_r}")
        print(f"{'门过':<44}{line_p}")


if __name__ == "__main__":
    main()
