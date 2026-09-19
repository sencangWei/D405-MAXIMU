#!/usr/bin/env python3
"""A/B：第 8/9 步要不要用 `--use-docker2-orientation-for-lever-arm`。

## 背景

第 8/9 步带这个开关时，输出四元数被整体替换成 `常量 × VINS姿态`
（`fuse_docker2_mast3r_complementary.py:200-226, 527`）⇒ MASt3R 姿态归零。
不带时走 `camera_to_body`，保留第 7/8 步真正融合出来的姿态
（那里 MASt3R 视觉 sigma 0.5° 与陀螺 0.25° 是联合优化的）。

本脚本对每个 cell 跑两臂、各自平滑、再按官方评测器口径对比。
复用现成的前端产物 ⇒ **不需要重跑 MASt3R 前端**。

用法: attitude_ab.py [输出目录]
"""
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble")
WORKFLOW = ROOT / "reports/lighthouse_umi_workflow"
PYTHON = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python")
VINS_CFG = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release"
    "/formal_runtime_calibration/vins_config.yaml"
)
FUSE_ARGS = [
    "--scale-horizon-s", "1", "--smoothing-s", "8",
    "--docker2-local-weight", "0.25", "--docker2-scale-weight", "0.475",
    "--adaptive-local-weight", "--roughness-threshold-mm", "9",
    "--adaptive-weight-strength", "0.45",
]


def run_step8(graph, vins, vins_report, graph_report, output, report, use_flag):
    cmd = [str(PYTHON), str(ROOT / "scripts/fuse_docker2_mast3r_complementary.py"),
           "--mast3r", str(graph), "--docker2", str(vins),
           "--docker2-report", str(vins_report), "--body-t-camera-yaml", str(VINS_CFG),
           *FUSE_ARGS, "--graph-report", str(graph_report),
           "--output", str(output), "--report", str(report)]
    if use_flag:
        cmd.append("--use-docker2-orientation-for-lever-arm")
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def smooth(source, output, report):
    return subprocess.run(
        [str(PYTHON), str(ROOT / "scripts/smooth_pose_trajectory.py"),
         "--input", str(source), "--output", str(output), "--method", "gaussian",
         "--gaussian-sigma-s", "0.025", "--report", str(report)],
        capture_output=True, text=True).returncode


def evaluate(estimate, gt):
    te, Pe, Qe = E.load_trajectory(estimate)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, ctx, interp_q = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return E.pose_errors(Pe[inside][valid], Qe[inside][valid], ctx[:, 1:4], interp_q, 30)


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "attitude_ab"
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = sorted(WORKFLOW.glob("**/fusion/*/mast3r/trajectory_graph.csv"))
    print(f"{len(cells)} 个 cell\n")
    header = (f"{'cell':<44}{'门rot':>13}{'纯漂移':>13}{'ATE_T(mm)':>13}{'P95(mm)':>11}")
    print(header)
    print(f"{'':<44}{'flag→nof':>13}{'flag→nof':>13}{'flag→nof':>13}{'flag→nof':>11}")
    print("-" * len(header))
    wins = []
    for graph in cells:
        group = graph.parents[3]
        vins = group / "docker2_slam/vio_corrected_stream.csv"
        vins_report = group / "docker2_slam/run_acceptance.json"
        if not (vins.exists() and vins_report.exists()):
            print(f"{str(group).split('lighthouse_umi_workflow/')[-1]:<44} 缺 VINS 输入")
            continue
        tag = str(group).split("lighthouse_umi_workflow/")[-1].replace("/", "_")
        results = {}
        for arm, flag in (("flag", True), ("nof", False)):
            raw = out_dir / f"{tag}_{arm}.csv"
            code = run_step8(graph, vins, vins_report,
                             graph.parent / "graph_fusion_report.json",
                             raw, out_dir / f"{tag}_{arm}_report.json", flag)
            if code != 0:
                results[arm] = None
                continue
            sm = out_dir / f"{tag}_{arm}_sm.csv"
            smooth(raw, sm, out_dir / f"{tag}_{arm}_smooth.json")
            results[arm] = evaluate(sm, group / "lighthouse_body_ground_truth.csv")
        if results["flag"] is None or results["nof"] is None:
            print(f"{tag:<44} 求解失败")
            continue
        a, b = results["flag"], results["nof"]
        wins.append((a["ate_rotation_rmse_deg"], b["ate_rotation_rmse_deg"],
                     a["ate_translation_rmse_m"], b["ate_translation_rmse_m"]))
        print(f"{tag:<44}"
              f"{a['ate_rotation_rmse_deg']:>6.2f}→{b['ate_rotation_rmse_deg']:<6.2f}"
              f"{a['attitude_aligned_ate_rotation_rmse_deg']:>6.2f}→"
              f"{b['attitude_aligned_ate_rotation_rmse_deg']:<6.2f}"
              f"{a['ate_translation_rmse_m']*1e3:>6.2f}→{b['ate_translation_rmse_m']*1e3:<6.2f}"
              f"{a['ate_translation_p95_m']*1e3:>5.2f}→{b['ate_translation_p95_m']*1e3:<5.2f}")
    if wins:
        w = np.array(wins)
        rot_better = (w[:, 1] < w[:, 0]).sum()
        ate_worse = (w[:, 3] > w[:, 2] * 1.02).sum()
        print(f"\n=== 汇总 {len(w)} 个 cell ===")
        print(f"  门rot 改善: {rot_better}/{len(w)}   中位变化 "
              f"{np.median(w[:,1]-w[:,0]):+.3f}°  (负=改善)")
        print(f"  ATE_T 变差>2%: {ate_worse}/{len(w)}   中位变化 "
              f"{np.median((w[:,3]-w[:,2])*1e3):+.3f} mm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
