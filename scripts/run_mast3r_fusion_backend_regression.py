#!/usr/bin/env python3
"""Re-run only the UMI-only fusion backend over successful corpus baselines."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from scripts.run_mast3r_fusion_regression_corpus import (
        EVALUATION_ACCEPTED_CODES,
        precision_summary,
        run_logged,
        write_error_marker,
    )
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from run_mast3r_fusion_regression_corpus import (
        EVALUATION_ACCEPTED_CODES,
        precision_summary,
        run_logged,
        write_error_marker,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config/mast3r_fusion_regression_corpus_13.json"
DEFAULT_BASELINE_ROOT = ROOT / "reports/mast3r_fusion_regression_13/baseline_current"
DEFAULT_OUTPUT_ROOT = ROOT / "reports/mast3r_fusion_regression_13/backend_restored_v1"
VINS_CONFIG = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/"
    "docker2_release/formal_runtime_calibration/vins_config.yaml"
)
IMU_CALIBRATION = ROOT / "config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"


def scored_dataset_ids(summary: dict) -> set[str]:
    return {
        item["id"]
        for item in summary.get("results", [])
        if item.get("status") == "SCORED"
    }


def build_graph_command(
    dataset: dict,
    baseline: Path,
    output: Path,
    visual_position_sigma_m: float = 0.020,
    metric_scale_mode: str = "joint",
    correction_cap_mode: str = "global",
) -> list[str]:
    mast3r = baseline / "fusion/mast3r"
    return [
        sys.executable,
        str(ROOT / "scripts/fuse_mast3r_stereo_imu.py"),
        "--session",
        dataset["session"],
        "--trajectory",
        str(mast3r / "trajectory_imu_metric.csv"),
        "--stream",
        "infrared_left",
        "--stereo-report",
        str(mast3r / "stereo_scale_bidirectional_report.json"),
        "--additional-stereo-report",
        str(mast3r / "stereo_scale_long_hops_report.json"),
        "--additional-stereo-report",
        str(mast3r / "stereo_scale_dense10hz_report.json"),
        "--additional-stereo-report",
        str(mast3r / "stereo_scale_multisecond_report.json"),
        "--imu-scale-report",
        str(mast3r / "imu_scale_report.json"),
        "--vins-config",
        str(VINS_CONFIG),
        "--imu-calibration",
        str(IMU_CALIBRATION),
        "--expected-td-s",
        "-0.009109323",
        "--orientation-node-stride",
        "10",
        "--position-node-stride",
        "10",
        "--minimum-stereo-sample-hop",
        "1",
        "--keyframe-dir",
        str(mast3r / "mast3r_logs/keyframes/dataset"),
        "--relative-motion-trajectory",
        str(baseline / "vins/vio_corrected_stream.csv"),
        "--relative-motion-report",
        str(baseline / "vins/run_acceptance.json"),
        "--relative-motion-sigma-m",
        "0.008",
        "--visual-position-sigma-m",
        str(visual_position_sigma_m),
        "--joint-max-correction-mm",
        "25",
        "--joint-correction-cap-mode",
        correction_cap_mode,
        "--full-rate-imu-position-refinement",
        "--full-rate-max-correction-mm",
        "20",
        "--metric-scale-mode",
        metric_scale_mode,
        "--position-mode",
        "keyframe-graph",
        "--output",
        str(output / "trajectory_graph.csv"),
        "--report",
        str(output / "graph_report.json"),
    ]


def build_fusion_command(baseline: Path, output: Path) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts/fuse_docker2_mast3r_complementary.py"),
        "--mast3r",
        str(output / "trajectory_graph.csv"),
        "--docker2",
        str(baseline / "vins/vio_corrected_stream.csv"),
        "--docker2-report",
        str(baseline / "vins/run_acceptance.json"),
        "--body-t-camera-yaml",
        str(VINS_CONFIG),
        "--scale-horizon-s",
        "1",
        "--smoothing-s",
        "8",
        "--docker2-local-weight",
        "0",
        "--docker2-scale-weight",
        "0",
        "--roughness-threshold-mm",
        "9",
        "--use-docker2-orientation-for-lever-arm",
        "--output",
        str(output / "trajectory_fused.csv"),
        "--report",
        str(output / "fusion_report.json"),
    ]


def build_score_command(
    baseline: Path, output: Path, thresholds: dict
) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts/evaluate_slam_ground_truth.py"),
        "--estimate",
        str(output / "trajectory_fused.csv"),
        "--ground-truth",
        str(baseline / "score/lighthouse_body_ground_truth.csv"),
        "--output",
        str(output / "score/precision.json"),
        "--plot",
        str(output / "score/precision.png"),
        "--report-md",
        str(output / "score/precision.md"),
        "--max-ate-rmse-mm",
        str(thresholds["max_ate_rmse_mm"]),
        "--max-ate-p95-mm",
        str(thresholds["max_ate_p95_mm"]),
        "--max-ate-max-mm",
        str(thresholds["max_ate_max_mm"]),
        "--min-within-10mm-ratio",
        str(thresholds["min_within_10mm_ratio"]),
        "--max-rotation-rmse-deg",
        str(thresholds["max_rotation_rmse_deg"]),
        "--min-timestamp-overlap-ratio",
        str(thresholds["min_timestamp_overlap_ratio"]),
    ]


def write_summary(path: Path, results: list[dict]) -> None:
    scored = [item for item in results if item.get("status") == "SCORED"]
    passing = [
        item for item in scored if item.get("precision", {}).get("result") == "PASS"
    ]
    report = {
        "schema": "umi_mast3r_fusion_backend_regression_v1",
        "updated_at": datetime.now().astimezone().isoformat(),
        "slam_supervision": False,
        "external_ground_truth_usage": "post_slam_scoring_only",
        "dataset_count": len(results),
        "scored_count": len(scored),
        "passing_count": len(passing),
        "all_sessions_pass": len(scored) == len(results) and len(passing) == len(results),
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline-root", type=Path, default=DEFAULT_BASELINE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--visual-position-sigma-m", type=float, default=0.020)
    parser.add_argument(
        "--metric-scale-mode", choices=("imu", "joint", "stereo"), default="joint"
    )
    parser.add_argument(
        "--joint-correction-cap-mode",
        choices=("global", "per-node", "per-frame"),
        default="global",
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.visual_position_sigma_m <= 0.0:
        parser.error("--visual-position-sigma-m must be positive")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    baseline_summary = json.loads(
        (args.baseline_root / "summary.json").read_text(encoding="utf-8")
    )
    available = scored_dataset_ids(baseline_summary)
    requested = set(args.dataset)
    if requested - available:
        raise ValueError(
            f"datasets do not have scored baselines: {sorted(requested - available)}"
        )
    datasets = [
        item
        for item in manifest["datasets"]
        if item["id"] in available and (not requested or item["id"] in requested)
    ]
    results = [
        {"id": item["id"], "fold": item["fold"], "status": "PENDING"}
        for item in datasets
    ]
    summary_path = args.output_root / "summary.json"
    had_errors = False

    for index, dataset in enumerate(datasets):
        item = results[index]
        baseline = args.baseline_root / dataset["id"]
        output = args.output_root / dataset["id"]
        precision = output / "score/precision.json"
        error_marker = output / "dataset_error.json"
        if args.resume and precision.is_file():
            item["status"] = "SCORED"
            item["precision"] = precision_summary(precision)
            write_summary(summary_path, results)
            continue
        try:
            output.mkdir(parents=True, exist_ok=True)
            run_logged(
                build_graph_command(
                    dataset,
                    baseline,
                    output,
                    args.visual_position_sigma_m,
                    args.metric_scale_mode,
                    args.joint_correction_cap_mode,
                ),
                output / "graph.log",
            )
            run_logged(build_fusion_command(baseline, output), output / "fusion.log")
            # External truth is introduced only after the UMI-only trajectory exists.
            run_logged(
                build_score_command(baseline, output, manifest["thresholds"]),
                output / "score.log",
                accepted_codes=EVALUATION_ACCEPTED_CODES,
            )
            item["status"] = "SCORED"
            item["precision"] = precision_summary(precision)
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            item["status"] = "ERROR"
            item["error"] = str(error)
            had_errors = True
            write_error_marker(error_marker, dataset["id"], error)
        write_summary(summary_path, results)

    print(f"Backend regression summary: {summary_path}")
    return 3 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
