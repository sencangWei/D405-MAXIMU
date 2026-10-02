#!/usr/bin/env python3
"""Prepare source-only RIGHT geometry refresh reports from independent RIGHT pixels.

This writes a new source-stage style preflight directory.  It does not run a
frontend, backend, scorer, GPU model, selector, or factor builder.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import diagnose_independent_right_stereo as diag  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402


SCHEMA = "umi_independent_right_geometry_refresh_preflight_v1"
POLICY = {
    "name": "independent_right_geometry_refresh_source_only",
    "development_only": True,
    "external_ground_truth_used": False,
    "slam_supervision": False,
    "scope": "recompute currently accepted RIGHT source rows from actual RIGHT pixels; keep fallback row on native reject",
    "limitation": "fixed-pair geometry refresh only; no missing-pair recovery, no factor generation, no backend/scorer",
    "bidirectional_gate": "native align_mast3r_scale_with_stereo.combine_bidirectional_scale default thresholds",
    "global_scale_policy": "report-level scale/quality preserved from corrected-derived fallback for confidence normalization only; not a new RIGHT scale claim",
}

STRUCTURAL_ROW_KEYS = ("accepted", "first_index", "second_index", "first_t_sec", "second_t_sec", "sample_hop")


def read_json(path: Path) -> Any:
    return diag.read_json(path)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return diag.file_sha256(path)


def snapshot(paths: dict[str, Path]) -> dict[str, dict[str, str]]:
    return diag.snapshot(paths)


def assert_unchanged(before: dict[str, dict[str, str]]) -> None:
    changed = diag.compare_snapshot(before, snapshot({label: Path(item["path"]) for label, item in before.items()}))
    if changed:
        raise ValueError("; ".join(changed))


def _by_name(paths: list[str], names: list[str], label: str) -> list[Path]:
    mapping = {Path(path).name: Path(path).resolve() for path in paths}
    missing = [name for name in names if name not in mapping]
    if missing:
        raise ValueError(f"{label} missing reports: {missing}")
    return [mapping[name] for name in names]


def stage_right_paths(stage_row: dict[str, Any]) -> list[Path]:
    paths = stage_row.get("refined_right_sources")
    if not isinstance(paths, list) or not paths:
        raise ValueError("source stage refined_right_sources missing")
    return _by_name(paths, physical.eye_report_names("right"), "refined RIGHT fallback")


def stage_left_paths(stage_row: dict[str, Any]) -> list[Path]:
    paths = stage_row.get("refined_left_sources")
    if not isinstance(paths, list) or not paths:
        raise ValueError("source stage refined_left_sources missing")
    return _by_name(paths, physical.eye_report_names("left"), "refined LEFT fixed")


def validate_override_hashes(stage_row: dict[str, Any], paths: list[Path]) -> dict[str, str]:
    overrides = stage_row.get("source_override_sha256")
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError("source stage source_override_sha256 missing")
    out: dict[str, str] = {}
    for path in paths:
        resolved = str(path.resolve())
        digest = file_hash(path)
        if overrides.get(resolved) != digest:
            raise ValueError(f"source_override_sha256 mismatch: {resolved}")
        out[resolved] = digest
    return out


def validate_report_identity(report: dict[str, Any], path: Path, record: dict[str, Any]) -> None:
    diag.validate_report(report, path, record, "infrared_right_camera_i")
    if report.get("result") not in (None, "PASS", "FAIL"):
        raise ValueError(f"unexpected report result in {path}")
    if not isinstance(report.get("observations"), list):
        raise ValueError(f"report observations missing: {path}")


def strict_index(value: Any, label: str, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{label} must be an exact integer in {path}")
    return int(value)


def accepted_row_indices(row: dict[str, Any], times: np.ndarray, path: Path) -> tuple[int, int]:
    indices = []
    for key, time_key in (("first_index", "first_t_sec"), ("second_index", "second_t_sec")):
        index = strict_index(row.get(key), key, path)
        if index < 0 or index >= len(times):
            raise ValueError(f"row index out of range in {path}")
        if time_key not in row:
            raise ValueError(f"row missing {time_key} in {path}")
        observed = float(row[time_key])
        if not np.isfinite(observed):
            raise ValueError(f"row non-finite {time_key} in {path}")
        if abs(float(times[index]) - observed) > 0.010:
            raise ValueError(f"row timestamp mismatch in {path}")
        indices.append(index)
    return indices[0], indices[1]


def validate_accepted_rows_before_images(report_paths: list[Path], times: np.ndarray) -> set[int]:
    selected: set[int] = set()
    for path in report_paths:
        report = read_json(path)
        validate_report_identity(report, path, {"session": report.get("session")})
        for row in report["observations"]:
            if row.get("accepted") is True:
                first, second = accepted_row_indices(row, times, path)
                selected.update((first, second))
    return selected


def result_fields(result: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "accepted",
        "reason",
        "method",
        "tracked_points",
        "pnp_inliers",
        "pnp_inlier_ratio",
        "pnp_refined",
        "pnp_reprojection_median_px",
        "pnp_reprojection_p95_px",
        "metric_distance_m",
        "metric_displacement_camera_i_m",
        "metric_displacement_frame",
        "direction_cosine",
        "rotation_error_deg",
        "pnp_rotation_quaternion_xyzw",
        "scale",
        "scale_estimator",
        "forward_scale",
        "reverse_scale",
        "bidirectional_relative_disagreement",
        "reverse_metric_distance_m",
        "reverse_pnp_inlier_ratio",
        "reverse_rotation_error_deg",
        "right_centric_motion_source",
    )
    return {key: result[key] for key in keep if key in result}


def structural_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in STRUCTURAL_ROW_KEYS if key in row}


class DisparityCache:
    def __init__(self, max_entries: int = 64):
        self.max_entries = max_entries
        self._order: list[int] = []
        self._cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def get(self, index: int, left_image: np.ndarray, right_image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if index in self._cache:
            return self._cache[index]
        value = diag.stereo.stereo_disparity(left_image, right_image, diag.NUM_DISPARITIES)
        self._cache[index] = value
        self._order.append(index)
        while len(self._order) > self.max_entries:
            old = self._order.pop(0)
            self._cache.pop(old, None)
        return value


def independent_right_result(
    row: dict[str, Any],
    *,
    left_numbers: np.ndarray,
    right_numbers: np.ndarray,
    left_images: dict[int, np.ndarray],
    right_images: dict[int, np.ndarray],
    positions: np.ndarray,
    rotations: Rotation,
    calibration: dict[str, Any],
    disparity_cache: DisparityCache,
    motion_cache: dict[tuple[int, int], dict[str, Any]],
) -> dict[str, Any]:
    first, second = strict_index(row["first_index"], "first_index", Path("<validated-row>")), strict_index(
        row["second_index"], "second_index", Path("<validated-row>")
    )
    cache_key = (first, second)
    if cache_key in motion_cache:
        return json.loads(json.dumps(motion_cache[cache_key], allow_nan=False))
    left_i, right_i = left_images[int(left_numbers[first])], right_images[int(right_numbers[first])]
    left_j, right_j = left_images[int(left_numbers[second])], right_images[int(right_numbers[second])]
    disp_l_i, disp_r_i = disparity_cache.get(first, left_i, right_i)
    disp_l_j, disp_r_j = disparity_cache.get(second, left_j, right_j)
    forward_pair = diag.Pair(first, second, "accepted_right_fallback_source", row)
    reverse_pair = diag.Pair(second, first, "accepted_right_fallback_source", row)
    forward = diag.estimate_right_motion(right_i, right_j, disp_l_i, disp_r_i, positions, rotations, forward_pair, calibration)
    reverse = diag.estimate_right_motion(right_j, right_i, disp_l_j, disp_r_j, positions, rotations, reverse_pair, calibration)
    combined = diag.combine_bidirectional_native(forward, reverse)
    combined["raw_forward_summary"] = diag._result_summary(forward)
    combined["raw_reverse_summary"] = diag._result_summary(reverse)
    motion_cache[cache_key] = json.loads(json.dumps(combined, allow_nan=False))
    return json.loads(json.dumps(combined, allow_nan=False))


def refresh_report(
    *,
    report_path: Path,
    output_path: Path,
    record: dict[str, Any],
    times: np.ndarray,
    positions: np.ndarray,
    rotations: Rotation,
    left_numbers: np.ndarray,
    right_numbers: np.ndarray,
    left_images: dict[int, np.ndarray],
    right_images: dict[int, np.ndarray],
    calibration: dict[str, Any],
    disparity_cache: DisparityCache,
    motion_cache: dict[tuple[int, int], dict[str, Any]],
) -> dict[str, Any]:
    report = read_json(report_path)
    validate_report_identity(report, report_path, record)
    updated = dict(report)
    updated_observations = []
    replaced = fallback = accepted_input = rejected_input = 0
    fallback_reasons: dict[str, int] = {}
    for row in report["observations"]:
        if row.get("accepted") is not True:
            rejected_input += 1
            updated_observations.append(dict(row))
            continue
        accepted_input += 1
        accepted_row_indices(row, times, report_path)
        native = independent_right_result(
            row,
            left_numbers=left_numbers,
            right_numbers=right_numbers,
            left_images=left_images,
            right_images=right_images,
            positions=positions,
            rotations=rotations,
            calibration=calibration,
            disparity_cache=disparity_cache,
            motion_cache=motion_cache,
        )
        if native.get("accepted") is True:
            out_row = structural_row(row)
            out_row.update(result_fields(native))
            out_row.update(
                independent_right_geometry_update=True,
                independent_right_geometry_update_status="native_bidirectional_accepted",
                fallback_left_derived_geometry_used=False,
                raw_forward_summary=native["raw_forward_summary"],
                raw_reverse_summary=native["raw_reverse_summary"],
            )
            replaced += 1
        else:
            out_row = dict(row)
            reason = str(native.get("reason", "unknown"))
            fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
            out_row.update(
                independent_right_geometry_update=False,
                independent_right_geometry_update_status="native_rejected_fallback_preserved",
                independent_right_geometry_failure_reason=reason,
                fallback_left_derived_geometry_used=True,
            )
            fallback += 1
        updated_observations.append(out_row)
    updated.pop("derived_from_left_stereo_report", None)
    updated.update(
        observations=updated_observations,
        geometry_refresh_policy={
            **POLICY,
            "fallback_right_source_path": str(report_path.resolve()),
            "fallback_right_source_sha256": file_hash(report_path),
        },
        independent_right_geometry_source_report=str(report_path.resolve()),
        independent_right_geometry_source_sha256=file_hash(report_path),
        independent_right_geometry_update_counts={
            "accepted_input_rows": accepted_input,
            "rejected_input_rows_unchanged": rejected_input,
            "independent_replaced_rows": replaced,
            "fallback_preserved_rows": fallback,
            "native_reject_reasons": fallback_reasons,
        },
        output=str(output_path.resolve()),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, updated)
    return {
        "source_report": str(report_path.resolve()),
        "refreshed_report": str(output_path.resolve()),
        "source_sha256": file_hash(report_path),
        "refreshed_sha256": file_hash(output_path),
        "pairs_preserved": True,
        "global_scale_preserved": True,
        **updated["independent_right_geometry_update_counts"],
    }


def prepare_record(record: dict[str, Any], stage: dict[str, Any], baseline: Path, output: Path, manifest: Path) -> dict[str, Any]:
    record_id = record["id"]
    stage_row = stage["by_id"].get(record_id)
    if stage_row is None:
        raise ValueError(f"source stage missing record: {record_id}")
    left_path, _primary_right, left_report, _left_traj, right_traj, db3, consumed = diag._record_sources(record, stage_row, baseline)
    left_paths = stage_left_paths(stage_row)
    right_paths = stage_right_paths(stage_row)
    override_left = validate_override_hashes(stage_row, left_paths)
    validate_override_hashes(stage_row, right_paths)
    consumed.update(
        {
            "manifest": manifest,
            "this_script": Path(__file__).resolve(),
            "source_stage_preflight": stage["path"],
            "right_helper_module": ROOT / "ego_vio/vio/right_stereo_motion.py",
        }
    )
    for index, path in enumerate([*left_paths, *right_paths]):
        consumed[f"stage_source_{index:02d}_{path.name}"] = path
    before = snapshot(consumed)
    times, positions, quaternions, _ = diag.stereo.load_trajectory(right_traj)
    rotations = Rotation.from_quat(quaternions)
    selected = validate_accepted_rows_before_images(right_paths, times)
    left_numbers, right_numbers, sync, calibration, left_images, right_images = diag.lowdiag._load_images(
        left_report, Path(record["session"]).resolve(), db3, "infrared_left", times, selected
    )
    out_dir = output / record_id / "refined_right_sources"
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"refined RIGHT output exists: {out_dir}")
    disparity_cache = DisparityCache()
    motion_cache: dict[tuple[int, int], dict[str, Any]] = {}
    reports = [
        refresh_report(
            report_path=path,
            output_path=out_dir / path.name,
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
        for path in right_paths
    ]
    assert_unchanged(before)
    refreshed = [item["refreshed_report"] for item in reports]
    source_override = {
        **override_left,
        **{item["refreshed_report"]: item["refreshed_sha256"] for item in reports},
    }
    refresh_proof = {
        "schema": "umi_independent_right_geometry_refresh_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "source_stage_preflight": str(stage["path"]),
        "source_stage_preflight_sha256": stage["sha256"],
        "reports": {
            item["refreshed_report"]: {
                "fallback_right_source_path": item["source_report"],
                "fallback_right_source_sha256": item["source_sha256"],
                "raw_right_geometry_trajectory": str(right_traj.resolve()),
                "raw_right_geometry_trajectory_sha256": file_hash(right_traj),
                "refreshed_row_count": item["independent_replaced_rows"],
                "fallback_row_count": item["fallback_preserved_rows"],
                "pairs_preserved": item["pairs_preserved"],
                "global_scale_preserved": item["global_scale_preserved"],
            }
            for item in reports
        },
    }
    return {
        "id": record_id,
        "status": "INDEPENDENT_RIGHT_GEOMETRY_REFRESH_READY",
        "baseline_artifact": str((baseline / record_id / "both").resolve()),
        "refined_left_sources": [str(path.resolve()) for path in left_paths],
        "refined_right_sources": refreshed,
        "source_override_sha256": source_override,
        "independent_right_geometry_refresh": refresh_proof,
        "right_raw_geometry_trajectory": str(right_traj.resolve()),
        "right_raw_geometry_trajectory_sha256": file_hash(right_traj),
        "right_geometry_refresh_policy": POLICY,
        "synchronization": sync,
        "reports": reports,
        "accepted_input_rows": sum(item["accepted_input_rows"] for item in reports),
        "independent_replaced_rows": sum(item["independent_replaced_rows"] for item in reports),
        "fallback_preserved_rows": sum(item["fallback_preserved_rows"] for item in reports),
        "rejected_input_rows_unchanged": sum(item["rejected_input_rows_unchanged"] for item in reports),
        "consumed_source_guard": {
            "schema": "independent_right_geometry_refresh_guard_v1",
            "guarded_path_count": len(before),
            "guarded_before_sha256": before,
            "guarded_after_verified": True,
        },
        "factor_output_count": 0,
        "accepted_as_candidate": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output must be new: {args.output}")
    records = diag.load_manifest_records(args.manifest, args.dataset)
    stage = diag.load_consistent_stage(args.source_stage)
    results, failures = [], []
    for record in records:
        try:
            row = prepare_record(record, stage, args.baseline, args.output, args.manifest)
            results.append(row)
            print(
                json.dumps(
                    {
                        "id": row["id"],
                        "replaced": row["independent_replaced_rows"],
                        "fallback": row["fallback_preserved_rows"],
                        "unchanged_rejected": row["rejected_input_rows_unchanged"],
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as error:
            failures.append({"id": record.get("id"), "stage": "prepare_record", "error": f"{type(error).__name__}: {error}"})
    report = {
        "schema": SCHEMA,
        "status": "PREFLIGHT_COMPLETE" if not failures else "PREFLIGHT_WITH_FAILURES",
        "development_only": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "factors_emitted": 0,
        "backend_launched": False,
        "scoring_launched": False,
        "gpu_model_used": False,
        "policy": {**POLICY, "this_script_sha256": file_hash(Path(__file__)), "diagnostic_module_sha256": file_hash(Path(diag.__file__))},
        "manifest": str(args.manifest.resolve()),
        "baseline": str(args.baseline.resolve()),
        "source_stage": str(args.source_stage.resolve()),
        "source_stage_preflight_sha256": file_hash(args.source_stage / "preflight_report.json"),
        "record_count": len(records),
        "ready_record_count": len(results),
        "refined_source_count": len(results),
        "records": results,
        "refined_sources": results,
        "failures": failures,
    }
    write_json(args.output / "preflight_report.json", report)
    return report


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    result = run(args)
    print(
        json.dumps(
            {
                "status": result["status"],
                "record_count": result["record_count"],
                "ready_record_count": result["ready_record_count"],
                "failure_count": len(result["failures"]),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "PREFLIGHT_COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
