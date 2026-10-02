#!/usr/bin/env python3
"""Development-only diagnostics for accepted bidirectional stereo PnP vectors.

This script replays a small uniform sample of already accepted free-PnP stereo
rows and captures the existing ``combine_bidirectional_scale`` call/return.  It
does not change gates, factors, reports, or production artifacts.
"""

from __future__ import annotations

import argparse
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
import diagnose_stereo_low_excitation as lowdiag  # noqa: E402

SCHEMA = "umi_stereo_bidirectional_vector_diagnostic_v1"
SELECTED_ESTIMATOR = "bidirectional_pnp_weighted_mean"
VECTOR_TOLERANCE_M = 1e-6


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
    function = stereo.combine_bidirectional_scale
    module_path = Path(stereo.__file__).resolve()
    helper_path = Path(lowdiag.__file__).resolve()
    script_path = Path(__file__).resolve()
    return {
        "alignment_module": str(module_path),
        "alignment_module_sha256": file_sha256(module_path),
        "helper_module": str(helper_path),
        "helper_module_sha256": file_sha256(helper_path),
        "diagnostic_script": str(script_path),
        "diagnostic_script_sha256": file_sha256(script_path),
        "function": function.__name__,
        "signature": str(inspect.signature(function)),
        "code_sha256": code_sha256(function.__code__),
        "first_lineno": int(function.__code__.co_firstlineno),
    }


def load_source_report(path: Path) -> dict[str, Any]:
    report = lowdiag.load_source_report(path)
    if report.get("motion_estimator", "pnp") != "pnp":
        raise ValueError("bidirectional diagnostic only supports pnp motion reports")
    if report.get("correspondence_estimator", "classical") != "classical":
        raise ValueError(
            "diagnostic would require the MASt3R model for this report; refusing GPU/model path"
        )
    if report.get("pnp_rotation_mode", "free") != "free":
        raise ValueError("bidirectional diagnostic requires original free-PnP reports")
    return report


def finite_vector3(value: Any, *, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must be a finite 3-vector")
    return vector


def finite_rotation(value: Any, *, label: str) -> Rotation:
    quat = np.asarray(value, dtype=float)
    if quat.shape != (4,) or not np.all(np.isfinite(quat)):
        raise ValueError(f"{label} must be a finite xyzw quaternion")
    if float(np.linalg.norm(quat)) <= np.finfo(float).eps:
        raise ValueError(f"{label} must be nonzero")
    return Rotation.from_quat(quat)


def vector_closure(forward: dict[str, Any], reverse: dict[str, Any]) -> dict[str, Any]:
    d_i = finite_vector3(forward.get("metric_displacement_camera_i_m"), label="forward displacement")
    d_j = finite_vector3(reverse.get("metric_displacement_camera_i_m"), label="reverse displacement")
    forward_z_ji = finite_rotation(forward.get("pnp_rotation_quaternion_xyzw"), label="forward rotation")
    reverse_z_ij = finite_rotation(reverse.get("pnp_rotation_quaternion_xyzw"), label="reverse rotation")
    mapped_inverse = -forward_z_ji.inv().apply(d_j)
    direct_reverse = -reverse_z_ij.apply(d_j)
    closure = d_i - mapped_inverse
    denom = max(0.5 * (np.linalg.norm(d_i) + np.linalg.norm(mapped_inverse)), 1e-12)
    rclosure = reverse_z_ij * forward_z_ji
    scalar_agreement = None
    if "scale" in forward and "scale" in reverse:
        f_scale = float(forward["scale"])
        r_scale = float(reverse["scale"])
        scalar_agreement = {
            "forward_scale": f_scale,
            "reverse_scale": r_scale,
            "relative_disagreement": abs(f_scale - r_scale) / max(0.5 * (f_scale + r_scale), 1e-12),
        }
    return {
        "forward_metric_displacement_camera_i_m": d_i.tolist(),
        "reverse_metric_displacement_camera_j_m": d_j.tolist(),
        "forward_rotation_z_ji_quaternion_xyzw": forward_z_ji.as_quat().tolist(),
        "reverse_rotation_z_ij_quaternion_xyzw": reverse_z_ij.as_quat().tolist(),
        "reverse_inverse_mapped_by_forward_z_ji_transpose_camera_i_m": mapped_inverse.tolist(),
        "direct_reverse_mapped_by_reverse_z_ij_camera_i_m": direct_reverse.tolist(),
        "direct_reverse_delta_from_forward_m": float(np.linalg.norm(d_i - direct_reverse)),
        "vector_closure_m": float(np.linalg.norm(closure)),
        "vector_closure_mm": float(1000.0 * np.linalg.norm(closure)),
        "relative_vector_disagreement": float(np.linalg.norm(closure) / denom),
        "direction_cosine": float(np.dot(d_i, mapped_inverse) / max(np.linalg.norm(d_i) * np.linalg.norm(mapped_inverse), 1e-12)),
        "rclosure_angle_deg": float(np.degrees(rclosure.magnitude())),
        "scalar_agreement": scalar_agreement,
    }


def combine_capture_from_locals(locals_: dict[str, Any], returned: Any) -> dict[str, Any] | None:
    forward = locals_.get("forward")
    reverse = locals_.get("reverse")
    base = {
        "returned": {
            key: returned.get(key)
            for key in (
                "accepted",
                "reason",
                "scale",
                "scale_estimator",
                "bidirectional_relative_disagreement",
                "metric_distance_m",
                "reverse_metric_distance_m",
            )
            if isinstance(returned, dict) and key in returned
        },
        "forward_status": (
            {"accepted": forward.get("accepted"), "reason": forward.get("reason")}
            if isinstance(forward, dict)
            else None
        ),
        "reverse_status": (
            {"accepted": reverse.get("accepted"), "reason": reverse.get("reason")}
            if isinstance(reverse, dict)
            else None
        ),
        "geometry_complete": False,
    }
    if not isinstance(forward, dict) or not isinstance(reverse, dict):
        base["capture_error"] = "combine locals missing forward/reverse dictionaries"
        return base
    if not forward.get("accepted") or not reverse.get("accepted"):
        base["capture_error"] = "forward/reverse replay not both accepted"
        return base
    try:
        capture = vector_closure(forward, reverse)
    except (TypeError, ValueError) as error:
        base["capture_error"] = str(error)
        return base
    capture.update(base)
    capture["geometry_complete"] = True
    return capture


def profile_combine_call(function: Callable[..., Any], target_code, *args, **kwargs) -> tuple[Any, dict[str, Any] | None]:
    prior_profiler = sys.getprofile()
    captured: dict[str, Any] | None = None

    def profiler(frame, event, arg):
        nonlocal captured
        if prior_profiler is not None:
            prior_profiler(frame, event, arg)
        if event == "return" and frame.f_code is target_code:
            try:
                captured = combine_capture_from_locals(frame.f_locals, arg)
            except Exception as error:  # Observer failures must not alter producer return.
                captured = {
                    "returned": (
                        {"accepted": arg.get("accepted"), "reason": arg.get("reason")}
                        if isinstance(arg, dict)
                        else {}
                    ),
                    "geometry_complete": False,
                    "capture_error": f"{type(error).__name__}: {error}",
                }
        return profiler

    sys.setprofile(profiler)
    try:
        result = function(*args, **kwargs)
    finally:
        sys.setprofile(prior_profiler)
    return result, captured


def select_pairs(report: dict[str, Any], *, max_pairs: int) -> list[Pair]:
    eligible: list[Pair] = []
    for observation in report.get("observations", []):
        if not observation.get("accepted"):
            continue
        if observation.get("scale_estimator") != SELECTED_ESTIMATOR:
            continue
        first = int(observation["first_index"])
        second = int(observation["second_index"])
        eligible.append(
            Pair(
                first_index=first,
                second_index=second,
                sample_hop=int(observation["sample_hop"]) if observation.get("sample_hop") is not None else None,
                source_observation=observation,
            )
        )
    eligible.sort(key=lambda item: (item.first_index, item.second_index))
    if len(eligible) <= max_pairs:
        return eligible
    indices = np.rint(np.linspace(0, len(eligible) - 1, max_pairs)).astype(int)
    return [eligible[int(index)] for index in indices]


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
    result, capture = profile_combine_call(
        stereo.estimate_pair_scale,
        stereo.combine_bidirectional_scale.__code__,
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
    saved = pair.source_observation
    result_vector = result.get("metric_displacement_camera_i_m") if isinstance(result, dict) else None
    saved_vector = saved.get("metric_displacement_camera_i_m")
    vector_delta_m = None
    if result_vector is not None and saved_vector is not None:
        vector_delta_m = float(np.linalg.norm(finite_vector3(result_vector, label="fresh vector") - finite_vector3(saved_vector, label="saved vector")))
    return {
        "first_index": first,
        "second_index": second,
        "sample_hop": pair.sample_hop,
        "first_left_frame_number": int(left_numbers[first]),
        "first_right_frame_number": int(right_numbers[first]),
        "second_left_frame_number": int(left_numbers[second]),
        "second_right_frame_number": int(right_numbers[second]),
        "first_t_sec": time_binding["actual_first_t_sec"],
        "second_t_sec": time_binding["actual_second_t_sec"],
        "time_binding": time_binding,
        "source_scale_estimator": saved.get("scale_estimator"),
        "source_scale": saved.get("scale"),
        "replayed_result": {key: result.get(key) for key in ("accepted", "reason", "scale", "scale_estimator", "method") if isinstance(result, dict) and key in result},
        "fresh_forward_vs_saved": {
            "vector_delta_m": vector_delta_m,
            "tolerance_m": VECTOR_TOLERANCE_M,
            "within_tolerance": vector_delta_m is not None and vector_delta_m <= VECTOR_TOLERANCE_M,
        },
        "combine_capture": capture,
    }


def _guarded_paths(report_path: Path, report: dict[str, Any]) -> dict[str, Path]:
    paths = lowdiag.bound_source_paths(report_path, report)
    paths.update(
        {
            "alignment_module": Path(stereo.__file__).resolve(),
            "helper_module": Path(lowdiag.__file__).resolve(),
            "diagnostic_script": Path(__file__).resolve(),
        }
    )
    return paths


def run(args: argparse.Namespace) -> dict[str, Any]:
    report_path = args.report.resolve()
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    report = load_source_report(report_path)
    source_identity = source_function_identity()
    lowdiag.validate_source_contract(report)
    params = lowdiag.report_parameters(report, args)
    guarded_paths = _guarded_paths(report_path, report)
    before_hashes = lowdiag.snapshot_hashes(guarded_paths)
    declared_failures = lowdiag.validate_report_expected_hashes(report, before_hashes)
    if declared_failures:
        raise ValueError("declared source hash mismatch before replay: " + "; ".join(declared_failures))
    pairs = select_pairs(report, max_pairs=args.max_pairs)
    session = lowdiag._path_from_report(report, "session")
    trajectory = lowdiag._path_from_report(report, "trajectory")
    db3 = lowdiag._path_from_report(report, "db3")
    if session is None or trajectory is None:
        raise ValueError("report must bind session and trajectory")
    if db3 is None and report.get("prepared_dataset") is None:
        raise ValueError("report must explicitly bind db3 or prepared_dataset; refusing auto-latest")
    if pairs:
        times, positions, quaternions, _rows = stereo.load_trajectory(trajectory)
        time_bindings = {
            (pair.first_index, pair.second_index): lowdiag.validate_pair_time_binding(pair, times)
            for pair in pairs
        }
        selected_indices = {idx for pair in pairs for idx in (pair.first_index, pair.second_index)}
        left_numbers, right_numbers, sync, calibration, left_images, right_images = lowdiag._load_images(
            report, session, db3, params["trajectory_frame"], times, selected_indices
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
    missed = sum(1 for item in diagnostics if item.get("combine_capture") is None)
    incomplete_geometry = sum(
        1
        for item in diagnostics
        if item.get("combine_capture") is not None
        and not item["combine_capture"].get("geometry_complete")
    )
    after_hashes = lowdiag.snapshot_hashes(guarded_paths)
    hash_failures = lowdiag.compare_hash_snapshots(before_hashes, after_hashes)
    failures = [*hash_failures]
    if missed:
        failures.append(f"profiler missed {missed} combine_bidirectional_scale call(s)")
    if incomplete_geometry:
        failures.append(f"incomplete bidirectional geometry for {incomplete_geometry} replayed row(s)")
    result = "FAIL" if failures else ("NO_MATCHING_PAIRS" if not pairs else "PASS")
    output = {
        "schema": SCHEMA,
        "result": result,
        "development_only": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "source_report": str(report_path),
        "source_function_identity": source_identity,
        "selection": {
            "eligible_scale_estimator": SELECTED_ESTIMATOR,
            "eligible_pairs_total": sum(1 for obs in report.get("observations", []) if obs.get("accepted") and obs.get("scale_estimator") == SELECTED_ESTIMATOR),
            "selection_rule": "uniform_sorted_pair_indices_over_entire_eligible_list",
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
            "extra": lowdiag.extra_provenance(report),
        },
        "historical_source_hash_verified": False,
        "hash_validation_failures": hash_failures,
        "profiler_missed_count": missed,
        "incomplete_geometry_count": incomplete_geometry,
        "emitted_factor_count": 0,
        "no_accepted_factors_emitted": True,
        "failures": failures,
        "diagnostics": diagnostics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pairs", type=int, default=12)
    parser.add_argument("--max-depth-m", type=float, required=True)
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
