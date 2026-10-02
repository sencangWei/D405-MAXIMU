#!/usr/bin/env python3
"""Development-only original-baseline independent-IR corpus source preflight.

This producer is intentionally metadata-only.  It binds the typed all-corpus
registry to the original LEFT reports and original derived RIGHT reports, then
declares the two source-only follow-up operations that are allowed later:

* refresh accepted RIGHT rows with the existing independent RIGHT native-geometry
  path, preserving rejected rows and report-level scale/confidence semantics;
* recover only originally rejected non-low-excitation rows symmetrically.

It does not extract images, run MASt3R/GPU, emit factors, run a backend, or
score.  Its output is a source contract for review before the expensive image
stage.
"""

from __future__ import annotations

import argparse
import csv
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_independent_ir_corpus_registry as registry  # noqa: E402
import prepare_independent_ir_recovery_probe as recovery  # noqa: E402
import prepare_independent_right_geometry_probe as right_refresh  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402


SCHEMA = "umi_independent_ir_corpus_source_preflight_v1"
READY_STATUS = "INDEPENDENT_IR_CORPUS_SOURCE_PREFLIGHT_READY"
FAIL_STATUS = "SOURCE_PREFLIGHT_FAILED"
RETAINED_STATUS = "RETAINED_UNOBSERVABLE_UNSCORED"
OPTIONAL_STEREO_POLICY = "reject_window"


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return registry.file_hash(Path(path))


def path_entry(path: Path) -> dict[str, str]:
    resolved = Path(path).resolve()
    return {"path": str(resolved), "sha256": file_hash(resolved)}


def load_registry(path: Path) -> dict[str, Any]:
    report_path = path / "preflight_report.json"
    report = read_json(report_path)
    if report.get("schema") != registry.SCHEMA:
        raise ValueError("registry schema mismatch")
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError("registry is not onboard-only")
    rows = report.get("records")
    if not isinstance(rows, list):
        raise ValueError("registry records missing")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        record_id = row.get("id")
        if not record_id or record_id in by_id:
            raise ValueError("registry has missing/duplicate record id")
        by_id[str(record_id)] = row
    return {
        "path": report_path.resolve(),
        "sha256": file_hash(report_path),
        "raw": report,
        "records": rows,
        "records_by_id": by_id,
    }


def _require_hash(path: Path, expected: str, label: str) -> str:
    digest = file_hash(path)
    if digest != expected:
        raise ValueError(f"{label} sha256 mismatch: {path}")
    return digest


def _load_times(path: Path) -> list[float]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if "t_sec" not in (reader.fieldnames or []):
            raise ValueError(f"trajectory missing t_sec: {path}")
        times = [float(row["t_sec"]) for row in reader]
    if not times or any(not np.isfinite(time) for time in times):
        raise ValueError(f"trajectory has invalid timestamps: {path}")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError(f"trajectory timestamps are not strictly increasing: {path}")
    return times


def _validate_row_time(row: dict[str, Any], times: list[float], path: Path) -> None:
    for index_key, time_key in (("first_index", "first_t_sec"), ("second_index", "second_t_sec")):
        if index_key not in row or time_key not in row:
            raise ValueError(f"row missing {index_key}/{time_key} in {path}")
        index = row[index_key]
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(f"{index_key} must be an exact integer in {path}")
        if index < 0 or index >= len(times):
            raise ValueError(f"{index_key} out of trajectory range in {path}")
        value = float(row[time_key])
        if not np.isfinite(value):
            raise ValueError(f"{time_key} non-finite in {path}")
        if abs(times[index] - value) > 0.010:
            raise ValueError(f"{time_key} timestamp mismatch in {path}")


def _candidate_manifest(row: dict[str, Any]) -> dict[str, Any]:
    candidate_path = Path(row["baseline_adapter_v2"]["files"]["candidate_manifest"]["path"])
    candidate = read_json(candidate_path)
    if candidate.get("schema") != "umi_dual_ir_symmetric_experiment_v1":
        raise ValueError("baseline candidate schema mismatch")
    if candidate.get("session") != row.get("session"):
        raise ValueError("baseline candidate session mismatch")
    if candidate.get("external_ground_truth_used") is not False:
        raise ValueError("baseline candidate used external ground truth")
    if candidate.get("slam_supervision") is not False:
        raise ValueError("baseline candidate used SLAM supervision")
    if candidate.get("policy_arguments", {}).get("optional_stereo_policy") != OPTIONAL_STEREO_POLICY:
        raise ValueError("baseline candidate optional_stereo_policy mismatch")
    input_hashes = candidate.get("input_sha256")
    if not isinstance(input_hashes, dict) or not input_hashes:
        raise ValueError("baseline candidate input_sha256 missing")
    if dict(input_hashes) != dict(row.get("baseline_candidate_input_sha256", {})):
        raise ValueError("registry baseline_candidate_input_sha256 mismatch")
    _require_hash(candidate_path, row["baseline_adapter_v2"]["files"]["candidate_manifest"]["sha256"], "baseline candidate")
    return candidate


def _input_hash(candidate: dict[str, Any], path: Path, label: str) -> str:
    key = str(path.resolve())
    expected = candidate.get("input_sha256", {}).get(key)
    if expected is None:
        raise ValueError(f"{label} not bound in baseline candidate input_sha256: {key}")
    return _require_hash(path, expected, label)


def _metric_trajectory(candidate: dict[str, Any], eye: str) -> dict[str, str]:
    path = physical.eye_trajectory_path_from_baseline(candidate, eye).resolve()
    digest = _input_hash(candidate, path, f"{eye} metric trajectory")
    return {"path": str(path), "sha256": digest, "role": f"{eye}_body_metric_trajectory"}


def _registry_metric_trajectory(eye_record: dict[str, Any], expected: dict[str, str], eye: str) -> dict[str, str]:
    entries = eye_record.get("metric_trajectories")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError(f"{eye} registry metric_trajectories must contain exactly one entry")
    entry = entries[0]
    path = str(Path(entry.get("path", "")).resolve())
    if path != expected["path"] or entry.get("sha256") != expected["sha256"]:
        raise ValueError(f"{eye} registry metric trajectory does not match baseline candidate binding")
    if entry.get("candidate_input_sha256_bound") is not True:
        raise ValueError(f"{eye} metric trajectory must be candidate-bound")
    return expected


def _classify_observations(report: dict[str, Any], times: list[float], path: Path) -> dict[str, Any]:
    accepted = rejected = low_excitation = rejected_non_low = invalid_rejected = 0
    invalid_reasons: dict[str, int] = {}
    for index, obs in enumerate(report.get("observations", [])):
        if obs.get("accepted") is True:
            _validate_row_time(obs, times, path)
            accepted += 1
            continue
        rejected += 1
        try:
            _validate_row_time(obs, times, path)
        except ValueError as error:
            invalid_rejected += 1
            reason = f"{type(error).__name__}: {error}"
            invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
            continue
        if obs.get("reason") == recovery.LOW_EXCITATION_REASON:
            low_excitation += 1
        else:
            rejected_non_low += 1
    return {
        "accepted_observation_count": accepted,
        "rejected_observation_count": rejected,
        "rejected_non_low_excitation_count": rejected_non_low,
        "low_excitation_rejected_count": low_excitation,
        "invalid_rejected_diagnostic_count": invalid_rejected,
        "invalid_rejected_diagnostic_reasons": invalid_reasons,
    }


def _validate_report_item(
    *,
    record: dict[str, Any],
    candidate: dict[str, Any],
    eye: str,
    item: dict[str, Any],
    left_paths: set[str],
) -> dict[str, Any]:
    path = Path(item["path"]).resolve()
    _require_hash(path, item["sha256"], f"{eye} source report")
    _input_hash(candidate, path, f"{eye} source report")
    report = read_json(path)
    if report.get("schema") != "umi_mast3r_stereo_scale_v2":
        raise ValueError(f"{eye} source report schema mismatch: {path}")
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError(f"{eye} source report is not onboard-only: {path}")
    if report.get("result") not in ("PASS", "FAIL"):
        raise ValueError(f"{eye} source report result unsupported: {path}")
    if report.get("observation_frame") != f"infrared_{eye}_camera_i":
        raise ValueError(f"{eye} source report frame mismatch: {path}")
    if Path(report.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError(f"{eye} source report session mismatch: {path}")
    trajectory = Path(report.get("trajectory", "")).resolve()
    if trajectory != Path(item["trajectory_path"]).resolve():
        raise ValueError(f"{eye} source report trajectory mismatch: {path}")
    _require_hash(trajectory, item["trajectory_sha256"], f"{eye} raw trajectory")
    times = _load_times(trajectory)
    counts = _classify_observations(report, times, path)
    if (
        counts["accepted_observation_count"] != item.get("accepted_observation_count")
        or counts["rejected_observation_count"] != item.get("rejected_observation_count")
    ):
        raise ValueError(f"{eye} source report observation count mismatch: {path}")
    derived = report.get("derived_from_left_stereo_report")
    if eye == "left" and derived is not None:
        raise ValueError(f"LEFT source report unexpectedly derived from another report: {path}")
    if eye == "right":
        if not derived:
            raise ValueError(f"RIGHT source report lacks derived_from_left_stereo_report: {path}")
        if str(Path(derived).resolve()) not in left_paths:
            raise ValueError(f"RIGHT source report derived_from_left_stereo_report is not a bound LEFT report: {path}")
    return {
        "path": str(path),
        "sha256": item["sha256"],
        "result": report["result"],
        "report": {**report, "report_path": str(path)},
        "trajectory": {"path": str(trajectory), "sha256": item["trajectory_sha256"], "role": f"{eye}_raw_geometry_trajectory"},
        **counts,
        "derived_from_left_stereo_report": str(Path(derived).resolve()) if derived else None,
    }


def _prepare_eye(record: dict[str, Any], candidate: dict[str, Any], eye: str, left_paths: set[str]) -> dict[str, Any]:
    eye_record = record.get(eye)
    if not isinstance(eye_record, dict):
        raise ValueError(f"registry missing {eye} eye record")
    metadata = physical.validate_eye_metadata(candidate, eye)
    raw_path = Path(eye_record["trajectory_path"]).resolve()
    raw_entry = eye_record.get("raw_frontend_timeline")
    if not isinstance(raw_entry, dict):
        raise ValueError(f"{eye} raw_frontend_timeline missing from registry")
    if str(Path(raw_entry.get("path", "")).resolve()) != str(raw_path):
        raise ValueError(f"{eye} raw_frontend_timeline path mismatch")
    raw_hashes = {item["trajectory_sha256"] for item in eye_record.get("source_reports", [])}
    if len(raw_hashes) != 1:
        raise ValueError(f"{eye} reports do not share one raw trajectory hash")
    raw_digest = _require_hash(raw_path, raw_entry["sha256"], f"{eye} raw trajectory")
    if raw_digest != next(iter(raw_hashes)):
        raise ValueError(f"{eye} raw trajectory registry/report hash mismatch")
    raw_times = _load_times(raw_path)
    metric = _registry_metric_trajectory(eye_record, _metric_trajectory(candidate, eye), eye)
    metric_path = Path(metric["path"])
    if metric_path.resolve() == raw_path:
        raise ValueError(f"{eye} raw and metric trajectories must be distinct paths")
    metric_times = _load_times(metric_path)
    if raw_times != metric_times:
        raise ValueError(f"{eye} raw and metric trajectories must share the exact timeline")
    reports = [
        _validate_report_item(record=record, candidate=candidate, eye=eye, item=deepcopy(item), left_paths=left_paths)
        for item in eye_record.get("source_reports", [])
    ]
    if not reports:
        raise ValueError(f"{eye} source reports missing")
    merged = registry.fusion.merge_stereo_reports(
        reports[0]["report"],
        [item["report"] for item in reports[1:]],
        optional_policy=OPTIONAL_STEREO_POLICY,
    )
    if merged.get("factory_stereo_calibration") != metadata["factory_stereo_calibration"]:
        raise ValueError(f"{eye} factory stereo calibration metadata mismatch")
    admitted_paths = {str(Path(path).resolve()) for path in merged.get("merged_report_paths", [])}
    optional_rejections = deepcopy(merged.get("optional_report_rejections", []))
    optional_rejected = len(optional_rejections)
    if optional_rejected != int(eye_record.get("optional_rejected_report_count", 0)):
        raise ValueError(f"{eye} optional rejected report count mismatch")
    for item in reports:
        normal = item["path"] in admitted_paths
        item["normal_source_admitted"] = normal
        item["admitted_for_rejected_recovery"] = bool(normal and item["result"] == "PASS")
        item["admitted_for_accepted_refresh"] = bool(eye == "right" and normal and item["result"] == "PASS")
        item.pop("report")
    admitted = [item for item in reports if item["normal_source_admitted"]]
    accepted_rows = sum(item["accepted_observation_count"] for item in admitted)
    rejected_non_low = sum(item["rejected_non_low_excitation_count"] for item in admitted)
    low = sum(item["low_excitation_rejected_count"] for item in admitted)
    invalid_rejected = sum(item["invalid_rejected_diagnostic_count"] for item in admitted)
    return {
        "lineage": {
            "actual_source": (
                "original_baseline_left_reports" if eye == "left" else "original_baseline_derived_right_reports"
            ),
            "not_sift_lm_refined_source": True,
            "not_refined_lineage_alias": True,
        },
        "source_reports": reports,
        "source_report_paths": [item["path"] for item in reports],
        "normal_pass_report_count": int(eye_record.get("normal_pass_report_count", len(admitted))),
        "optional_rejected_report_count": optional_rejected,
        "optional_report_rejections": optional_rejections,
        "factory_stereo_calibration": metadata["factory_stereo_calibration"],
        "effective_body_T_camera": metadata["effective_body_T_camera"],
        "raw_geometry_trajectory": {"path": str(raw_path), "sha256": raw_digest, "role": f"{eye}_raw_geometry_trajectory"},
        "metric_body_trajectory": metric,
        "admitted_report_count": len(admitted),
        "accepted_rows_in_admitted_reports": accepted_rows,
        "rejected_non_low_excitation_rows_in_admitted_reports": rejected_non_low,
        "low_excitation_rejected_rows_skipped": low,
        "invalid_rejected_diagnostic_rows_excluded": invalid_rejected,
    }


def _report_path_map(left: dict[str, Any], right: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "left": [item["path"] for item in left["source_reports"]],
        "right": [item["path"] for item in right["source_reports"]],
    }


def prepare_record(registry_record: dict[str, Any], *, registry_entry: dict[str, str] | None = None) -> dict[str, Any]:
    source = deepcopy(registry_record)
    record_id = source.get("id")
    if source.get("status") == RETAINED_STATUS:
        return {
            "id": record_id,
            "session": source.get("session"),
            "status": RETAINED_STATUS,
            "denominator_retained": True,
            "ready_for_image_extract": False,
            "reason": source.get("reason"),
            "factors_emitted": 0,
        }
    if source.get("status") != "BASELINE_SOURCE_REGISTRY_READY" or source.get("ready_for_source_refresh") is not True:
        raise ValueError(f"record is not ready in corpus registry: {record_id}")
    if source.get("not_sift_lm_refined_source") is not True or source.get("not_independent_right_geometry_source") is not True:
        raise ValueError("registry record is not the original baseline source context")
    candidate = _candidate_manifest(source)
    left_paths = {str(Path(path).resolve()) for path in source.get("source_reports_left", [])}
    left = _prepare_eye(source, candidate, "left", left_paths)
    right = _prepare_eye(source, candidate, "right", left_paths)
    report_map = _report_path_map(left, right)
    return {
        "id": record_id,
        "session": source.get("session"),
        "status": READY_STATUS,
        "denominator_retained": True,
        "ready_for_image_extract": True,
        "source_registry": deepcopy(registry_entry or {}),
        "source_lineage": "original_baseline_corpus_sources",
        "optional_stereo_policy": OPTIONAL_STEREO_POLICY,
        "left": left,
        "right": {
            **right,
            "accepted_native_geometry_refresh": {
                "policy": "reuse_existing_independent_right_accepted_row_native_geometry_refresh",
                "source_rows": "accepted rows in admitted original derived RIGHT reports only",
                "output_reports_written": False,
                "accepted_input_rows": right["accepted_rows_in_admitted_reports"],
                "rejected_rows_preserved": (
                    right["rejected_non_low_excitation_rows_in_admitted_reports"]
                    + right["low_excitation_rejected_rows_skipped"]
                ),
            },
        },
        "right_refresh_proof": {
            "status": "PLANNED_NOT_RUN_METADATA_PREFLIGHT_ONLY",
            "path": None,
            "sha256": None,
            "report_path_map": {"right": report_map["right"]},
            "expected_policy": "reuse_existing_independent_right_accepted_row_native_geometry_refresh",
        },
        "recovery_appendix": {
            "status": "PLANNED_NOT_RUN_METADATA_PREFLIGHT_ONLY",
            "path": None,
            "sha256": None,
            "report_path_map": report_map,
            "expected_policy": "unchanged_symmetric_rejected_non_low_excitation_recovery",
        },
        "rejected_recovery": {
            "policy": "unchanged_symmetric_rejected_non_low_excitation_recovery",
            "low_excitation_reason_excluded": recovery.LOW_EXCITATION_REASON,
            "output_appendix_written": False,
            "left_candidate_rows": left["rejected_non_low_excitation_rows_in_admitted_reports"],
            "right_candidate_rows": right["rejected_non_low_excitation_rows_in_admitted_reports"],
            "low_excitation_rows_skipped": (
                left["low_excitation_rejected_rows_skipped"] + right["low_excitation_rejected_rows_skipped"]
            ),
            "invalid_rejected_diagnostic_rows_excluded": (
                left["invalid_rejected_diagnostic_rows_excluded"]
                + right["invalid_rejected_diagnostic_rows_excluded"]
            ),
        },
        "source_override_sha256": {},
        "baseline_input_sha256_preserved": True,
        "factors_emitted": 0,
        "backend_launched": False,
        "scoring_launched": False,
        "gpu_model_used": False,
    }


def build_source_preflight(registry_stage: Path, datasets: list[str] | None = None) -> dict[str, Any]:
    loaded = load_registry(registry_stage)
    wanted = set(datasets or [])
    unknown = sorted(wanted - set(loaded["records_by_id"]))
    if unknown:
        raise ValueError(f"registry missing requested datasets: {unknown}")
    selected = [row for row in loaded["records"] if not wanted or row.get("id") in wanted]
    records = []
    for row in selected:
        try:
            records.append(prepare_record(row, registry_entry={"path": str(loaded["path"]), "sha256": loaded["sha256"]}))
        except Exception as error:  # noqa: BLE001 - retain all per-record failures.
            records.append(
                {
                    "id": row.get("id"),
                    "session": row.get("session"),
                    "status": FAIL_STATUS,
                    "denominator_retained": True,
                    "ready_for_image_extract": False,
                    "error": f"{type(error).__name__}: {error}",
                    "factors_emitted": 0,
                }
            )
    failures = [row for row in records if row["status"] == FAIL_STATUS]
    ready = [row for row in records if row["status"] == READY_STATUS]
    retained = [row for row in records if row["status"] == RETAINED_STATUS]
    return {
        "schema": SCHEMA,
        "status": "PREFLIGHT_WITH_FAILURES" if failures else "PREFLIGHT_COMPLETE",
        "development_only": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "backend_launched": False,
        "scoring_launched": False,
        "gpu_model_used": False,
        "factors_emitted": 0,
        "source_stage_role": "original_baseline_corpus_source_preflight_metadata_only",
        "registry": str(loaded["path"]),
        "registry_sha256": loaded["sha256"],
        "record_count": len(records),
        "ready_record_count": len(ready),
        "retained_unobservable_count": len(retained),
        "failure_count": len(failures),
        "policy": {
            "right_accepted_refresh": "existing independent RIGHT accepted-row native geometry refresh",
            "rejected_recovery": "existing symmetric recovery for rejected non-low-excitation rows",
            "no_sift_lm_refined_lineage_claim": True,
            "no_backend_or_scoring": True,
            "one_physical_factor_per_pair_later": True,
        },
        "records": records,
        "failures": failures,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output must be new: {args.output}")
    report = build_source_preflight(args.registry, args.dataset)
    write_json(args.output / "preflight_report.json", report)
    return report


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    report = run(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "record_count": report["record_count"],
                "ready_record_count": report["ready_record_count"],
                "failure_count": report["failure_count"],
                "output": str((args.output / "preflight_report.json").resolve()),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "PREFLIGHT_COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
