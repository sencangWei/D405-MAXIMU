#!/usr/bin/env python3
"""跑**完整的 09-14 配方**并对比三套配置。

09-14 备份脚本的融合段：
    --docker2-local-weight 0 --docker2-scale-weight 0 --auto-docker2-scale-weight
现役：
    --docker2-local-weight 0.25 --docker2-scale-weight 0.475 --adaptive-local-weight
"""
import subprocess, sys, tempfile
from pathlib import Path
import numpy as np
sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
              "formal_runtime_calibration/vins_config.yaml")
SCRIPT = Path("/home/robot/ego_vio_humble/scripts/fuse_docker2_mast3r_complementary.py")

CONFIGS = {
    "现役 lw.25 sw.475 adapt": ["--docker2-local-weight", "0.25", "--docker2-scale-weight", "0.475",
                                "--adaptive-local-weight", "--adaptive-weight-strength", "0.45"],
    "09-14 lw0 + auto":        ["--docker2-local-weight", "0", "--docker2-scale-weight", "0",
                                "--auto-docker2-scale-weight"],
    "lw0 + sw.25 (扫描最优)":   ["--docker2-local-weight", "0", "--docker2-scale-weight", "0.25"],
}


def main():
    print("三套融合尾段配置对比（其余参数一致）\n")
    print(f"{'cell':<44}" + "".join(f"{k:>26}" for k in CONFIGS))
    print("-" * (44 + 26 * len(CONFIGS)))
    acc = {k: ([], [], []) for k in CONFIGS}
    for c in sorted({f.parents[2] for f in
                     ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
        gt = c / "lighthouse_body_ground_truth.csv"
        if not gt.exists():
            continue
        tg, Pg, Qg = E.load_trajectory(gt)
        row = []
        for k, extra in CONFIGS.items():
            md = c / "fusion_current/tight/mast3r"
            with tempfile.TemporaryDirectory() as td:
                out, rep = Path(td) / "f.csv", Path(td) / "r.json"
                cmd = [sys.executable, str(SCRIPT),
                       "--mast3r", str(md / "trajectory_graph.csv"),
                       "--docker2", str(c / "docker2_slam/vio_corrected_stream.csv"),
                       "--docker2-report", str(c / "docker2_slam/run_acceptance.json"),
                       "--body-t-camera-yaml", str(CONFIG),
                       "--graph-report", str(md / "graph_fusion_report.json"),
                       "--scale-horizon-s", "1", "--smoothing-s", "8",
                       "--roughness-threshold-mm", "9",
                       "--use-docker2-orientation-for-lever-arm",
                       "--output", str(out), "--report", str(rep)] + extra
                subprocess.run(cmd, check=True, capture_output=True)
                t, P, Q = E.load_trajectory(out)
                _, _, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
                m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
                a, r = m['ate_translation_rmse_m']*1000, m['ate_rotation_rmse_deg']
                acc[k][0].append(a); acc[k][1].append(r)
                acc[k][2].append(m['ate_translation_max_m']*1000)
            row.append((a, r))
        print(f"{str(c).replace(str(ROOT)+'/', ''):<44}" + "".join(f"{a:>12.2f}/{r:<13.2f}" for a, r in row))
    print("-" * (44 + 26 * len(CONFIGS)))
    for lab, i, f in (("中位 ATE (mm)", 0, np.median), ("中位 门 (°)", 1, np.median),
                      ("中位 max (mm)", 2, np.median)):
        print(f"{lab:<44}" + "".join(f"{f(acc[k][i]):>26.2f}" for k in CONFIGS))
    print(f"{'门过':<44}" + "".join(
        f"{int((np.array(acc[k][1])<2.0).sum()):>22}/{len(acc[k][1]):<3}" for k in CONFIGS))
    print(f"{'p95 中位 ATE':<44}" + "".join(f"{np.median(acc[k][0]):>26.2f}" for k in CONFIGS))


if __name__ == "__main__":
    main()
