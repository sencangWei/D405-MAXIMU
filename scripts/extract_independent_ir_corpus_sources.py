#!/usr/bin/env python3
"""Extract native independent-IR corpus source reports.

Development-only source producer.  It refreshes accepted original-derived RIGHT
rows with the existing independent RIGHT native geometry path, byte-copies
unadmitted optional reports, and builds a symmetric rejected-row recovery
appendix.  It does not emit factors, launch a backend, score, use GT, or run a
frontend/GPU model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import diagnose_independent_right_stereo as diag  # noqa: E402
import prepare_independent_ir_corpus_source_probe as source_meta  # noqa: E402
import prepare_independent_ir_recovery_probe as recovery  # noqa: E402
import prepare_independent_right_geometry_probe as right_refresh  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402
from ego_vio.vio.cached_ir_correspondences import CachedIrSiftCorrespondences  # noqa: E402,F401


SCHEMA = "umi_independent_ir_corpus_native_sources_v1"
READY_STATUS = "INDEPENDENT_IR_CORPUS_NATIVE_SOURCES_READY"
FAIL_STATUS = "INDEPENDENT_IR_CORPUS_NATIVE_SOURCE_FAILED"
RETAINED_STATUS = source_meta.RETAINED_STATUS
OPTIONAL_STEREO_POLICY = source_meta.OPTIONAL_STEREO_POLICY


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return source_meta.file_hash(Path(path))


def path_entry(path: Path) -> dict[str, str]:
    resolved = Path(path).resolve()
    return {"path": str(resolved), "sha256": file_hash(resolved)}


def snapshot(paths: dict[str, Path]) -> dict[str, dict[str, str]]:
    return {label: path_entry(path) for label, path in paths.items()}


def assert_unchanged(before: dict[str, dict[str, str]]) -> None:
    changed = []
    for label, item in before.items():
        path = Path(item["path"])
        digest = file_hash(path)
        if digest != item["sha256"]:
            changed.append(f"{label}: {path}")
    if changed:
        raise ValueError(f"consumed source changed during extraction: {changed}")


def load_source_preflight(path: Path) -> dict[str, Any]:
    report_path = path / "preflight_report.json" if path.is_dir() else path
    report = read_json(report_path)
    if report.get("schema") != source_meta.SCHEMA:
        raise ValueError("source preflight schema mismatch")
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError("source preflight is not onboard-only")
    records = report.get("records")
    if not isinstance(records, list):
        raise ValueError("source preflight records missing")
    by_id: dict[str, dict[str, Any]] = {}
    for row in records:
        record_id = row.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in by_id:
            raise ValueError("source preflight missing/duplicate record id")
        by_id[record_id] = row
    return {"path": report_path.resolve(), "sha256": file_hash(report_path), "raw": report, "records": records, "by_id": by_id}


def _ordered_report_paths(eye_row: dict[str, Any], eye: str) -> list[Path]:
    reports = eye_row.get("source_reports")
    if not isinstance(reports, list):
        raise ValueError(f"{eye} source_reports missing")
    by_name = {Path(item["path"]).name: Path(item["path"]).resolve() for item in reports}
    missing = [name for name in physical.eye_report_names(eye) if name not in by_name]
    if missing:
        raise ValueError(f"{eye} report set missing expected reports: {missing}")
    return [by_name[name] for name in physical.eye_report_names(eye)]


def _reports_by_path(eye_row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(Path(item["path"]).resolve()): item for item in eye_row.get("source_reports", [])}


def _item_trajectory(item: dict[str, Any], eye: str) -> dict[str, str]:
    trajectory = item.get("trajectory")
    if isinstance(trajectory, dict):
        path = trajectory.get("path")
        sha = trajectory.get("sha256")
        role = trajectory.get("role", f"{eye}_raw_geometry_trajectory")
    else:
        path = item.get("trajectory_path")
        sha = item.get("trajectory_sha256")
        role = f"{eye}_raw_geometry_trajectory"
    if not path or not sha:
        raise ValueError(f"{eye} source report trajectory metadata missing: {item.get('path')}")
    return {"path": str(Path(path).resolve()), "sha256": str(sha), "role": str(role)}


def _times(path: Path) -> list[float]:
    return source_meta._load_times(path)


def _validate_eye_roles(row: dict[str, Any], eye: str) -> None:
    eye_row = row.get(eye)
    if not isinstance(eye_row, dict):
        raise ValueError(f"{eye} source metadata missing")
    raw = eye_row.get("raw_geometry_trajectory")
    metric = eye_row.get("metric_body_trajectory")
    if not isinstance(raw, dict) or not isinstance(metric, dict):
        raise ValueError(f"{eye} raw/metric trajectory metadata missing")
    raw_path = Path(raw["path"]).resolve()
    metric_path = Path(metric["path"]).resolve()
    if raw_path == metric_path:
        raise ValueError(f"{eye} raw and metric trajectories must be distinct")
    if file_hash(raw_path) != raw.get("sha256"):
        raise ValueError(f"{eye} raw trajectory sha256 mismatch")
    if file_hash(metric_path) != metric.get("sha256"):
        raise ValueError(f"{eye} metric trajectory sha256 mismatch")
    if _times(raw_path) != _times(metric_path):
        raise ValueError(f"{eye} raw and metric trajectories must share exact timeline")


def _validate_report_bindings(row: dict[str, Any], eye: str, paths: list[Path]) -> None:
    eye_row = row[eye]
    record = {"id": row["id"], "session": row["session"]}
    items = _reports_by_path(eye_row)
    left_paths = {str(Path(item["path"]).resolve()) for item in row["left"]["source_reports"]}
    raw_path = Path(eye_row["raw_geometry_trajectory"]["path"]).resolve()
    raw_sha = eye_row["raw_geometry_trajectory"]["sha256"]
    for path in paths:
        key = str(path.resolve())
        if key not in items:
            raise ValueError(f"{eye} source report missing metadata: {path}")
        item = items[key]
        if file_hash(path) != item.get("sha256"):
            raise ValueError(f"{eye} source report sha256 mismatch: {path}")
        report = read_json(path)
        if report.get("schema") != "umi_mast3r_stereo_scale_v2":
            raise ValueError(f"{eye} source report schema mismatch: {path}")
        if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
            raise ValueError(f"{eye} source report is not onboard-only: {path}")
        if report.get("observation_frame") != f"infrared_{eye}_camera_i":
            raise ValueError(f"{eye} source report frame mismatch: {path}")
        if Path(report.get("session", "")).resolve() != Path(row["session"]).resolve():
            raise ValueError(f"{eye} source report session mismatch: {path}")
        trajectory = Path(report.get("trajectory", "")).resolve()
        item_trajectory = _item_trajectory(item, eye)
        if trajectory != raw_path or item_trajectory["path"] != str(raw_path):
            raise ValueError(f"{eye} source report raw trajectory mismatch: {path}")
        if file_hash(trajectory) != raw_sha or item_trajectory["sha256"] != raw_sha:
            raise ValueError(f"{eye} source report raw trajectory sha256 mismatch: {path}")
        counts = source_meta._classify_observations(report, _times(raw_path), path)
        if counts["accepted_observation_count"] != item.get("accepted_observation_count"):
            raise ValueError(f"{eye} accepted observation count mismatch: {path}")
        if counts["rejected_observation_count"] != item.get("rejected_observation_count"):
            raise ValueError(f"{eye} rejected observation count mismatch: {path}")
        derived = report.get("derived_from_left_stereo_report")
        if eye == "left" and derived is not None:
            raise ValueError(f"LEFT report unexpectedly declares derived_from_left_stereo_report: {path}")
        if eye == "right":
            if not derived or str(Path(derived).resolve()) not in left_paths:
                raise ValueError(f"RIGHT report lineage mismatch: {path}")
    merged, normal_reports = recovery.load_normal_merged_reports(paths, eye, record)
    if eye_row.get("factory_stereo_calibration") is not None and merged.get("factory_stereo_calibration") != eye_row["factory_stereo_calibration"]:
        raise ValueError(f"{eye} factory calibration mismatch")
    admitted = {report["report_path"] for report in normal_reports}
    for path in paths:
        item = items[str(path.resolve())]
        normal = str(path.resolve()) in admitted
        expected_recovery = bool(normal and item.get("result") == "PASS")
        expected_refresh = bool(eye == "right" and normal and item.get("result") == "PASS")
        if item.get("admitted_for_rejected_recovery") is not expected_recovery:
            raise ValueError(f"{eye} rejected recovery admission mismatch: {path}")
        if item.get("admitted_for_accepted_refresh") is not expected_refresh:
            raise ValueError(f"{eye} accepted refresh admission mismatch: {path}")


def _registry_guard_paths(row: dict[str, Any]) -> dict[str, Path]:
    entry = row.get("source_registry") or {}
    path_value = entry.get("path")
    if not path_value:
        raise ValueError("source_registry path missing")
    registry_path = Path(path_value).resolve()
    if file_hash(registry_path) != entry.get("sha256"):
        raise ValueError("source_registry sha256 mismatch")
    paths = {"source_registry_preflight": registry_path}
    registry_doc = read_json(registry_path)
    matched = False
    for record in registry_doc.get("records", []):
        if record.get("id") != row.get("id"):
            continue
        matched = True
        files = record.get("baseline_adapter_v2", {}).get("files", {})
        for label, item in files.items():
            if isinstance(item, dict) and item.get("path"):
                file_path = Path(item["path"]).resolve()
                expected = item.get("sha256")
                if not expected:
                    raise ValueError(f"registry {label} sha256 missing")
                if file_hash(file_path) != expected:
                    raise ValueError(f"registry {label} sha256 mismatch: {file_path}")
                paths[f"registry_{label}"] = file_path
        candidate_hashes = record.get("baseline_candidate_input_sha256")
        if not isinstance(candidate_hashes, dict) or not candidate_hashes:
            raise ValueError("registry baseline_candidate_input_sha256 missing")
        d405_seen = False
        for source_path, expected in candidate_hashes.items():
            candidate_path = Path(source_path).resolve()
            if not candidate_path.is_file():
                raise ValueError(f"candidate input missing: {candidate_path}")
            if file_hash(candidate_path) != expected:
                raise ValueError(f"candidate input sha256 mismatch: {candidate_path}")
            if candidate_path.name == "d405_frames.csv":
                d405_seen = True
            paths[f"candidate_input_{len(paths):03d}_{candidate_path.name}"] = candidate_path
        if not d405_seen:
            raise ValueError("candidate input d405_frames.csv missing from guard")
        break
    if not matched:
        raise ValueError(f"source registry missing record id: {row.get('id')}")
    return paths


def _consumed_paths(source: dict[str, Any], row: dict[str, Any], left_paths: list[Path], right_paths: list[Path]) -> dict[str, Path]:
    consumed = {
        "source_preflight": source["path"],
        "this_script": Path(__file__).resolve(),
        "source_metadata_module": Path(source_meta.__file__).resolve(),
        "right_refresh_module": Path(right_refresh.__file__).resolve(),
        "recovery_module": Path(recovery.__file__).resolve(),
        "diagnostic_module": Path(diag.__file__).resolve(),
        "low_excitation_diagnostic_module": Path(diag.lowdiag.__file__).resolve(),
        "native_stereo_module": Path(diag.stereo.__file__).resolve(),
        "physical_helper_module": Path(physical.__file__).resolve(),
        "cache_module": ROOT / "ego_vio/vio/cached_ir_correspondences.py",
        "right_geometry_module": ROOT / "ego_vio/vio/right_stereo_motion.py",
    }
    consumed.update(_registry_guard_paths(row))
    for eye in ("left", "right"):
        eye_row = row[eye]
        consumed[f"{eye}_raw_trajectory"] = Path(eye_row["raw_geometry_trajectory"]["path"]).resolve()
        consumed[f"{eye}_metric_trajectory"] = Path(eye_row["metric_body_trajectory"]["path"]).resolve()
    for index, path in enumerate(left_paths):
        consumed[f"left_report_{index:02d}_{path.name}"] = path
    for index, path in enumerate(right_paths):
        consumed[f"right_report_{index:02d}_{path.name}"] = path
    first_left = read_json(left_paths[0])
    db3 = first_left.get("db3")
    if db3:
        consumed["db3"] = Path(db3).resolve()
    return consumed


def _strict_copy_report(source: Path, output: Path) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"right report output exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    if file_hash(source) != file_hash(output):
        raise ValueError(f"byte-copy sha mismatch: {source}")
    return {
        "source_report": str(source.resolve()),
        "refreshed_report": str(output.resolve()),
        "source_sha256": file_hash(source),
        "refreshed_sha256": file_hash(output),
        "byte_copied_unadmitted_optional": True,
        "accepted_input_rows": 0,
        "independent_replaced_rows": 0,
        "fallback_preserved_rows": 0,
        "rejected_input_rows_unchanged": len(read_json(source).get("observations", [])),
        "pairs_preserved": True,
        "global_scale_preserved": True,
    }


def _refresh_right_reports(record: dict[str, Any], row: dict[str, Any], left_paths: list[Path], right_paths: list[Path], output: Path) -> tuple[list[Path], dict[str, Any], dict[str, str]]:
    right_items = _reports_by_path(row["right"])
    admitted = [path for path in right_paths if right_items[str(path)].get("admitted_for_accepted_refresh") is True]
    out_dir = output / record["id"] / "right_sources"
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"right source output exists: {out_dir}")
    right_raw = Path(row["right"]["raw_geometry_trajectory"]["path"]).resolve()
    times, positions, quaternions, _ = diag.stereo.load_trajectory(right_raw)
    rotations = Rotation.from_quat(quaternions)
    selected = right_refresh.validate_accepted_rows_before_images(admitted, times) if admitted else set()
    left_report = read_json(left_paths[0])
    if selected:
        db3 = left_report.get("db3")
        if not db3:
            raise ValueError("LEFT report db3 missing for native RIGHT refresh")
        left_numbers, right_numbers, sync, calibration, left_images, right_images = diag.lowdiag._load_images(
            left_report, Path(record["session"]).resolve(), Path(db3).resolve(), "infrared_left", times, selected
        )
    else:
        left_numbers = right_numbers = np.asarray([], dtype=int)
        sync, calibration, left_images, right_images = {}, {}, {}, {}
    disparity_cache = right_refresh.DisparityCache()
    motion_cache: dict[tuple[int, int], dict[str, Any]] = {}
    reports: list[dict[str, Any]] = []
    outputs: list[Path] = []
    for path in right_paths:
        out_path = out_dir / path.name
        if path in admitted:
            reports.append(
                right_refresh.refresh_report(
                    report_path=path,
                    output_path=out_path,
                    record=record,
                    times=times,
                    positions=positions,
                    rotations=rotations,
                    left_numbers=left_numbers,
                    right_numbers=right_numbers,
                    left_images=left_images,
                    right_images=right_images,
                    calibration=calibration,
                    disparity_cache=disparity_cache,
                    motion_cache=motion_cache,
                )
            )
        else:
            reports.append(_strict_copy_report(path, out_path))
        outputs.append(out_path.resolve())
    source_override = {str(path.resolve()): file_hash(path) for path in left_paths}
    source_override.update({str(path.resolve()): file_hash(path) for path in outputs})
    proof = {
        "schema": "umi_independent_right_geometry_refresh_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "source_preflight": str(row["source_registry"]["path"]),
        "source_preflight_sha256": row["source_registry"]["sha256"],
        "reports": {
            item["refreshed_report"]: {
                "fallback_right_source_path": item["source_report"],
                "fallback_right_source_sha256": item["source_sha256"],
                "raw_right_geometry_trajectory": str(right_raw),
                "raw_right_geometry_trajectory_sha256": file_hash(right_raw),
                "refreshed_row_count": item["independent_replaced_rows"],
                "fallback_row_count": item["fallback_preserved_rows"],
                "byte_copied_unadmitted_optional": item.get("byte_copied_unadmitted_optional", False),
                "pairs_preserved": item["pairs_preserved"],
                "global_scale_preserved": item["global_scale_preserved"],
            }
            for item in reports
        },
        "report_path_map": {"right": [str(path) for path in outputs]},
        "accepted_input_rows": sum(item["accepted_input_rows"] for item in reports),
        "independent_replaced_rows": sum(item["independent_replaced_rows"] for item in reports),
        "fallback_preserved_rows": sum(item["fallback_preserved_rows"] for item in reports),
        "optional_unadmitted_copied_count": sum(1 for item in reports if item.get("byte_copied_unadmitted_optional")),
    }
    proof["synchronization"] = sync
    return outputs, proof, source_override


def _write_right_refresh_record_stage(
    *,
    record: dict[str, Any],
    source: dict[str, Any],
    left_paths: list[Path],
    right_outputs: list[Path],
    source_override: dict[str, str],
    right_refresh_proof: dict[str, Any],
    output: Path,
) -> tuple[str, str]:
    path = output / record["id"] / "right_refresh_source_stage.json"
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"right refresh record stage exists: {path}")
    stage = {
        "schema": "umi_independent_ir_corpus_right_refresh_record_v1",
        "development_only": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "backend_launched": False,
        "scoring_launched": False,
        "gpu_model_used": False,
        "factors_emitted": 0,
        "id": record["id"],
        "session": str(Path(record["session"]).resolve()),
        "source_preflight": str(source["path"]),
        "source_preflight_sha256": source["sha256"],
        "left_source_paths": [str(path.resolve()) for path in left_paths],
        "right_source_paths": [str(path.resolve()) for path in right_outputs],
        "source_override_sha256": dict(source_override),
        "right_refresh_proof": right_refresh_proof,
    }
    write_json(path, stage)
    return str(path.resolve()), file_hash(path)


def _filtered_preflight_eye(eye: str, record: dict[str, Any], paths: list[Path]) -> tuple[tuple[Any, ...], dict[str, int]]:
    merged, normal_reports = recovery.load_normal_merged_reports(paths, eye, record)
    trajectory = Path(merged["trajectory"]).resolve()
    times, positions, quaternions, _ = diag.stereo.load_trajectory(trajectory)
    rows = []
    invalid_rejected = 0
    invalid_reasons: dict[str, int] = {}
    for report in normal_reports:
        report_path = Path(report["report_path"]).resolve()
        for index, obs in enumerate(report.get("observations", [])):
            if obs.get("accepted") is True:
                right_refresh.accepted_row_indices(obs, times, report_path)
                continue
            if obs.get("accepted") is not False:
                continue
            if obs.get("reason") == recovery.LOW_EXCITATION_REASON:
                continue
            try:
                first, second = right_refresh.accepted_row_indices(obs, times, report_path)
            except ValueError as error:
                invalid_rejected += 1
                reason = f"{type(error).__name__}: {error}"
                invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
                continue
            rows.append({"report": report, "path": report_path, "index": index, "row": obs, "first": first, "second": second})
    path_by_report = {report["report_path"]: Path(report["report_path"]) for report in normal_reports}
    return (merged, normal_reports, rows, times, positions, quaternions, path_by_report), {
        "eligible_rejected_non_low_rows": len(rows),
        "invalid_rejected_diagnostic_rows_excluded": invalid_rejected,
        "invalid_rejected_diagnostic_reasons": invalid_reasons,
    }


def _empty_eye_context(eye: str, paths: list[Path], preflight: tuple[Any, ...]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Path]]:
    merged = preflight[0]
    trajectory = Path(merged["trajectory"]).resolve()
    context = {
        "reference_scale": float(merged["scale_m_per_mast3r_unit"]),
        "reference_trajectory_path": str(trajectory),
        "reference_trajectory_sha256": file_hash(trajectory),
        "merged_report_paths": [str(Path(path).resolve()) for path in merged.get("merged_report_paths", [])],
        "report_sha256": {str(path.resolve()): file_hash(path) for path in paths},
        "synchronization": None,
    }
    consumed = {f"{eye}_report_{index}_{path.name}": path for index, path in enumerate(paths)}
    consumed[f"{eye}_trajectory"] = trajectory
    return context, [], consumed


def _validate_consumed_subset(eye: str, consumed: dict[str, Path], guarded_paths: set[str]) -> None:
    missing = sorted(str(Path(path).resolve()) for path in consumed.values() if str(Path(path).resolve()) not in guarded_paths)
    if missing:
        raise ValueError(f"{eye} consumed paths missing from source guard: {missing}")


def _build_recovery_appendix(
    record: dict[str, Any],
    source: dict[str, Any],
    left_paths: list[Path],
    right_paths: list[Path],
    output: Path,
    *,
    guard_before: dict[str, dict[str, str]],
    generated_right_sources: dict[str, str],
    right_refresh_stage_path: str,
    right_refresh_stage_sha256: str,
) -> tuple[dict[str, Any], str, str]:
    left_preflight, left_diag = _filtered_preflight_eye("left", record, left_paths)
    right_preflight, right_diag = _filtered_preflight_eye("right", record, right_paths)
    left_report = read_json(left_paths[0])
    if left_preflight[2]:
        left_context, left_observations, left_consumed = recovery.build_eye_appendix(
            eye="left", record=record, paths=left_paths, left_report_for_images=left_report, preflight=left_preflight
        )
    else:
        left_context, left_observations, left_consumed = _empty_eye_context("left", left_paths, left_preflight)
    if right_preflight[2]:
        right_context, right_observations, right_consumed = recovery.build_eye_appendix(
            eye="right", record=record, paths=right_paths, left_report_for_images=left_report, preflight=right_preflight
        )
    else:
        right_context, right_observations, right_consumed = _empty_eye_context("right", right_paths, right_preflight)
    guarded_paths = {item["path"] for item in guard_before.values()} | set(generated_right_sources)
    _validate_consumed_subset("left", left_consumed, guarded_paths)
    _validate_consumed_subset("right", right_consumed, guarded_paths)
    observations = left_observations + right_observations
    appendix = {
        "schema": recovery.APPENDIX_SCHEMA,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "id": record["id"],
        "session": str(Path(record["session"]).resolve()),
        "native_source_schema": SCHEMA,
        "source_stage_preflight": right_refresh_stage_path,
        "source_stage_preflight_sha256": right_refresh_stage_sha256,
        "source_preflight": str(source["path"]),
        "source_preflight_sha256": source["sha256"],
        "eye_contexts": {"left": left_context, "right": right_context},
        "observations": observations,
        "native_observation_count": len(observations),
        "native_accepted_count": sum(1 for obs in observations if obs.get("native_observation", {}).get("accepted") is True),
        "invalid_rejected_diagnostic_rows_excluded": (
            left_diag["invalid_rejected_diagnostic_rows_excluded"] + right_diag["invalid_rejected_diagnostic_rows_excluded"]
        ),
        "eligible_rejected_non_low_rows": (
            left_diag["eligible_rejected_non_low_rows"] + right_diag["eligible_rejected_non_low_rows"]
        ),
        "eye_diagnostics": {"left": left_diag, "right": right_diag},
        "consumed_source_guard": {
            "schema": "independent_ir_corpus_native_recovery_guard_v1",
            "guarded_path_count": len(guard_before),
            "guarded_before_sha256": guard_before,
            "generated_right_source_sha256": generated_right_sources,
            "generated_parent_source_stage_sha256": {right_refresh_stage_path: right_refresh_stage_sha256},
            "guarded_after_verified": True,
        },
    }
    out_path = output / record["id"] / "independent_ir_recovery_appendix.json"
    if out_path.exists() or out_path.is_symlink():
        raise FileExistsError(f"recovery appendix output exists: {out_path}")
    write_json(out_path, appendix)
    return appendix, str(out_path.resolve()), file_hash(out_path)


def prepare_record(row: dict[str, Any], source: dict[str, Any], output: Path) -> dict[str, Any]:
    record_id = row["id"]
    if row.get("status") == RETAINED_STATUS:
        return {
            "id": record_id,
            "session": row.get("session"),
            "status": RETAINED_STATUS,
            "denominator_retained": True,
            "ready_for_native_sources": False,
            "reason": row.get("reason"),
            "factors_emitted": 0,
        }
    if row.get("status") != source_meta.READY_STATUS or row.get("ready_for_image_extract") is not True:
        raise ValueError(f"source preflight record is not ready for extraction: {record_id}")
    record = {"id": record_id, "session": row["session"]}
    _validate_eye_roles(row, "left")
    _validate_eye_roles(row, "right")
    left_paths = _ordered_report_paths(row["left"], "left")
    right_paths = _ordered_report_paths(row["right"], "right")
    _validate_report_bindings(row, "left", left_paths)
    _validate_report_bindings(row, "right", right_paths)
    consumed = _consumed_paths(source, row, left_paths, right_paths)
    before = snapshot(consumed)
    right_outputs, right_proof, override = _refresh_right_reports(record, row, left_paths, right_paths, output)
    generated_right_sources = {str(path.resolve()): file_hash(path) for path in right_outputs}
    parent_stage_path, parent_stage_sha = _write_right_refresh_record_stage(
        record=record,
        source=source,
        left_paths=left_paths,
        right_outputs=right_outputs,
        source_override=override,
        right_refresh_proof=right_proof,
        output=output,
    )
    appendix, appendix_path, appendix_sha = _build_recovery_appendix(
        record,
        source,
        left_paths,
        right_outputs,
        output,
        guard_before=before,
        generated_right_sources=generated_right_sources,
        right_refresh_stage_path=parent_stage_path,
        right_refresh_stage_sha256=parent_stage_sha,
    )
    assert_unchanged(before)
    return {
        "id": record_id,
        "session": row["session"],
        "status": READY_STATUS,
        "denominator_retained": True,
        "ready_for_consumer": True,
        "source_lineage": "original_baseline_corpus_native_sources",
        "source_preflight": {"path": str(source["path"]), "sha256": source["sha256"]},
        "left_source_paths": [str(path.resolve()) for path in left_paths],
        "right_source_paths": [str(path.resolve()) for path in right_outputs],
        "source_override_sha256": override,
        "right_refresh_source_stage_path": parent_stage_path,
        "right_refresh_source_stage_sha256": parent_stage_sha,
        "right_refresh_proof": right_proof,
        "recovery_appendix_path": appendix_path,
        "recovery_appendix_sha256": appendix_sha,
        "recovery_native_observation_count": appendix["native_observation_count"],
        "recovery_native_accepted_count": appendix["native_accepted_count"],
        "invalid_rejected_diagnostic_rows_excluded": appendix["invalid_rejected_diagnostic_rows_excluded"],
        "raw_metric_binding": {
            eye: {
                "raw_geometry_trajectory": row[eye]["raw_geometry_trajectory"],
                "metric_body_trajectory": row[eye]["metric_body_trajectory"],
                "exact_timeline_verified": True,
            }
            for eye in ("left", "right")
        },
        "consumed_source_guard": {
            "schema": "independent_ir_corpus_native_source_guard_v1",
            "guarded_path_count": len(before),
            "guarded_before_sha256": before,
            "guarded_after_verified": True,
        },
        "baseline_input_sha256_preserved": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "gpu_model_used": False,
        "factors_emitted": 0,
        "backend_launched": False,
        "scoring_launched": False,
    }


def build_native_sources(source_preflight: Path, output: Path, datasets: list[str] | None = None) -> dict[str, Any]:
    source = load_source_preflight(source_preflight)
    wanted = set(datasets or [])
    unknown = sorted(wanted - set(source["by_id"]))
    if unknown:
        raise ValueError(f"source preflight missing requested datasets: {unknown}")
    selected = [row for row in source["records"] if not wanted or row["id"] in wanted]
    records = []
    for row in selected:
        started = time.monotonic()
        try:
            result = prepare_record(row, source, output)
        except Exception as error:  # noqa: BLE001 - retain per-record technical failures.
            result = {
                "id": row.get("id"),
                "session": row.get("session"),
                "status": FAIL_STATUS,
                "denominator_retained": True,
                "ready_for_consumer": False,
                "stage": "extract_native_sources",
                "error": f"{type(error).__name__}: {error}",
                "factors_emitted": 0,
                "backend_launched": False,
                "scoring_launched": False,
            }
        records.append(result)
        progress = {
            "schema": "umi_independent_ir_corpus_native_sources_progress_v1",
            "processed_record_count": len(records),
            "last_record_id": row.get("id"),
            "last_status": result["status"],
            "last_elapsed_sec": round(time.monotonic() - started, 3),
            "ready_record_count": sum(1 for item in records if item["status"] == READY_STATUS),
            "failure_count": sum(1 for item in records if item["status"] == FAIL_STATUS),
        }
        write_json(output / "progress.json", progress)
        print(json.dumps(progress, ensure_ascii=False, sort_keys=True), flush=True)
    failures = [row for row in records if row["status"] == FAIL_STATUS]
    ready = [row for row in records if row["status"] == READY_STATUS]
    retained = [row for row in records if row["status"] == RETAINED_STATUS]
    return {
        "schema": SCHEMA,
        "status": "NATIVE_SOURCES_WITH_FAILURES" if failures else "NATIVE_SOURCES_COMPLETE",
        "development_only": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "backend_launched": False,
        "scoring_launched": False,
        "gpu_model_used": False,
        "factors_emitted": 0,
        "source_preflight": str(source["path"]),
        "source_preflight_sha256": source["sha256"],
        "source_policy": {
            "right_refresh": "accepted original-derived RIGHT rows only; unadmitted optional reports byte-copied",
            "recovery": "symmetric rejected non-low-excitation rows with invalid rejected diagnostics excluded",
            "optional_stereo_policy": OPTIONAL_STEREO_POLICY,
            "no_refined_lineage_alias": True,
        },
        "record_count": len(records),
        "ready_record_count": len(ready),
        "retained_unobservable_count": len(retained),
        "failure_count": len(failures),
        "records": records,
        "by_id": {str(row["id"]): index for index, row in enumerate(records)},
        "failures": failures,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output must be new: {args.output}")
    report = build_native_sources(args.source_preflight, args.output, args.dataset)
    write_json(args.output / "preflight_report.json", report)
    return report


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-preflight", type=Path, required=True)
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
    return 0 if report["status"] == "NATIVE_SOURCES_COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
