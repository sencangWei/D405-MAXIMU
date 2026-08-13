#!/usr/bin/env python3
"""Aggregate immutable datasets and run reports into a product release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from validate_slam_dataset_roles import ROOT, validate_manifest, verify_hash


DEFAULT_THRESHOLDS = {
    "min_pose_coverage": 0.98,
    "max_loop_endpoint_m": 0.01,
    "max_ate_translation_rmse_m": 0.01,
    "max_rpe_translation_rmse_m": 0.005,
    "max_rpe_rotation_rmse_deg": 0.5,
    "max_z_rmse_m": 0.01,
    "max_attitude_ate_rmse_deg": 0.5,
    "max_endpoint_drift_percent_of_path": 1.0,
    "max_failure_rate": 0.05,
    "min_hidden_runs_per_motion": 3,
}
REQUIRED_VARIANTS = ("raw_vins", "auto_loop", "depth_plane")


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_hashed_json(
    path_value: str | None,
    expected_hash: str | None,
    label: str,
    failures: list[str],
) -> dict | None:
    if not path_value:
        failures.append(f"{label}: missing report path")
        return None
    path = resolve(path_value)
    if not path.is_file():
        failures.append(f"{label}: missing report {path}")
        return None
    if not expected_hash:
        failures.append(f"{label}: missing report sha256")
        return None
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        failures.append(
            f"{label}: report hash changed ({actual_hash} != {expected_hash})"
        )
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def validate_release(manifest_path: Path, require_complete: bool) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    thresholds = {**DEFAULT_THRESHOLDS, **manifest.get("thresholds", {})}
    release_variant = manifest.get("release_variant")
    dataset_gate = validate_manifest(manifest_path, require_hidden=require_complete)
    failures = list(dataset_gate["failures"])
    measured_runs = 0
    failed_runs = 0
    hidden_runs_by_motion: dict[str, int] = {}
    ground_truth_reports = 0
    loop_reports = 0
    variant_report_counts = {name: 0 for name in REQUIRED_VARIANTS}
    variant_ground_truth_counts = {name: 0 for name in REQUIRED_VARIANTS}

    if require_complete and release_variant not in REQUIRED_VARIANTS:
        failures.append(
            "release_variant must be one of: " + ", ".join(REQUIRED_VARIANTS)
        )

    for dataset in manifest.get("datasets", []):
        dataset_id = dataset.get("id", "unknown")
        role = dataset.get("role")
        motion = dataset.get("motion", "unknown")
        report = load_hashed_json(
            dataset.get("run_report"),
            dataset.get("run_report_sha256"),
            f"{dataset_id}: run",
            failures,
        )
        if report is not None:
            measured_runs += 1
            if report.get("result") != "PASS":
                failed_runs += 1
                failures.append(f"{dataset_id}: run report is not PASS")
            if report.get("pose_coverage", 0.0) < thresholds["min_pose_coverage"]:
                failures.append(f"{dataset_id}: pose coverage below threshold")
            if report.get("loop_input_drop_events") != 0:
                failures.append(f"{dataset_id}: loop input drops are nonzero")
            if report.get("estimator_keyframe_queue_drop_events") != 0:
                failures.append(f"{dataset_id}: estimator keyframe drops are nonzero")
            expected_loop = dataset.get("expected_loop")
            accepted = int(report.get("automatic_loop_accepts", 0))
            if expected_loop is True:
                loop_reports += 1
                if accepted < 1:
                    failures.append(f"{dataset_id}: expected loop was not accepted")
                endpoint = report.get("corrected_trajectory_diagnostics", {}).get(
                    "endpoint_delta_m"
                )
                if endpoint is None or endpoint > thresholds["max_loop_endpoint_m"]:
                    failures.append(f"{dataset_id}: loop endpoint exceeds threshold")
            elif expected_loop is False and accepted:
                failures.append(f"{dataset_id}: false loop accepted")
            if role == "hidden_test":
                hidden_runs_by_motion[motion] = hidden_runs_by_motion.get(motion, 0) + 1

        ground_truth = load_hashed_json(
            dataset.get("ground_truth_report"),
            dataset.get("ground_truth_report_sha256"),
            f"{dataset_id}: ground truth",
            failures,
        ) if dataset.get("ground_truth_report") else None
        if role == "hidden_test" and ground_truth is None:
            failures.append(
                f"{dataset_id}: hidden test lacks hashed ground-truth evaluation report"
            )
        if ground_truth is not None:
            ground_truth_reports += 1
            checks = {
                "ate_translation_rmse_m": "max_ate_translation_rmse_m",
                "rpe_translation_rmse_m": "max_rpe_translation_rmse_m",
                "rpe_rotation_rmse_deg": "max_rpe_rotation_rmse_deg",
                "z_rmse_m": "max_z_rmse_m",
                "attitude_aligned_ate_rotation_rmse_deg":
                    "max_attitude_ate_rmse_deg",
                "endpoint_drift_percent_of_path":
                    "max_endpoint_drift_percent_of_path",
            }
            for metric, threshold_name in checks.items():
                value = ground_truth.get(metric)
                if value is None or value > thresholds[threshold_name]:
                    failures.append(
                        f"{dataset_id}: {metric} missing or above threshold"
                    )

        if require_complete and role == "hidden_test":
            variant_reports = dataset.get("variant_reports", {})
            variant_trajectories: list[str] = []
            for variant in REQUIRED_VARIANTS:
                variant_entry = variant_reports.get(variant)
                if not isinstance(variant_entry, dict):
                    failures.append(
                        f"{dataset_id}: {variant}: missing variant report"
                    )
                    continue
                variant_run = load_hashed_json(
                    variant_entry.get("run_report"),
                    variant_entry.get("run_report_sha256"),
                    f"{dataset_id}: {variant}: run",
                    failures,
                )
                if variant_run is not None:
                    variant_report_counts[variant] += 1
                    if variant_run.get("variant") != variant:
                        failures.append(
                            f"{dataset_id}: {variant}: run report variant mismatch"
                        )
                    if variant_run.get("result") != "PASS":
                        failures.append(
                            f"{dataset_id}: {variant}: run report is not PASS"
                        )
                    if variant_run.get("pose_coverage", 0.0) < thresholds[
                        "min_pose_coverage"
                    ]:
                        failures.append(
                            f"{dataset_id}: {variant}: pose coverage below threshold"
                        )
                    if variant_run.get("loop_input_drop_events") != 0:
                        failures.append(
                            f"{dataset_id}: {variant}: loop input drops are nonzero"
                        )
                    if variant_run.get("estimator_keyframe_queue_drop_events") != 0:
                        failures.append(
                            f"{dataset_id}: {variant}: estimator keyframe drops are nonzero"
                        )
                trajectory_value = variant_entry.get("trajectory")
                if trajectory_value:
                    variant_trajectories.append(str(resolve(trajectory_value).resolve()))
                trajectory_failure = verify_hash(
                    resolve(trajectory_value or ""),
                    variant_entry.get("trajectory_sha256"),
                    f"{dataset_id}: {variant}: trajectory",
                )
                if trajectory_failure:
                    failures.append(trajectory_failure)
                variant_ground_truth = load_hashed_json(
                    variant_entry.get("ground_truth_report"),
                    variant_entry.get("ground_truth_report_sha256"),
                    f"{dataset_id}: {variant}: ground truth",
                    failures,
                )
                if variant_ground_truth is not None:
                    variant_ground_truth_counts[variant] += 1
                    if variant_ground_truth.get("variant") != variant:
                        failures.append(
                            f"{dataset_id}: {variant}: ground truth report variant mismatch"
                        )
                    if variant == release_variant:
                        checks = {
                            "ate_translation_rmse_m": "max_ate_translation_rmse_m",
                            "rpe_translation_rmse_m": "max_rpe_translation_rmse_m",
                            "rpe_rotation_rmse_deg": "max_rpe_rotation_rmse_deg",
                            "z_rmse_m": "max_z_rmse_m",
                            "attitude_aligned_ate_rotation_rmse_deg":
                                "max_attitude_ate_rmse_deg",
                            "endpoint_drift_percent_of_path":
                                "max_endpoint_drift_percent_of_path",
                        }
                        for metric, threshold_name in checks.items():
                            value = variant_ground_truth.get(metric)
                            if value is None or value > thresholds[threshold_name]:
                                failures.append(
                                    f"{dataset_id}: {variant}: {metric} missing or "
                                    "above release threshold"
                                )
            if len(set(variant_trajectories)) != len(REQUIRED_VARIANTS):
                failures.append(
                    f"{dataset_id}: variant trajectory paths must be distinct"
                )

    failure_rate = failed_runs / measured_runs if measured_runs else 1.0
    if failure_rate > thresholds["max_failure_rate"]:
        failures.append(
            f"failure rate {failure_rate:.3f} > {thresholds['max_failure_rate']:.3f}"
        )
    if require_complete:
        if ground_truth_reports == 0:
            failures.append("no hashed external-ground-truth evaluation report")
        if not any(variant_report_counts.values()):
            failures.append("no hidden three-variant evaluation matrix")
        for motion in dataset_gate["required_motions"]:
            count = hidden_runs_by_motion.get(motion, 0)
            if count < thresholds["min_hidden_runs_per_motion"]:
                failures.append(
                    f"hidden motion {motion}: {count} runs < "
                    f"{thresholds['min_hidden_runs_per_motion']}"
                )

    result = "PASS" if not failures else "FAIL"
    if failures:
        release_readiness = "NOT_READY"
    elif require_complete:
        release_readiness = "CUSTOMER_READY"
    else:
        release_readiness = "CANDIDATE_PASS"

    return {
        "result": result,
        "release_readiness": release_readiness,
        "customer_release_complete": release_readiness == "CUSTOMER_READY",
        "evaluation_scope": (
            "complete_hidden_release" if require_complete else "candidate_evidence_only"
        ),
        "thresholds": thresholds,
        "release_variant": release_variant,
        "required_variants": list(REQUIRED_VARIANTS),
        "dataset_gate": dataset_gate,
        "measured_runs": measured_runs,
        "ground_truth_reports": ground_truth_reports,
        "loop_reports": loop_reports,
        "variant_report_counts": variant_report_counts,
        "variant_ground_truth_counts": variant_ground_truth_counts,
        "hidden_runs_by_motion": hidden_runs_by_motion,
        "failure_rate": failure_rate,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="客户SLAM总交付门禁")
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "config/slam_product_datasets.json"
    )
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_release(args.manifest.resolve(), args.require_complete)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
