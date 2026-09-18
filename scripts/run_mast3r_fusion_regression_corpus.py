#!/usr/bin/env python3
"""Run one frozen MASt3R fusion pipeline across the 13-session corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from scripts.validate_mast3r_fusion_regression_corpus import validate_manifest
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from validate_mast3r_fusion_regression_corpus import validate_manifest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config/mast3r_fusion_regression_corpus_13.json"
DEFAULT_OUTPUT = ROOT / "reports/mast3r_fusion_regression_13/baseline_current"
VINS_CONFIG = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/"
    "docker2_release/formal_runtime_calibration/vins_config.yaml"
)
EVALUATION_ACCEPTED_CODES = {0, 3}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_calibration(manifest: dict, dataset: dict) -> dict:
    """Return the calibration epoch explicitly bound to this capture."""
    calibration = dataset.get("tracker_body_calibration")
    if calibration is None:
        calibration = manifest.get("tracker_body_calibration")
    if not isinstance(calibration, dict):
        raise ValueError(f"{dataset.get('id', '?')}: tracker/body calibration is missing")
    return calibration


def build_vins_command(dataset: dict, output: Path) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts/test_vins_auto_loop.py"),
        dataset["session"],
        "--config",
        str(VINS_CONFIG),
        "--imu-shift-ms",
        "0",
        "--rate",
        "0.5",
        "--expect-loop",
        "any",
        "--out-dir",
        str(output / "vins"),
    ]


def build_fusion_command(dataset: dict, output: Path) -> list[str]:
    return [
        str(ROOT / "scripts/mast3r_slam_precision_workflow.sh"),
        "fusion",
        dataset["session"],
        str(output / "vins/vio_corrected_stream.csv"),
        str(output / "vins/run_acceptance.json"),
        str(output / "fusion"),
    ]


def build_ground_truth_command(dataset: dict, calibration: Path, output: Path) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts/apply_lighthouse_aprilgrid_calibration.py"),
        "--query-times",
        str(output / "fusion/trajectory_fused.csv"),
        "--tracker",
        dataset["tracker_csv"],
        "--d405-frames",
        str(Path(dataset["session"]) / "d405_frames.csv"),
        "--calibration",
        str(calibration),
        "--body-camera-config",
        str(VINS_CONFIG),
        "--target",
        "body",
        "--output",
        str(output / "score/lighthouse_body_ground_truth.csv"),
        "--report",
        str(output / "score/lighthouse_ground_truth_provenance.json"),
    ]


def build_score_command(output: Path, thresholds: dict) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts/evaluate_slam_ground_truth.py"),
        "--estimate",
        str(output / "fusion/trajectory_fused.csv"),
        "--ground-truth",
        str(output / "score/lighthouse_body_ground_truth.csv"),
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


def run_logged(command: list[str], log_path: Path, accepted_codes: set[int] = {0}) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"$ {' '.join(command)}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        return_code = process.wait()
    if return_code not in accepted_codes:
        raise subprocess.CalledProcessError(return_code, command)
    return return_code


def precision_summary(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        "result": report.get("result"),
        "rmse_mm": report.get("ate_translation_rmse_m", 0.0) * 1000.0,
        "p95_mm": report.get("ate_translation_p95_m", 0.0) * 1000.0,
        "max_mm": report.get("ate_translation_max_m", 0.0) * 1000.0,
        "within_10mm_ratio": report.get("ate_translation_within_10mm_ratio"),
        "rotation_rmse_deg": report.get("ate_rotation_rmse_deg"),
        "precision_report": str(path.resolve()),
    }


def load_resume_result(
    precision: Path,
    error_marker: Path,
    expected_calibration: dict | None = None,
) -> dict | None:
    provenance = precision.parent / "lighthouse_ground_truth_provenance.json"
    calibration_matches = True
    if expected_calibration is not None:
        calibration_matches = False
        if provenance.is_file():
            report = json.loads(provenance.read_text(encoding="utf-8"))
            calibration_matches = (
                report.get("sha256", {}).get("calibration")
                == expected_calibration.get("sha256")
            )
    if precision.is_file() and calibration_matches:
        return {"status": "SCORED", "precision": precision_summary(precision)}
    if error_marker.is_file():
        marker = json.loads(error_marker.read_text(encoding="utf-8"))
        return {"status": "ERROR", "error": marker["error"]}
    return None


def write_error_marker(path: Path, dataset_id: str, error: Exception) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"dataset_id": dataset_id, "error": str(error)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_summary(path: Path, manifest_path: Path, results: list[dict]) -> None:
    complete = [item for item in results if item.get("status") == "SCORED"]
    passing = [item for item in complete if item.get("precision", {}).get("result") == "PASS"]
    summary = {
        "schema": "umi_mast3r_fusion_regression_summary_v1",
        "updated_at": datetime.now().astimezone().isoformat(),
        "manifest": str(manifest_path.resolve()),
        "slam_supervision": False,
        "external_ground_truth_usage": "post_slam_scoring_only",
        "dataset_count": len(results),
        "scored_count": len(complete),
        "passing_count": len(passing),
        "all_sessions_pass": len(complete) == len(results) and len(passing) == len(results),
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    validation = validate_manifest(manifest_path)
    print(json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False))
    if validation["result"] != "PASS":
        return 2
    if args.validate_only:
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = set(args.dataset)
    datasets = [
        dataset
        for dataset in manifest["datasets"]
        if not selected or dataset["id"] in selected
    ]
    missing = selected - {dataset["id"] for dataset in datasets}
    if missing:
        raise ValueError(f"unknown dataset ids: {sorted(missing)}")

    output_root = args.output_root.resolve()
    results = [
        {"id": dataset["id"], "fold": dataset["fold"], "status": "PENDING"}
        for dataset in datasets
    ]
    summary_path = output_root / "summary.json"
    had_errors = False

    for index, dataset in enumerate(datasets):
        item = results[index]
        output = output_root / dataset["id"]
        precision = output / "score/precision.json"
        error_marker = output / "dataset_error.json"
        try:
            calibration_record = dataset_calibration(manifest, dataset)
            calibration = Path(calibration_record["path"]).resolve()
            resumed = (
                load_resume_result(precision, error_marker, calibration_record)
                if args.resume
                else None
            )
            if resumed is not None:
                item.update(resumed)
                had_errors = had_errors or item["status"] == "ERROR"
                write_summary(summary_path, manifest_path, results)
                continue

            print(f"\n=== {dataset['id']} ({index + 1}/{len(datasets)}) ===", flush=True)
            output.mkdir(parents=True, exist_ok=True)
            run_logged(build_vins_command(dataset, output), output / "vins_stage.log")
            run_logged(build_fusion_command(dataset, output), output / "fusion_stage.log")
            if not (output / "fusion/trajectory_fused.csv").is_file():
                raise FileNotFoundError(output / "fusion/trajectory_fused.csv")

            # Truth is introduced only after the UMI-only trajectory is complete.
            run_logged(
                build_ground_truth_command(dataset, calibration, output),
                output / "ground_truth_stage.log",
            )
            run_logged(
                build_score_command(output, manifest["thresholds"]),
                output / "score_stage.log",
                accepted_codes=EVALUATION_ACCEPTED_CODES,
            )
            item["status"] = "SCORED"
            item["precision"] = precision_summary(precision)
            item["tracker_body_calibration"] = {
                "path": str(calibration),
                "sha256": sha256(calibration),
            }
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            item["status"] = "ERROR"
            item["error"] = str(error)
            had_errors = True
            write_error_marker(error_marker, dataset["id"], error)
            write_summary(summary_path, manifest_path, results)
            print(f"{dataset['id']} failed: {error}", file=sys.stderr)
            continue
        write_summary(summary_path, manifest_path, results)

    print(f"Regression summary: {summary_path}")
    return 3 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
