#!/usr/bin/env python3
"""Development-only diagnostics for rejected D405 stereo low-excitation pairs.

This script does not change acceptance, thresholds, factors, or production
artifacts.  It replays only already-rejected ``translation_excitation_low``
pairs from an existing stereo scale report and captures locals from the existing
PnP estimator at its return event.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import marshal
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Callable

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import align_mast3r_scale_with_stereo as stereo  # noqa: E402

SCHEMA = "umi_stereo_low_excitation_diagnostic_v1"
LOW_EXCITATION_REASON = "translation_excitation_low"
REQUIRED_ESTIMATOR_PARAMETERS = {
    "points_i",
    "points_j",
    "correspondence_valid",
    "disparity_left",
    "disparity_right",
    "mast3r_position_i",
    "mast3r_position_j",
    "mast3r_rotation_i",
    "mast3r_rotation_j",
    "calibration",
    "min_depth_m",
    "max_depth_m",
    "method",
}


@dataclass(frozen=True)
class Pair:
    first_index: int
    second_index: int
    sample_hop: int | None
    source_observation: dict[str, Any]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_sha256(code) -> str:
    return hashlib.sha256(marshal.dumps(code)).hexdigest()


def source_function_identity() -> dict[str, Any]:
    function = stereo.estimate_motion_from_correspondences
    module_path = Path(stereo.__file__).resolve()
    return {
        "module": str(module_path),
        "module_sha256": file_sha256(module_path),
        "function": function.__name__,
        "signature": str(inspect.signature(function)),
        "code_sha256": code_sha256(function.__code__),
        "first_lineno": int(function.__code__.co_firstlineno),
    }


def alignment_argument_defaults() -> dict[str, Any]:
    """Read defaults from the existing align script's argparse declarations."""
    tree = ast.parse(Path(stereo.__file__).read_text(encoding="utf-8"))
    defaults: dict[str, Any] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        option_names = [
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.startswith("--")
        ]
        if not option_names:
            continue
        default_node = next(
            (
                keyword.value
                for keyword in node.keywords
                if keyword.arg == "default"
            ),
            None,
        )
        if default_node is None:
            continue
        try:
            default_value = ast.literal_eval(default_node)
        except ValueError:
            continue
        for option in option_names:
            defaults[option.lstrip("-").replace("-", "_")] = default_value
    return defaults


def validate_source_contract(report: dict[str, Any]) -> dict[str, Any]:
    identity = source_function_identity()
    parameters = set(inspect.signature(stereo.estimate_motion_from_correspondences).parameters)
    missing = REQUIRED_ESTIMATOR_PARAMETERS - parameters
    if missing:
        raise ValueError(
            "alignment estimator signature missing required parameters: "
            + ", ".join(sorted(missing))
        )
    declared = {}
    for key in ("source_sha256", "input_sha256"):
        value = report.get(key)
        if isinstance(value, dict):
            declared.update(value)
    expected = declared.get(identity["module"])
    if expected is not None and expected != identity["module_sha256"]:
        raise ValueError(f"alignment source hash mismatch: {identity['module']}")
    return identity


def finite_float(value: Any) -> float | None:
    try:
        scalar = float(value)
    except (TypeError, ValueError):
        return None
    return scalar if np.isfinite(scalar) else None


def vector_list(value: Any, *, length: int | None = None) -> list[float] | None:
    array = np.asarray(value, dtype=float)
    if length is not None and array.shape != (length,):
        return None
    if array.ndim != 1 or not np.all(np.isfinite(array)):
        return None
    return [float(item) for item in array]


def numeric_stats(values: Any) -> dict[str, float] | None:
    array = np.asarray(values, dtype=float).reshape(-1)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return None
    return {
        "count": int(array.size),
        "min": float(np.min(array)),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
    }


def _local_quality(capture: dict[str, Any]) -> dict[str, Any]:
    finite = True
    nonfinite_keys: list[str] = []
    for key, value in capture.items():
        if isinstance(value, (float, int)):
            if not np.isfinite(float(value)):
                finite = False
                nonfinite_keys.append(key)
        elif isinstance(value, list):
            try:
                array = np.asarray(value, dtype=float)
            except (TypeError, ValueError):
                continue
            if array.size and not np.all(np.isfinite(array)):
                finite = False
                nonfinite_keys.append(key)
    return {"finite": finite, "nonfinite_keys": nonfinite_keys}


def low_excitation_capture_from_locals(locals_: dict[str, Any]) -> dict[str, Any]:
    metric_distance = finite_float(locals_.get("metric_distance"))
    mast3r_distance = finite_float(locals_.get("mast3r_distance"))
    pnp_rotation = locals_.get("pnp_rotation")
    camera_displacement = vector_list(locals_.get("camera_displacement_i"), length=3)
    mast3r_delta = vector_list(locals_.get("mast3r_delta_i"), length=3)
    pnp_translation = vector_list(locals_.get("pnp_translation"), length=3)
    object_points = np.asarray(locals_.get("object_points", []), dtype=float)
    inlier_object_points = np.asarray(locals_.get("inlier_object_points", []), dtype=float)
    reprojection_stats = numeric_stats(locals_.get("reprojection_before"))
    capture: dict[str, Any] = {
        "metric_distance_m": metric_distance,
        "mast3r_distance": mast3r_distance,
        "metric_below_3mm": metric_distance is not None and metric_distance < 0.003,
        "mast3r_below_1e-4": mast3r_distance is not None and mast3r_distance < 1e-4,
        "both_low": (
            metric_distance is not None
            and mast3r_distance is not None
            and metric_distance < 0.003
            and mast3r_distance < 1e-4
        ),
        "metric_displacement_camera_i_m": camera_displacement,
        "mast3r_delta_camera_i": mast3r_delta,
        "pnp_translation": pnp_translation,
        "tracked_points": int(len(object_points)) if object_points.ndim >= 1 else None,
        "pnp_inliers": int(len(locals_.get("inliers", []))) if locals_.get("inliers") is not None else None,
        "pnp_inlier_ratio": finite_float(locals_.get("inlier_ratio")),
        "pnp_reprojection_px": reprojection_stats,
        "pnp_reprojection_median_px": (
            reprojection_stats["median"] if reprojection_stats is not None else None
        ),
        "pnp_reprojection_p95_px": (
            reprojection_stats["p95"] if reprojection_stats is not None else None
        ),
        "depth_m": numeric_stats(locals_.get("z")),
        "pnp_refined": bool(locals_.get("pnp_refined", False)),
        "pnp_rotation_constrained": bool(locals_.get("pnp_rotation_constrained", False)),
        "pnp_free_rotation_delta_deg": finite_float(locals_.get("free_rotation_delta_deg")),
        "pnp_rotation_mode": locals_.get("pnp_rotation_mode"),
        "method": locals_.get("method"),
    }
    if isinstance(pnp_rotation, Rotation):
        capture["pnp_rotation_quaternion_xyzw"] = pnp_rotation.as_quat().tolist()
    if inlier_object_points.ndim == 2 and inlier_object_points.shape[1] == 3 and len(inlier_object_points):
        capture["inlier_object_points_camera_i_m"] = {
            "centroid": np.mean(inlier_object_points, axis=0).tolist(),
            "extent": (np.max(inlier_object_points, axis=0) - np.min(inlier_object_points, axis=0)).tolist(),
        }
    capture["local_quality"] = _local_quality(capture)
    return capture


def profile_return_locals(
    function: Callable[..., Any],
    target_code,
    *args,
    **kwargs,
) -> tuple[Any, dict[str, Any] | None]:
    prior_profiler = sys.getprofile()
    captured: dict[str, Any] | None = None

    def profiler(frame, event, arg):
        nonlocal captured
        if prior_profiler is not None:
            prior_profiler(frame, event, arg)
        if event == "return" and frame.f_code is target_code:
            if isinstance(arg, dict) and arg.get("reason") == LOW_EXCITATION_REASON:
                captured = low_excitation_capture_from_locals(frame.f_locals)
        return profiler

    sys.setprofile(profiler)
    try:
        result = function(*args, **kwargs)
    finally:
        sys.setprofile(prior_profiler)
    return result, captured


def load_source_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != "umi_mast3r_stereo_scale_v2":
        raise ValueError(f"unsupported stereo report schema: {report.get('schema')}")
    if report.get("external_ground_truth_used") or report.get("slam_supervision"):
        raise ValueError("stereo diagnostic accepts onboard-only reports")
    return report


def select_low_excitation_pairs(
    report: dict[str, Any],
    index_start: int,
    index_end: int,
    *,
    max_pairs: int,
) -> list[Pair]:
    if index_start > index_end:
        raise ValueError("--index-start must be <= --index-end")
    pairs: list[Pair] = []
    for observation in report.get("observations", []):
        if observation.get("accepted"):
            continue
        if observation.get("reason") != LOW_EXCITATION_REASON:
            continue
        first = int(observation["first_index"])
        second = int(observation["second_index"])
        if first < index_start or second > index_end:
            continue
        pairs.append(
            Pair(
                first_index=first,
                second_index=second,
                sample_hop=(
                    int(observation["sample_hop"])
                    if observation.get("sample_hop") is not None
                    else None
                ),
                source_observation=observation,
            )
        )
        if len(pairs) >= max_pairs:
            break
    return pairs


def _path_from_report(report: dict[str, Any], key: str) -> Path | None:
    value = report.get(key)
    return Path(value).resolve() if value else None


def _parameter_value(
    report: dict[str, Any],
    parser_defaults: dict[str, Any],
    key: str,
    *,
    cli_value: Any = None,
    synchronization_key: str | None = None,
    require_evidence: bool = False,
) -> tuple[Any, dict[str, Any]]:
    if key in report:
        return report[key], {"source": "report_field", "historical_exact": True}
    if synchronization_key is not None and synchronization_key in report.get("synchronization", {}):
        return (
            report["synchronization"][synchronization_key],
            {"source": "report_synchronization", "historical_exact": True},
        )
    if cli_value is not None:
        return (
            cli_value,
            {
                "source": "cli_explicit_override",
                "historical_exact": False,
                "note": "source report did not record this parameter; caller supplied value for diagnostic replay",
            },
        )
    if require_evidence:
        raise ValueError(
            f"source report does not record {key}; pass --{key.replace('_', '-')} "
            "with the historically recorded workflow value"
        )
    if key in parser_defaults:
        return (
            parser_defaults[key],
            {
                "source": "alignment_argument_parser_default",
                "historical_exact": False,
                "note": "source report did not record this parameter",
            },
        )
    raise ValueError(f"no parameter evidence for {key}")


def report_parameters(report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if report.get("motion_estimator", "pnp") != "pnp":
        raise ValueError("low-excitation diagnostic only supports pnp motion reports")
    if report.get("correspondence_estimator", "classical") != "classical":
        raise ValueError(
            "diagnostic would require the MASt3R model for this report; refusing GPU/model path"
        )
    parser_defaults = alignment_argument_defaults()
    values: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    for key, options in {
        "trajectory_frame": {"synchronization_key": "trajectory_frame"},
        "motion_estimator": {},
        "correspondence_estimator": {},
        "pnp_rotation_mode": {},
        "num_disparities": {},
        "min_depth_m": {},
        "max_depth_m": {"cli_value": args.max_depth_m, "require_evidence": True},
    }.items():
        value, source = _parameter_value(report, parser_defaults, key, **options)
        values[key] = value
        provenance[key] = source
    if "max_depth_m" in report and args.max_depth_m is not None:
        if abs(float(report["max_depth_m"]) - float(args.max_depth_m)) > 1e-12:
            raise ValueError("CLI --max-depth-m conflicts with source report max_depth_m")
    values.update(
        {
            "motion_estimator": "pnp",
            "correspondence_estimator": "classical",
            "num_disparities": int(values["num_disparities"]),
            "min_depth_m": float(values["min_depth_m"]),
            "max_depth_m": float(values["max_depth_m"]),
            "parameter_provenance": provenance,
            "historical_command_evidence": (
                "scripts/mast3r_slam_precision_workflow.sh invokes "
                "align_mast3r_scale_with_stereo.py with --max-depth-m 0.6 "
                "for stereo reports; reports that omit max_depth_m still "
                "require an explicit CLI override."
            ),
        }
    )
    return values


def bound_source_paths(report_path: Path, report: dict[str, Any]) -> dict[str, Path]:
    session = _path_from_report(report, "session")
    trajectory = _path_from_report(report, "trajectory")
    if session is None or trajectory is None:
        raise ValueError("report must bind session and trajectory")
    frame_csv = session / "d405_frames.csv"
    if not frame_csv.is_file():
        raise FileNotFoundError(f"missing bound d405_frames.csv: {frame_csv}")
    if not trajectory.is_file():
        raise FileNotFoundError(f"missing bound trajectory: {trajectory}")
    return {
        "source_report": report_path.resolve(),
        "trajectory": trajectory.resolve(),
        "d405_frames": frame_csv.resolve(),
    }


def snapshot_hashes(paths: dict[str, Path]) -> dict[str, Any]:
    return {
        label: {"path": str(path), "sha256": file_sha256(path)}
        for label, path in paths.items()
    }


def compare_hash_snapshots(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for label, before_entry in before.items():
        after_entry = after.get(label)
        if after_entry is None:
            failures.append(f"missing after hash: {label}")
        elif before_entry["path"] != after_entry["path"]:
            failures.append(f"path changed: {label}")
        elif before_entry["sha256"] != after_entry["sha256"]:
            failures.append(f"hash changed: {label}: {before_entry['path']}")
    return failures


def extra_provenance(report: dict[str, Any]) -> dict[str, Any]:
    db3 = _path_from_report(report, "db3")
    prepared_dataset = _path_from_report(report, "prepared_dataset")
    provenance: dict[str, Any] = {}
    if db3 is not None:
        provenance["db3"] = {
            "path": str(db3),
            "exists": db3.is_file(),
            "sha256": None,
            "sha256_note": "not hashed by this bounded diagnostic; report did not carry a source DB3 hash",
        }
    if prepared_dataset is not None:
        manifest = prepared_dataset / "dataset_manifest.json"
        if manifest.is_file():
            provenance["prepared_dataset_manifest"] = {
                "path": str(manifest.resolve()),
                "sha256": file_sha256(manifest),
            }
    return provenance


def validate_report_expected_hashes(report: dict[str, Any], hashes: dict[str, Any]) -> list[str]:
    expected: dict[str, Any] = {}
    for key in ("input_sha256", "source_sha256"):
        value = report.get(key) or {}
        if not isinstance(value, dict):
            return [f"{key} is not an object"]
        expected.update(value)
    failures: list[str] = []
    by_path = {entry["path"]: entry["sha256"] for entry in hashes.values()}
    for path, wanted in expected.items():
        resolved = str(Path(path).resolve())
        if resolved in by_path and by_path[resolved] != wanted:
            failures.append(f"hash mismatch: {resolved}")
    return failures


def validate_pair_time_binding(pair: Pair, times: np.ndarray) -> dict[str, Any]:
    first = pair.first_index
    second = pair.second_index
    if first < 0 or second < 0 or first >= len(times) or second >= len(times):
        raise ValueError(f"pair index out of bounds: {first}->{second}")
    if second <= first:
        raise ValueError(f"pair indices must be increasing: {first}->{second}")
    actual_first = float(times[first])
    actual_second = float(times[second])
    binding: dict[str, Any] = {
        "actual_first_t_sec": actual_first,
        "actual_second_t_sec": actual_second,
        "source_first_t_sec": pair.source_observation.get("first_t_sec"),
        "source_second_t_sec": pair.source_observation.get("second_t_sec"),
        "max_allowed_delta_s": 0.010,
    }
    for label, actual in (("first", actual_first), ("second", actual_second)):
        source = pair.source_observation.get(f"{label}_t_sec")
        if source is None:
            binding[f"{label}_time_delta_s"] = None
            continue
        delta = abs(float(source) - actual)
        binding[f"{label}_time_delta_s"] = float(delta)
        if delta > 0.010:
            raise ValueError(
                f"{label} timestamp mismatch for pair {first}->{second}: {delta:.6f}s"
            )
    return binding


def _load_images(
    report: dict[str, Any],
    session: Path,
    db3: Path | None,
    trajectory_frame: str,
    times: np.ndarray,
    selected_indices: set[int],
):
    left_numbers, right_numbers, sync = stereo.match_trajectory_to_stereo_frames(
        session / "d405_frames.csv", times, trajectory_frame=trajectory_frame
    )
    selected_left = {int(left_numbers[index]) for index in selected_indices}
    selected_right = {int(right_numbers[index]) for index in selected_indices}
    prepared_dataset = _path_from_report(report, "prepared_dataset")
    if prepared_dataset is not None:
        calibration = stereo.load_stereo_calibration_from_prepared_dataset(prepared_dataset)
        left_images, right_images = stereo.load_selected_prepared_stereo_images(
            prepared_dataset,
            session / "d405_frames.csv",
            selected_left,
            selected_right,
        )
    else:
        if db3 is None:
            raise ValueError("report lacks db3 and prepared_dataset image source")
        calibration = stereo.load_stereo_calibration(db3)
        left_images, right_images = stereo.load_selected_stereo_images(
            db3, selected_left, selected_right
        )
    return left_numbers, right_numbers, sync, calibration, left_images, right_images


def _repro_status(result: dict[str, Any]) -> str:
    if result.get("accepted"):
        return "nondeterministic_changed_acceptance"
    if result.get("reason") == LOW_EXCITATION_REASON:
        return "reproduced_low_excitation"
    return "different_rejection_reason"


def diagnose_pair(
    pair: Pair,
    *,
    left_numbers: np.ndarray,
    right_numbers: np.ndarray,
    left_images: dict[int, Any],
    right_images: dict[int, Any],
    positions: np.ndarray,
    rotations: Rotation,
    calibration: dict[str, Any],
    params: dict[str, Any],
    time_binding: dict[str, Any],
) -> dict[str, Any]:
    first = pair.first_index
    second = pair.second_index
    result, capture = profile_return_locals(
        stereo.estimate_pair_scale,
        stereo.estimate_motion_from_correspondences.__code__,
        left_images[int(left_numbers[first])],
        right_images[int(right_numbers[first])],
        left_images[int(left_numbers[second])],
        right_images[int(right_numbers[second])],
        positions[first],
        positions[second],
        rotations[first],
        rotations[second],
        calibration,
        params["num_disparities"],
        params["min_depth_m"],
        params["max_depth_m"],
        params["trajectory_frame"],
        params["motion_estimator"],
        params["correspondence_estimator"],
        None,
        params["pnp_rotation_mode"],
    )
    return {
        "first_index": first,
        "second_index": second,
        "sample_hop": pair.sample_hop,
        "first_left_frame_number": int(left_numbers[first]),
        "first_right_frame_number": int(right_numbers[first]),
        "second_left_frame_number": int(left_numbers[second]),
        "second_right_frame_number": int(right_numbers[second]),
        "time_binding": time_binding,
        "first_t_sec": time_binding["actual_first_t_sec"],
        "second_t_sec": time_binding["actual_second_t_sec"],
        "source_rejection_reason": pair.source_observation.get("reason"),
        "replayed_result": {
            key: result.get(key)
            for key in ("accepted", "reason", "method", "tracked_points", "pnp_inliers", "pnp_inlier_ratio")
            if key in result
        },
        "ransac_repro_status": _repro_status(result),
        "diagnostic": capture,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    report_path = args.report.resolve()
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    report = load_source_report(report_path)
    source_identity = validate_source_contract(report)
    params = report_parameters(report, args)
    guarded_paths = bound_source_paths(report_path, report)
    before_hashes = snapshot_hashes(guarded_paths)
    declared_hash_failures = validate_report_expected_hashes(report, before_hashes)
    if declared_hash_failures:
        raise ValueError(
            "declared source hash mismatch before replay: "
            + "; ".join(declared_hash_failures)
        )
    pairs = select_low_excitation_pairs(
        report, args.index_start, args.index_end, max_pairs=args.max_pairs
    )
    session = _path_from_report(report, "session")
    trajectory = _path_from_report(report, "trajectory")
    if session is None or trajectory is None:
        raise ValueError("report must bind session and trajectory")
    db3 = _path_from_report(report, "db3")
    if db3 is None and report.get("prepared_dataset") is None:
        db3 = stereo.select_db3(session)
    times, positions, quaternions, _rows = stereo.load_trajectory(trajectory)
    time_bindings = {
        (pair.first_index, pair.second_index): validate_pair_time_binding(pair, times)
        for pair in pairs
    }
    selected_indices = {index for pair in pairs for index in (pair.first_index, pair.second_index)}
    if selected_indices:
        (
            left_numbers,
            right_numbers,
            sync,
            calibration,
            left_images,
            right_images,
        ) = _load_images(
            report,
            session,
            db3,
            params["trajectory_frame"],
            times,
            selected_indices,
        )
        rotations = Rotation.from_quat(quaternions)
        diagnostics = [
            diagnose_pair(
                pair,
                left_numbers=left_numbers,
                right_numbers=right_numbers,
                left_images=left_images,
                right_images=right_images,
                positions=positions,
                rotations=rotations,
                calibration=calibration,
                params=params,
                time_binding=time_bindings[(pair.first_index, pair.second_index)],
            )
            for pair in pairs
        ]
    else:
        sync = {}
        calibration = {}
        diagnostics = []
    replayed_accepted_count = sum(1 for item in diagnostics if item["replayed_result"].get("accepted"))
    rejected_count = sum(1 for item in diagnostics if not item["replayed_result"].get("accepted"))
    profiler_missed_count = sum(
        1
        for item in diagnostics
        if item["replayed_result"].get("reason") == LOW_EXCITATION_REASON
        and item.get("diagnostic") is None
    )
    after_hashes = snapshot_hashes(guarded_paths)
    hash_failures = compare_hash_snapshots(before_hashes, after_hashes)
    profiler_failures = (
        [f"profiler missed {profiler_missed_count} reproduced low-excitation row(s)"]
        if profiler_missed_count
        else []
    )
    failures = [*hash_failures, *profiler_failures]
    if failures:
        result = "FAIL"
    elif not pairs:
        result = "NO_MATCHING_PAIRS"
    else:
        result = "PASS"
    output = {
        "schema": SCHEMA,
        "result": result,
        "development_only": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "replayed_accepted_count": replayed_accepted_count,
        "emitted_factor_count": 0,
        "rejected_replayed_count": rejected_count,
        "profiler_missed_count": profiler_missed_count,
        "no_accepted_factors_emitted": True,
        "source_report": str(report_path),
        "source_function_identity": source_identity,
        "pair_filter": {
            "reason": LOW_EXCITATION_REASON,
            "index_start": args.index_start,
            "index_end": args.index_end,
            "max_pairs": args.max_pairs,
            "selected_pairs": len(pairs),
        },
        "parameters": params,
        "synchronization": sync,
        "factory_stereo_calibration_summary": {
            "baseline_m": calibration.get("baseline_m") if calibration else None,
            "factory_topics": calibration.get("factory_topics") if calibration else None,
        },
        "provenance_sha256": {
            "guarded_before": before_hashes,
            "guarded_after": after_hashes,
            "extra": extra_provenance(report),
        },
        "hash_validation_failures": hash_failures,
        "failures": failures,
        "historical_source_hash_verified": bool(
            report.get("source_sha256") or report.get("input_sha256")
        ),
        "reproducibility_note": (
            "OpenCV solvePnPRansac may be nondeterministic; diagnostics report "
            "whether replay preserved the original rejection reason. No rejected "
            "row is accepted or converted into a factor."
        ),
        "diagnostics": diagnostics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index-start", type=int, default=323)
    parser.add_argument("--index-end", type=int, default=385)
    parser.add_argument("--max-pairs", type=int, default=8)
    parser.add_argument(
        "--max-depth-m",
        type=float,
        help=(
            "Required when the source report omits max_depth_m; pass the "
            "historically recorded align_mast3r_scale_with_stereo.py value "
            "(current precision workflow uses 0.6)."
        ),
    )
    return parser


def main() -> int:
    parser = argument_parser()
    args = parser.parse_args()
    if args.max_pairs < 1 or args.max_pairs > 64:
        parser.error("--max-pairs must be in [1, 64]")
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
