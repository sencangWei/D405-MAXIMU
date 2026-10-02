#!/usr/bin/env python3
"""Read-only DEVELOPMENT-ONLY acceptance audit for dual-IR regression batches.

This script does not run SLAM, does not rescore, and does not promote a
candidate.  It verifies that a frozen 25-record development batch is complete,
globally consistent, onboard-only, and already officially scored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any


HEADER = "DEVELOPMENT_ONLY: read-only audit; not a blind-test or production acceptance claim"
REQUIRED_COUNT = 25
MAX_ATE_M = 0.010
MIN_OVERLAP = 0.98


def reject_nonfinite(token: str) -> None:
    raise ValueError(f"non-finite JSON constant {token}")


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream, parse_constant=reject_nonfinite)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def same_path(left: str | Path, right: str | Path) -> bool:
    return Path(left).resolve() == Path(right).resolve()


def under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def fail(failures: list[dict[str, str]], record_id: str, message: str) -> None:
    failures.append({"id": record_id, "message": message})


def selected_variant(summary_result: dict[str, Any], policy: str) -> dict[str, Any]:
    return summary_result.get("variants", {}).get(policy, {})


def artifact_dir_for(record: dict[str, Any], summary_result: dict[str, Any], policy: str, root: Path) -> Path:
    variant = selected_variant(summary_result, policy)
    explicit = variant.get("artifact_dir") or record.get("artifact_dir")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = root / path
        return path.resolve()
    return (root / record["id"] / policy).resolve()


def validate_hash_map(candidate: dict[str, Any], failures: list[dict[str, str]], record_id: str) -> None:
    hashes = candidate.get("input_sha256")
    if not isinstance(hashes, dict) or not hashes:
        fail(failures, record_id, "candidate_manifest input_sha256 missing")
        return
    for name, expected in hashes.items():
        path = Path(name)
        if not path.is_file():
            fail(failures, record_id, f"hashed input missing: {path}")
            continue
        actual = sha256(path)
        if actual != expected:
            fail(failures, record_id, f"input hash mismatch: {path}")


def validate_metadata_onboard(
    payload: dict[str, Any], failures: list[dict[str, str]], record_id: str, label: str
) -> None:
    if payload.get("external_ground_truth_used") is not False:
        fail(failures, record_id, f"{label} is not GT-independent")
    if payload.get("slam_supervision") is not False:
        fail(failures, record_id, f"{label} does not disable slam_supervision")


def validate_score(
    record: dict[str, Any],
    summary_result: dict[str, Any],
    artifact: Path,
    policy: str,
    failures: list[dict[str, str]],
    global_state: dict[str, Any],
) -> dict[str, Any]:
    record_id = record["id"]
    row = {"id": record_id, "artifact_dir": str(artifact), "status": "PASS", "policy": policy}
    paths = {
        "candidate": artifact / "candidate_manifest.json",
        "graph": artifact / "graph_report.json",
        "estimate": artifact / "body_trajectory_fused.csv",
        "workflow": artifact / "score" / "workflow_manifest.json",
        "reference_provenance": artifact / "score" / "reference_provenance.json",
        "precision": artifact / "score" / "precision.json",
    }
    for label, path in paths.items():
        if not path.is_file():
            fail(failures, record_id, f"missing {label}: {path}")
            row["status"] = "FAIL"
            return row

    try:
        candidate = read_json(paths["candidate"])
        graph = read_json(paths["graph"])
        workflow = read_json(paths["workflow"])
        provenance = read_json(paths["reference_provenance"])
        precision = read_json(paths["precision"])
    except Exception as error:
        fail(failures, record_id, f"invalid JSON: {type(error).__name__}: {error}")
        row["status"] = "FAIL"
        return row

    validate_metadata_onboard(candidate, failures, record_id, "candidate_manifest")
    validate_metadata_onboard(graph, failures, record_id, "graph_report")
    if workflow.get("slam_supervision") is not False:
        fail(failures, record_id, "workflow_manifest does not disable slam_supervision")
    if not same_path(candidate.get("session", ""), record.get("session", "")):
        fail(failures, record_id, "candidate_manifest session does not match manifest record")
    if graph.get("output_frame") != "body_imu_origin":
        fail(failures, record_id, "graph_report output_frame is not body_imu_origin")

    policy_arguments = candidate.get("policy_arguments")
    if not isinstance(policy_arguments, dict):
        fail(failures, record_id, "candidate_manifest policy_arguments missing")
    elif global_state.setdefault("policy_arguments", policy_arguments) != policy_arguments:
        fail(failures, record_id, "candidate_manifest policy_arguments are not globally consistent")
    if graph.get("policy_arguments") != policy_arguments:
        fail(failures, record_id, "graph_report policy_arguments do not match candidate_manifest")

    validate_hash_map(candidate, failures, record_id)

    estimate = paths["estimate"].resolve()
    if workflow.get("schema") != "official_steamvr_slam_score_v1":
        fail(failures, record_id, "workflow_manifest schema is not official scorer schema")
    if workflow.get("result") != "SCORING_COMPLETED":
        fail(failures, record_id, "workflow_manifest result is not SCORING_COMPLETED")
    if workflow.get("estimate_unchanged") is not True:
        fail(failures, record_id, "workflow_manifest estimate_unchanged is not true")
    if not same_path(workflow.get("estimate", ""), estimate):
        fail(failures, record_id, "workflow_manifest estimate is not body output trajectory")
    if workflow.get("estimate_frame") != "body_imu_origin":
        fail(failures, record_id, "workflow_manifest estimate_frame is not body_imu_origin")
    if estimate.is_file() and workflow.get("estimate_sha256") != sha256(estimate):
        fail(failures, record_id, "workflow_manifest estimate_sha256 does not match current estimate")
    reference = Path(workflow.get("reference_manifest", ""))
    if not reference.is_file():
        fail(failures, record_id, "workflow_manifest reference_manifest is missing")
    else:
        ref_tuple = (str(reference.resolve()), workflow.get("reference_manifest_sha256"))
        if workflow.get("reference_manifest_sha256") != sha256(reference):
            fail(failures, record_id, "workflow_manifest reference_manifest_sha256 mismatch")
        elif global_state.setdefault("reference", ref_tuple) != ref_tuple:
            fail(failures, record_id, "workflow_manifest reference is not globally frozen")

    if provenance.get("schema") != "lighthouse_aprilgrid_ground_truth_provenance_v1":
        fail(failures, record_id, "reference_provenance schema mismatch")
    if provenance.get("result") != "PASS" or provenance.get("slam_supervision") is not False:
        fail(failures, record_id, "reference_provenance is not a passing unsupervised reference")
    if provenance.get("reference_manifest_sha256") != workflow.get("reference_manifest_sha256"):
        fail(failures, record_id, "reference_provenance reference hash does not match workflow")
    capture_manifest = Path(record.get("capture_dir", "")) / "capture_manifest.json"
    if capture_manifest.is_file() and provenance.get("capture_manifest_sha256") != sha256(capture_manifest):
        fail(failures, record_id, "reference_provenance capture_manifest_sha256 mismatch")
    prov_inputs = provenance.get("inputs", {})
    prov_hashes = provenance.get("sha256", {})
    for key in ("query_timestamps", "tracker", "calibration", "body_camera_config", "d405_frames"):
        value = prov_inputs.get(key)
        if not value or not Path(value).is_file():
            fail(failures, record_id, f"reference_provenance input missing: {key}")
    if prov_inputs.get("query_timestamps") and not same_path(prov_inputs["query_timestamps"], estimate):
        fail(failures, record_id, "reference_provenance query_timestamps is not body output trajectory")
    expected_tracker = Path(record.get("capture_dir", "")) / "tracker.csv"
    if prov_inputs.get("tracker") and not same_path(prov_inputs["tracker"], expected_tracker):
        fail(failures, record_id, "reference_provenance tracker is not record capture tracker.csv")
    expected_frames = Path(record.get("session", "")) / "d405_frames.csv"
    if prov_inputs.get("d405_frames") and not same_path(prov_inputs["d405_frames"], expected_frames):
        fail(failures, record_id, "reference_provenance d405_frames is not record session d405_frames.csv")
    tracker = prov_inputs.get("tracker")
    if tracker and Path(tracker).is_file() and provenance.get("tracker_csv_sha256") != sha256(Path(tracker)):
        fail(failures, record_id, "reference_provenance tracker_csv_sha256 mismatch")
    for key in ("calibration", "body_camera_config"):
        value = prov_inputs.get(key)
        if value and Path(value).is_file() and prov_hashes.get(key) != sha256(Path(value)):
            fail(failures, record_id, f"reference_provenance {key} hash mismatch")
    output_reference = provenance.get("output")
    if output_reference and not Path(output_reference).is_file():
        fail(failures, record_id, "reference_provenance output reference file is missing")
    if output_reference and precision.get("ground_truth") and not same_path(output_reference, precision["ground_truth"]):
        fail(failures, record_id, "reference_provenance output does not match precision ground_truth")

    if precision.get("result") != "PASS":
        fail(failures, record_id, "precision result is not PASS")
    if precision.get("estimate_frame") != "as_recorded":
        fail(failures, record_id, "precision estimate_frame is not as_recorded")
    if precision.get("estimate") and not same_path(precision["estimate"], estimate):
        fail(failures, record_id, "precision estimate is not body output trajectory")
    max_m = precision.get("ate_translation_max_m")
    samples = precision.get("samples")
    total = precision.get("estimate_samples_total")
    overlap = precision.get("timestamp_overlap_ratio")
    for key in (
        "ate_translation_max_m",
        "ate_translation_rmse_m",
        "ate_translation_p95_m",
        "ate_translation_mean_m",
        "timestamp_overlap_ratio",
    ):
        if not is_finite_number(precision.get(key)):
            fail(failures, record_id, f"precision {key} is not finite")
    if is_finite_number(max_m) and float(max_m) > MAX_ATE_M:
        fail(failures, record_id, f"precision max {max_m} exceeds {MAX_ATE_M}")
    if is_finite_number(overlap) and float(overlap) < MIN_OVERLAP:
        fail(failures, record_id, f"precision overlap {overlap} below {MIN_OVERLAP}")
    if not isinstance(samples, int) or not isinstance(total, int) or samples != total or samples < 4:
        fail(failures, record_id, "precision samples must equal estimate_samples_total and be >=4")

    summary_score = selected_variant(summary_result, policy).get("score") or record.get("selected_score") or {}
    if "ate_translation_max_m" in summary_score and summary_score["ate_translation_max_m"] != max_m:
        fail(failures, record_id, "summary selected score max does not match precision")
    if "samples" in summary_score and summary_score["samples"] != samples:
        fail(failures, record_id, "summary selected score samples do not match precision")

    row.update(
        precision_result=precision.get("result"),
        ate_translation_max_m=max_m,
        samples=samples,
        timestamp_overlap_ratio=overlap,
    )
    return row


def evaluate(
    manifest_path: str | Path,
    summary_path: str | Path,
    policy: str,
    experiment_root: str | Path | None = None,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    summary_path = Path(summary_path)
    root = Path(experiment_root) if experiment_root is not None else summary_path.parent
    result: dict[str, Any] = {
        "header": HEADER,
        "result": "FAIL",
        "dataset_count": 0,
        "policy": policy,
        "rows": [],
        "failures": [],
    }
    failures = result["failures"]
    try:
        manifest = read_json(manifest_path)
        summary = read_json(summary_path)
    except Exception as error:
        fail(failures, "GLOBAL", f"failed to read inputs: {type(error).__name__}: {error}")
        return result

    records = manifest.get("records", [])
    results = summary.get("results", [])
    result["dataset_count"] = len(records)
    if len(records) != REQUIRED_COUNT or summary.get("dataset_count") != REQUIRED_COUNT:
        fail(failures, "GLOBAL", "dataset_count must be exactly 25 in manifest and summary")
    ids = [record.get("id") for record in records]
    if len(ids) != len(set(ids)):
        fail(failures, "GLOBAL", "duplicate manifest record id")
    summary_ids = [item.get("id") for item in results]
    if len(summary_ids) != len(set(summary_ids)):
        fail(failures, "GLOBAL", "duplicate summary ID")
    if set(ids) != set(summary_ids):
        fail(failures, "GLOBAL", "manifest IDs and summary IDs differ; no omissions allowed")
    if summary.get("status") != "COMPLETED":
        fail(failures, "GLOBAL", "summary final status is not COMPLETED")

    summary_by_id = {item.get("id"): item for item in results}
    global_state: dict[str, Any] = {}
    for record in records:
        record_id = record.get("id", "<missing>")
        summary_result = summary_by_id.get(record_id, {})
        row = {"id": record_id, "status": "FAIL", "policy": policy}
        if summary_result.get("status") != "COMPLETED":
            fail(failures, record_id, "summary record status is not COMPLETED")
        if policy not in summary_result.get("variants", {}):
            fail(failures, record_id, f"summary selected policy missing: {policy}")
        try:
            artifact = artifact_dir_for(record, summary_result, policy, root)
            if not under_root(artifact, root):
                fail(failures, record_id, "artifact_dir is not under experiment_root")
            else:
                row = validate_score(record, summary_result, artifact, policy, failures, global_state)
        except Exception as error:
            fail(failures, record_id, f"evaluation error: {type(error).__name__}: {error}")
        result["rows"].append(row)

    failed_ids = {failure["id"] for failure in failures if failure["id"] != "GLOBAL"}
    for row in result["rows"]:
        if row["id"] in failed_ids:
            row["status"] = "FAIL"
    if not failures:
        result["result"] = "PASS"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--experiment-root", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = evaluate(args.manifest, args.summary, args.policy, args.experiment_root)
    except Exception as error:
        payload = {
            "header": HEADER,
            "result": "FAIL",
            "dataset_count": 0,
            "policy": args.policy,
            "rows": [],
            "failures": [{"id": "GLOBAL", "message": f"unexpected evaluator error: {type(error).__name__}: {error}"}],
        }
    print(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False))
    return 0 if payload.get("result") == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
