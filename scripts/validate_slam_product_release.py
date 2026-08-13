#!/usr/bin/env python3
"""Aggregate immutable datasets and run reports into a product release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from slam_benchmark_environment import validate_environment_report
from slam_run_health import evaluate_slam_health, trajectory_diagnostics_from_csv
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


def validate_loop_observability(report: dict, label: str) -> list[str]:
    """Require accepted loop edges to carry PnP and optimizer evidence."""
    failures: list[str] = []
    accepted = int(report.get("automatic_loop_accepts", 0))
    if accepted == 0:
        return failures

    accepted_edges = report.get("pnp_quality", {}).get("accepted_edges", [])
    if len(accepted_edges) != accepted:
        failures.append(
            f"{label}: {accepted} accepted loops but {len(accepted_edges)} "
            "accepted PnP quality records"
        )
    for index, edge in enumerate(accepted_edges):
        numeric_fields = (
            "rmse_px",
            "p95_px",
            "current_hull_fraction",
            "old_hull_fraction",
        )
        if not all(
            isinstance(edge.get(field), (int, float))
            and math.isfinite(float(edge[field]))
            for field in numeric_fields
        ):
            failures.append(f"{label}: accepted PnP edge {index} has non-finite metrics")
            continue
        if int(edge.get("inliers", 0)) <= 0:
            failures.append(f"{label}: accepted PnP edge {index} has no inliers")
        if not 0.0 < float(edge["current_hull_fraction"]) <= 1.0:
            failures.append(
                f"{label}: accepted PnP edge {index} has invalid current hull"
            )
        if not 0.0 < float(edge["old_hull_fraction"]) <= 1.0:
            failures.append(f"{label}: accepted PnP edge {index} has invalid old hull")

    pose_graph = report.get("pose_graph_health", {})
    optimizations = int(pose_graph.get("optimizations", 0))
    usable = int(pose_graph.get("usable_optimizations", 0))
    rejected = int(pose_graph.get("rejected_optimizations", 0))
    if optimizations < accepted:
        failures.append(
            f"{label}: {accepted} accepted loops but only {optimizations} "
            "pose-graph optimization records"
        )
    if usable != optimizations or rejected != 0:
        failures.append(f"{label}: pose-graph optimization evidence is not fully usable")
    return failures


def validate_benchmark_environment(report: dict, label: str) -> list[str]:
    environment = report.get("benchmark_environment")
    if not isinstance(environment, dict):
        return [f"{label}: missing benchmark environment preflight"]
    return [f"{label}: {failure}" for failure in validate_environment_report(environment)]


def validate_run_health(
    report: dict, label: str, trajectory_path: Path | None = None
) -> list[str]:
    expected = evaluate_slam_health(report)
    failures = []
    if report.get("health") != expected:
        failures.append(f"{label}: run health report is missing or inconsistent")
    if expected.get("state") != "SLAM_HEALTHY":
        failures.append(f"{label}: run health is {expected.get('state')}")
    if trajectory_path is not None and trajectory_path.is_file():
        try:
            artifact = trajectory_diagnostics_from_csv(trajectory_path)
        except ValueError as exc:
            failures.append(f"{label}: invalid trajectory for health check: {exc}")
        else:
            if report.get("corrected_odometry_samples") != artifact["sample_count"]:
                failures.append(
                    f"{label}: corrected sample count does not match trajectory"
                )
            reported_diagnostics = report.get("corrected_trajectory_diagnostics", {})
            diagnostics_match = isinstance(reported_diagnostics, dict) and all(
                (
                    reported_diagnostics.get(name) is None
                    and measured is None
                )
                or (
                    isinstance(reported_diagnostics.get(name), (int, float))
                    and measured is not None
                    and math.isclose(
                        float(reported_diagnostics[name]),
                        float(measured),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                )
                for name, measured in artifact["diagnostics"].items()
            )
            if not diagnostics_match:
                failures.append(
                    f"{label}: corrected diagnostics do not match trajectory"
                )
    return failures


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
    qualified_spatial_threshold = None

    if require_complete and release_variant not in REQUIRED_VARIANTS:
        failures.append(
            "release_variant must be one of: " + ", ".join(REQUIRED_VARIANTS)
        )
    if require_complete:
        gate_evidence = manifest.get("pnp_spatial_gate_evidence", {})
        gate_report = load_hashed_json(
            gate_evidence.get("report"),
            gate_evidence.get("report_sha256"),
            "PnP spatial gate evidence",
            failures,
        )
        if gate_report is not None:
            if gate_report.get("result") != "PASS":
                failures.append("PnP spatial gate evidence is not PASS")
            if gate_report.get("threshold_freeze_allowed") is not True:
                failures.append("PnP spatial gate threshold is not qualified for freeze")
            selected_threshold = gate_report.get("selected_threshold")
            if not isinstance(selected_threshold, (int, float)) or not (
                0.0 < selected_threshold <= 1.0
            ):
                failures.append("PnP spatial gate selected threshold is invalid")
            else:
                qualified_spatial_threshold = float(selected_threshold)
            if gate_report.get("truth_policy") != (
                "development_and_validation_only_hidden_forbidden"
            ):
                failures.append("PnP spatial gate evidence violates tuning policy")

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
            if require_complete and qualified_spatial_threshold is not None:
                effective_threshold = report.get("min_loop_spatial_support")
                if not isinstance(effective_threshold, (int, float)) or not math.isclose(
                    effective_threshold, qualified_spatial_threshold, abs_tol=5e-5
                ):
                    failures.append(
                        f"{dataset_id}: effective PnP spatial threshold does not "
                        "match qualified evidence"
                    )
            if require_complete:
                failures.extend(validate_run_health(report, dataset_id))
            expected_loop = dataset.get("expected_loop")
            accepted = int(report.get("automatic_loop_accepts", 0))
            if expected_loop is True:
                loop_reports += 1
                if accepted < 1:
                    failures.append(f"{dataset_id}: expected loop was not accepted")
                endpoint = report.get("corrected_trajectory_diagnostics", {}).get(
                    "endpoint_delta_m"
                )
                if endpoint is None or endpoint >= thresholds["max_loop_endpoint_m"]:
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
                trajectory_value = variant_entry.get("trajectory")
                trajectory_path = resolve(trajectory_value or "")
                if trajectory_value:
                    variant_trajectories.append(str(trajectory_path.resolve()))
                trajectory_failure = verify_hash(
                    trajectory_path,
                    variant_entry.get("trajectory_sha256"),
                    f"{dataset_id}: {variant}: trajectory",
                )
                if trajectory_failure:
                    failures.append(trajectory_failure)
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
                    if variant_run.get("failure_scope") != "SLAM":
                        failures.append(
                            f"{dataset_id}: {variant}: run was not a valid SLAM evaluation"
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
                    if (
                        variant in {"auto_loop", "depth_plane"}
                        and qualified_spatial_threshold is not None
                    ):
                        effective_threshold = variant_run.get(
                            "min_loop_spatial_support"
                        )
                        if not isinstance(effective_threshold, (int, float)) or not math.isclose(
                            effective_threshold,
                            qualified_spatial_threshold,
                            abs_tol=5e-5,
                        ):
                            failures.append(
                                f"{dataset_id}: {variant}: effective PnP spatial "
                                "threshold does not match qualified evidence"
                            )
                    if variant_run.get("pose_graph_health", {}).get(
                        "rejected_optimizations", 0
                    ) != 0:
                        failures.append(
                            f"{dataset_id}: {variant}: pose graph rejected an "
                            "unusable solution"
                        )
                    failures.extend(
                        validate_loop_observability(
                            variant_run, f"{dataset_id}: {variant}"
                        )
                    )
                    failures.extend(
                        validate_benchmark_environment(
                            variant_run, f"{dataset_id}: {variant}"
                        )
                    )
                    failures.extend(
                        validate_run_health(
                            variant_run,
                            f"{dataset_id}: {variant}",
                            trajectory_path,
                        )
                    )
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
                    scored_trajectory = variant_ground_truth.get("estimate")
                    expected_trajectory = resolve(trajectory_value or "").resolve()
                    if (
                        not scored_trajectory
                        or Path(scored_trajectory).resolve() != expected_trajectory
                    ):
                        failures.append(
                            f"{dataset_id}: {variant}: ground truth report scored a "
                            "different trajectory"
                        )
                    scored_ground_truth = variant_ground_truth.get("ground_truth")
                    expected_ground_truth = resolve(
                        dataset.get("external_ground_truth") or ""
                    ).resolve()
                    if (
                        not scored_ground_truth
                        or Path(scored_ground_truth).resolve() != expected_ground_truth
                    ):
                        failures.append(
                            f"{dataset_id}: {variant}: ground truth report used a "
                            "different truth file"
                        )
                    if variant_ground_truth.get("truth_usage") != "post_run_scoring_only":
                        failures.append(
                            f"{dataset_id}: {variant}: ground truth usage is not "
                            "post-run scoring only"
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
                if variant == "depth_plane":
                    factor = load_hashed_json(
                        variant_entry.get("factor_report"),
                        variant_entry.get("factor_report_sha256"),
                        f"{dataset_id}: {variant}: factor",
                        failures,
                    )
                    if factor is not None:
                        plane_factor = factor.get("plane_factor", {})
                        if factor.get("result") != "PASS":
                            failures.append(
                                f"{dataset_id}: {variant}: factor report is not PASS"
                            )
                        if plane_factor.get("causal") is not True:
                            failures.append(
                                f"{dataset_id}: {variant}: factor is not causal"
                            )
                        if plane_factor.get("uses_absolute_height") is not False:
                            failures.append(
                                f"{dataset_id}: {variant}: factor uses absolute height"
                            )
                        if plane_factor.get("uses_endpoint_constraint") is not False:
                            failures.append(
                                f"{dataset_id}: {variant}: factor uses endpoint constraint"
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
