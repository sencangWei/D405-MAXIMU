#!/usr/bin/env python3
"""Build a source-only registry for the 25-record dual-IR corpus.

The registry is deliberately not a source producer.  It binds the current
unrefined adapter-v2 source reports and downstream frozen artifacts so a later
bridge can decide what to refresh without pretending the four-record pilot
stages cover the whole corpus.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fuse_mast3r_stereo_imu as fusion  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402


SCHEMA = "umi_independent_ir_corpus_registry_v1"
OPTIONAL_STEREO_POLICY = "reject_window"
BASELINE_VARIANT = "both"
CONSTANT_VARIANT = "selected"
COMBINED_VARIANT = "physical_stereo_constant_gauge"


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.mkdir(parents=True, exist_ok=False)
    (path / "preflight_report.json").write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path_entry(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": file_hash(path)}


def _load_times(path: Path) -> list[float]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if "t_sec" not in (reader.fieldnames or []):
            raise ValueError(f"trajectory missing t_sec: {path}")
        times = [float(row["t_sec"]) for row in reader]
    if not times:
        raise ValueError(f"trajectory has no rows: {path}")
    if any(not math.isfinite(time) for time in times):
        raise ValueError(f"trajectory has non-finite timestamps: {path}")
    return times


def _strict_index(value: Any, label: str, report_path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} is not an exact integer in {report_path}")
    return value


def _validate_observation_times(report: dict[str, Any], report_path: Path, times: list[float]) -> dict[str, Any]:
    accepted = 0
    rejected = 0
    invalid_rejected: list[dict[str, Any]] = []
    for row_index, row in enumerate(report.get("observations", [])):
        if row.get("accepted") is True:
            accepted += 1
        else:
            rejected += 1
            continue
        for index_key, time_key in (("first_index", "first_t_sec"), ("second_index", "second_t_sec")):
            if index_key not in row:
                raise ValueError(f"accepted row missing {index_key} in {report_path}")
            if time_key not in row:
                raise ValueError(f"accepted row missing {time_key} in {report_path}")
            index = _strict_index(row[index_key], index_key, report_path)
            if index < 0 or index >= len(times):
                raise ValueError(f"{index_key} out of trajectory range in {report_path}")
            observed = float(row[time_key])
            if not math.isfinite(observed):
                raise ValueError(f"accepted row has non-finite {time_key} in {report_path}")
            if abs(times[index] - observed) > 0.010:
                raise ValueError(f"timestamp mismatch for {time_key} in {report_path}")
    for row_index, row in enumerate(report.get("observations", [])):
        if row.get("accepted") is True:
            continue
        for index_key, time_key in (("first_index", "first_t_sec"), ("second_index", "second_t_sec")):
            if index_key not in row or time_key not in row:
                continue
            try:
                index = _strict_index(row[index_key], index_key, report_path)
                observed = float(row[time_key])
                if index < 0 or index >= len(times) or not math.isfinite(observed) or abs(times[index] - observed) > 0.010:
                    invalid_rejected.append(
                        {
                            "observation_index": row_index,
                            "first_index": row.get("first_index"),
                            "second_index": row.get("second_index"),
                            "first_t_sec": row.get("first_t_sec"),
                            "second_t_sec": row.get("second_t_sec"),
                            "accepted": row.get("accepted"),
                            "reason": row.get("reason"),
                            "invalid_reason": "index_time_binding_mismatch",
                        }
                    )
                    break
            except (TypeError, ValueError):
                invalid_rejected.append(
                    {
                        "observation_index": row_index,
                        "first_index": row.get("first_index"),
                        "second_index": row.get("second_index"),
                        "first_t_sec": row.get("first_t_sec"),
                        "second_t_sec": row.get("second_t_sec"),
                        "accepted": row.get("accepted"),
                        "reason": row.get("reason"),
                        "invalid_reason": "malformed_index_or_time",
                    }
                )
                break
    return {
        "observation_count": len(report.get("observations", [])),
        "accepted_observation_count": accepted,
        "rejected_observation_count": rejected,
        "invalid_rejected_observation_count": len(invalid_rejected),
        "invalid_rejected_observations": invalid_rejected,
    }


def _candidate_input_hash(candidate: dict[str, Any], path: Path) -> str:
    key = str(path.resolve())
    hashes = candidate.get("input_sha256", {})
    if key not in hashes:
        raise ValueError(f"candidate input_sha256 missing source path: {key}")
    actual = file_hash(path)
    if hashes[key] != actual:
        raise ValueError(f"candidate input_sha256 mismatch for {key}")
    return actual


def _hash_with_optional_candidate_binding(candidate: dict[str, Any], path: Path) -> dict[str, Any]:
    actual = file_hash(path)
    key = str(path.resolve())
    declared = candidate.get("input_sha256", {}).get(key)
    if declared is not None and declared != actual:
        raise ValueError(f"candidate input_sha256 mismatch for {key}")
    return {"path": key, "sha256": actual, "candidate_input_sha256_bound": declared is not None}


def _report_paths(candidate: dict[str, Any], eye: str) -> list[Path]:
    names = set(physical.eye_report_names(eye))
    by_name: dict[str, Path] = {}
    for source in candidate.get("input_sha256", {}):
        path = Path(source).resolve()
        if path.name not in names:
            continue
        if path.name in by_name:
            raise ValueError(f"duplicate {eye} report basename: {path.name}")
        by_name[path.name] = path
    missing = [name for name in physical.eye_report_names(eye) if name not in by_name]
    if missing:
        raise ValueError(f"{eye} report paths missing: {missing}")
    ordered = [by_name[name] for name in physical.eye_report_names(eye)]
    if len({str(path) for path in ordered}) != len(ordered):
        raise ValueError(f"duplicate {eye} report path")
    return ordered


def _metric_trajectory_entries(candidate: dict[str, Any], eye: str) -> list[dict[str, Any]]:
    path = physical.eye_trajectory_path_from_baseline(candidate, eye).resolve()
    return [_hash_with_optional_candidate_binding(candidate, path)]


def _summarize_eye(candidate: dict[str, Any], record: dict[str, Any], eye: str) -> dict[str, Any]:
    paths = _report_paths(candidate, eye)
    eye_metadata = physical.validate_eye_metadata(candidate, eye)
    reports: list[dict[str, Any]] = []
    trajectory_paths: set[str] = set()
    source_reports = []
    for path in paths:
        expected_hash = _candidate_input_hash(candidate, path)
        report = read_json(path)
        if report.get("schema") != "umi_mast3r_stereo_scale_v2":
            raise ValueError(f"unexpected stereo report schema in {path}")
        if report.get("session") != candidate.get("session") or report.get("session") != record.get("session"):
            raise ValueError(f"stereo report session mismatch in {path}")
        expected_frame = f"infrared_{eye}_camera_i"
        if report.get("observation_frame") != expected_frame:
            raise ValueError(f"stereo report frame mismatch in {path}")
        if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
            raise ValueError(f"stereo report is not source-only/onboard: {path}")
        trajectory = Path(report["trajectory"]).resolve()
        trajectory_entry = _hash_with_optional_candidate_binding(candidate, trajectory)
        times = _load_times(trajectory)
        counts = _validate_observation_times(report, path, times)
        trajectory_paths.add(str(trajectory))
        reports.append({**report, "report_path": str(path)})
        source_reports.append(
            {
                "path": str(path),
                "sha256": expected_hash,
                "schema": report.get("schema"),
                "result": report.get("result"),
                "normal_source_admitted": False,
                "trajectory_path": str(trajectory),
                "trajectory_sha256": trajectory_entry["sha256"],
                "trajectory_candidate_input_sha256_bound": trajectory_entry["candidate_input_sha256_bound"],
                "scale_m_per_mast3r_unit": report.get("scale_m_per_mast3r_unit"),
                **counts,
            }
        )
    merged = fusion.merge_stereo_reports(reports[0], reports[1:], optional_policy=OPTIONAL_STEREO_POLICY)
    admitted = {str(path) for path in merged.get("merged_report_paths", [])}
    for item in source_reports:
        item["normal_source_admitted"] = item["path"] in admitted
    rejected = merged.get("optional_report_rejections", [])
    derived = [report.get("derived_from_left_stereo_report") for report in reports if report.get("derived_from_left_stereo_report")]
    trajectory_path = next(iter(trajectory_paths)) if len(trajectory_paths) == 1 else None
    return {
        "source_reports": source_reports,
        "reports": source_reports,
        f"source_reports_{eye}": [item["path"] for item in source_reports],
        "report_count": len(source_reports),
        "normal_pass_report_count": sum(1 for item in source_reports if item["normal_source_admitted"]),
        "optional_rejected_report_count": len(rejected),
        "optional_report_rejections": rejected,
        "trajectory_path": trajectory_path,
        "trajectory_sha256": file_hash(Path(trajectory_path)) if trajectory_path else None,
        "raw_frontend_timeline": _hash_with_optional_candidate_binding(candidate, Path(trajectory_path)) if trajectory_path else None,
        "trajectory_role": f"{eye}_report_bound_raw_frontend_timeline",
        "metric_trajectories": _metric_trajectory_entries(candidate, eye),
        "trajectory_paths": sorted(trajectory_paths),
        "lineage": {
            "source_context": "current_unrefined_baseline_adapter_v2",
            "metric_relabel_forbidden": True,
            "derived_from_left_stereo_report_count": len(derived),
            "derived_from_left_stereo_report_paths": sorted(set(derived)),
        },
        "eye_metadata": eye_metadata,
    }


def _artifact_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, key in (("local_motion_factors.json", "local_motion_factor_count"), ("shared_stereo_observations.json", "shared_stereo_observation_count")):
        data = read_json(path / name)
        rows = data.get("factors", data.get("observations", [])) if isinstance(data, dict) else data
        counts[key] = len(rows) if isinstance(rows, list) else 0
    return counts


def _require_onboard(report: dict[str, Any], label: str) -> None:
    if report.get("external_ground_truth_used") is not False:
        raise ValueError(f"{label} used ground truth")
    if report.get("slam_supervision") is not False:
        raise ValueError(f"{label} used slam supervision")


def _expected_graph_schema(schema: str) -> str:
    if schema == "umi_dual_ir_symmetric_experiment_v1":
        return "umi_dual_ir_symmetric_graph_diagnostic_v1"
    if schema == "umi_constant_ir_gauge_candidate_v1":
        return "umi_constant_ir_gauge_graph_diagnostic_v1"
    if schema == "umi_physical_stereo_lever_candidate_v1":
        return "umi_physical_stereo_lever_graph_diagnostic_v1"
    raise ValueError(f"unknown artifact schema: {schema}")


def _artifact(path: Path, schema: str, variant: str, session: str) -> dict[str, Any]:
    if not path.is_dir():
        raise ValueError(f"artifact directory missing: {path}")
    files = {
        "candidate_manifest": path / "candidate_manifest.json",
        "graph_report": path / "graph_report.json",
        "local_motion_factors": path / "local_motion_factors.json",
        "shared_stereo_observations": path / "shared_stereo_observations.json",
        "body_trajectory_fused": path / "body_trajectory_fused.csv",
    }
    candidate = read_json(files["candidate_manifest"])
    graph = read_json(files["graph_report"])
    if candidate.get("schema") != schema:
        raise ValueError(f"candidate schema mismatch in {files['candidate_manifest']}")
    if candidate.get("session") != session:
        raise ValueError(f"candidate session mismatch in {files['candidate_manifest']}")
    _require_onboard(candidate, str(files["candidate_manifest"]))
    _require_onboard(graph, str(files["graph_report"]))
    if graph.get("schema") != _expected_graph_schema(schema):
        raise ValueError(f"graph schema mismatch in {files['graph_report']}")
    if graph.get("output_frame") != "body_imu_origin":
        raise ValueError(f"graph output_frame mismatch in {files['graph_report']}")
    policy = candidate.get("policy_arguments", {})
    if policy.get("optional_stereo_policy") != OPTIONAL_STEREO_POLICY:
        raise ValueError(f"optional_stereo_policy mismatch in {files['candidate_manifest']}")
    if policy.get("eyes") != "both":
        raise ValueError(f"baseline eyes policy mismatch in {files['candidate_manifest']}")
    if schema == "umi_dual_ir_symmetric_experiment_v1":
        if policy.get("source_policy") not in (None, "both"):
            raise ValueError(f"baseline source_policy mismatch in {files['candidate_manifest']}")
    elif policy.get("source_policy") != "both":
        raise ValueError(f"source_policy mismatch in {files['candidate_manifest']}")
    if variant and policy.get("variant") not in (variant, None):
        raise ValueError(f"variant mismatch in {files['candidate_manifest']}")
    if schema in {"umi_constant_ir_gauge_candidate_v1", "umi_physical_stereo_lever_candidate_v1"} and candidate.get("output_estimate_sha256") is None:
        raise ValueError(f"missing output_estimate_sha256 in {files['candidate_manifest']}")
    if candidate.get("output_estimate_sha256") is not None:
        estimate_hash = file_hash(files["body_trajectory_fused"])
        if candidate["output_estimate_sha256"] != estimate_hash:
            raise ValueError(f"output_estimate_sha256 mismatch in {files['candidate_manifest']}")
    if candidate.get("output_motion_factors_sha256") is not None:
        motion_hash = file_hash(files["local_motion_factors"])
        if candidate["output_motion_factors_sha256"] != motion_hash:
            raise ValueError(f"output_motion_factors_sha256 mismatch in {files['candidate_manifest']}")
    return {
        "path": str(path.resolve()),
        "schema": schema,
        "variant": variant,
        "files": {label: _path_entry(file_path) for label, file_path in files.items()},
        **_artifact_counts(path),
    }


def _progress_reason(record: dict[str, Any], baseline: Path) -> str | None:
    progress = baseline / record["id"] / "progress.json"
    if progress.exists():
        data = read_json(progress)
        return data.get("error") or data.get("status")
    return None


def _manifest_unobservable_reason(record: dict[str, Any]) -> str | None:
    audit = record.get("recovery_audit", {})
    text = json.dumps(audit, ensure_ascii=False).lower()
    fields = " ".join(str(record.get(key, "")) for key in ("left_status", "right_status", "capture_status")).lower()
    if any(token in text or token in fields for token in ("unobservable", "0 accepted", "partial_missing_imu_scale", "missing")):
        return "manifest_audit_unobservable_or_missing_source"
    return None


def _unobservable_row(record: dict[str, Any], baseline: Path, constant: Path, combined: Path, reason: str) -> dict[str, Any]:
    return {
        "id": record["id"],
        "session": record.get("session"),
        "status": "RETAINED_UNOBSERVABLE_UNSCORED",
        "denominator_retained": True,
        "ready_for_source_refresh": False,
        "reason": reason,
        "missing_artifacts": {
            "baseline_both": str((baseline / record["id"] / BASELINE_VARIANT).resolve()),
            "constant_gauge_selected": str((constant / record["id"] / CONSTANT_VARIANT).resolve()),
            "physical_stereo_constant_gauge": str((combined / record["id"] / COMBINED_VARIANT).resolve()),
        },
    }


def build_record(record: dict[str, Any], baseline: Path, constant: Path, combined: Path) -> dict[str, Any]:
    base_artifact = baseline / record["id"] / BASELINE_VARIANT
    if not base_artifact.exists():
        reason = _progress_reason(record, baseline) or _manifest_unobservable_reason(record)
        if reason:
            return _unobservable_row(record, baseline, constant, combined, reason)
        raise ValueError(f"baseline artifact missing: {base_artifact}")
    candidate_path = base_artifact / "candidate_manifest.json"
    candidate = read_json(candidate_path)
    if candidate.get("schema") != "umi_dual_ir_symmetric_experiment_v1":
        raise ValueError(f"baseline candidate schema mismatch: {candidate_path}")
    if candidate.get("session") != record.get("session"):
        raise ValueError(f"baseline candidate session mismatch: {candidate_path}")
    policy = candidate.get("policy_arguments", {})
    if policy.get("optional_stereo_policy") != OPTIONAL_STEREO_POLICY:
        raise ValueError(f"baseline optional_stereo_policy mismatch: {candidate_path}")
    left = _summarize_eye(candidate, record, "left")
    right = _summarize_eye(candidate, record, "right")
    return {
        "id": record["id"],
        "session": record.get("session"),
        "status": "BASELINE_SOURCE_REGISTRY_READY",
        "denominator_retained": True,
        "ready_for_source_refresh": True,
        "source_context": "current_unrefined_baseline_adapter_v2",
        "not_sift_lm_refined_source": True,
        "not_independent_right_geometry_source": True,
        "optional_stereo_policy": OPTIONAL_STEREO_POLICY,
        "baseline_adapter_v2": _artifact(base_artifact, "umi_dual_ir_symmetric_experiment_v1", "", record["session"]),
        "constant_gauge_selected": _artifact(
            constant / record["id"] / CONSTANT_VARIANT,
            "umi_constant_ir_gauge_candidate_v1",
            CONSTANT_VARIANT,
            record["session"],
        ),
        "physical_stereo_constant_gauge": _artifact(
            combined / record["id"] / COMBINED_VARIANT,
            "umi_physical_stereo_lever_candidate_v1",
            COMBINED_VARIANT,
            record["session"],
        ),
        "baseline_candidate_input_sha256": candidate.get("input_sha256", {}),
        "eye_reports_present": sorted(candidate.get("eye_reports", {}).keys()),
        "left": left,
        "right": right,
        "source_reports_left": left["source_reports_left"],
        "source_reports_right": right["source_reports_right"],
    }


def build_registry(manifest_path: Path, baseline: Path, constant: Path, combined: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    records = manifest.get("records", [])
    if not isinstance(records, list):
        raise ValueError("manifest records missing")
    ids = []
    for index, record in enumerate(records):
        record_id = record.get("id")
        if not record_id:
            raise ValueError(f"manifest record missing id at index {index}")
        ids.append(record_id)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate manifest record id")
    output_records = []
    for record in records:
        try:
            output_records.append(build_record(record, baseline, constant, combined))
        except Exception as exc:  # noqa: BLE001 - per-record preflight must retain failures.
            output_records.append(
                {
                    "id": record.get("id"),
                    "session": record.get("session"),
                    "status": "ERROR",
                    "denominator_retained": True,
                    "ready_for_source_refresh": False,
                    "error": str(exc),
                }
            )
    errors = [row for row in output_records if row["status"] == "ERROR"]
    ready = [row for row in output_records if row["status"] == "BASELINE_SOURCE_REGISTRY_READY"]
    retained = [row for row in output_records if row["status"] == "RETAINED_UNOBSERVABLE_UNSCORED"]
    return {
        "schema": SCHEMA,
        "status": "PREFLIGHT_WITH_RECORD_ERRORS" if errors else "PREFLIGHT_COMPLETE",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "backend_launched": False,
        "scoring_launched": False,
        "gpu_model_used": False,
        "record_count": len(output_records),
        "ready_record_count": len(ready),
        "retained_unobservable_count": len(retained),
        "error_record_count": len(errors),
        "manifest": _path_entry(manifest_path),
        "baseline_root": str(baseline.resolve()),
        "constant_gauge_root": str(constant.resolve()),
        "combined_reference_root": str(combined.resolve()),
        "policy": {
            "schema_role": "honest_current_baseline_source_registry",
            "optional_stereo_policy": OPTIONAL_STEREO_POLICY,
            "source_stage_scope": "all_manifest_records_current_unrefined_baseline_context",
            "four_record_pilot_sources_not_assumed": True,
            "unobservable_records_retain_denominator": True,
        },
        "records": output_records,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--constant-gauge", required=True, type=Path)
    parser.add_argument("--combined-reference", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output already exists or is a symlink: {args.output}")
    report = build_registry(
        args.manifest.resolve(),
        args.baseline.resolve(),
        args.constant_gauge.resolve(),
        args.combined_reference.resolve(),
    )
    write_json(args.output, report)
    print(
        json.dumps(
            {
                "output": str((args.output / "preflight_report.json").resolve()),
                "status": report["status"],
                "record_count": report["record_count"],
                "ready_record_count": report["ready_record_count"],
                "retained_unobservable_count": report["retained_unobservable_count"],
                "error_record_count": report["error_record_count"],
            },
            sort_keys=True,
        )
    )
    return 3 if report["error_record_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
