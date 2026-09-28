#!/usr/bin/env python3
"""Diagnostic-only feature-loss tracing scaffold for stereo seam windows.

This module does not run BA, graph optimization, GT scoring, recording, or any
new selector/threshold.  It shadows the existing stereo tracking gates with the
same constants and returns the original helper result unchanged alongside
diagnostic counters.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports/metric_window_bundle_20260928"
DEFAULT_OUTPUT = REPORT_ROOT / "feature_loss_trace_v1.json"
FULL_SHAPE_SUMMARY = REPORT_ROOT / "shape_full_ten_v1/summary.json"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_stereo_window_observations as prepare
import run_full_shape_controls as full_shape_controls
import run_seam_window_controls as seam_controls


TRACE_PAIRS = (26, 27, 28)
TRACE_PAIR_INDICES = {pair: list(range((pair - 1) * 40, (pair - 1) * 40 + 41, 5)) for pair in TRACE_PAIRS}
FLOW_BACKWARD_LIMIT_PX = 1.0
STEREO_EPIPOLAR_LIMIT_PX = 1.0
DISPARITY_MIN_PX = 0.5
DEPTH_LIMITS_M = (0.07, 0.6)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_image(image: np.ndarray) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json_no_overwrite(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite feature-loss trace: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _flow_with_diagnostics(source, target, points, guess=None) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    options = dict(
        winSize=(31, 31),
        maxLevel=4,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01),
    )
    next_points, status, _ = cv2.calcOpticalFlowPyrLK(
        source,
        target,
        points.astype(np.float32),
        None if guess is None else guess.astype(np.float32),
        flags=0 if guess is None else cv2.OPTFLOW_USE_INITIAL_FLOW,
        **options,
    )
    if next_points is None:
        empty = np.zeros(len(points), dtype=bool)
        flow_gates = ordered_gate_counts(
            [
                ("forward_status", empty),
                ("backward_status", empty),
                ("finite", empty),
                ("backward_error_le_1px", empty),
                ("image_bounds", empty),
            ]
        )
        return points.copy(), empty, {
            "count": int(len(points)),
            "forward_status": 0,
            "backward_status": 0,
            "finite": 0,
            "backward_error_le_1px": 0,
            "image_bounds": 0,
            "valid": 0,
            "ordered_gates": flow_gates,
        }
    backward_seed = None if guess is None else points.astype(np.float32).copy()
    previous, backward, _ = cv2.calcOpticalFlowPyrLK(
        target,
        source,
        next_points,
        backward_seed,
        flags=0 if backward_seed is None else cv2.OPTFLOW_USE_INITIAL_FLOW,
        **options,
    )
    if previous is None:
        empty = np.zeros(len(points), dtype=bool)
        finite_mask = np.all(np.isfinite(next_points), axis=1)
        flow_gates = ordered_gate_counts(
            [
                ("forward_status", status.ravel().astype(bool)),
                ("backward_status", empty),
                ("finite", finite_mask),
                ("backward_error_le_1px", empty),
                ("image_bounds", empty),
            ]
        )
        return next_points, empty, {
            "count": int(len(points)),
            "forward_status": int(np.count_nonzero(status)),
            "backward_status": 0,
            "finite": int(np.count_nonzero(finite_mask)),
            "backward_error_le_1px": 0,
            "image_bounds": 0,
            "valid": 0,
            "ordered_gates": flow_gates,
        }
    forward_mask = status.ravel().astype(bool)
    backward_mask = backward.ravel().astype(bool)
    finite_mask = np.all(np.isfinite(next_points), axis=1)
    backward_error_mask = np.linalg.norm(previous - points, axis=1) <= FLOW_BACKWARD_LIMIT_PX
    height, width = target.shape
    bounds_mask = (
        (next_points[:, 0] >= 1)
        & (next_points[:, 0] < width - 1)
        & (next_points[:, 1] >= 1)
        & (next_points[:, 1] < height - 1)
    )
    valid = forward_mask & backward_mask & finite_mask & backward_error_mask & bounds_mask
    flow_gates = ordered_gate_counts(
        [
            ("forward_status", forward_mask),
            ("backward_status", backward_mask),
            ("finite", finite_mask),
            ("backward_error_le_1px", backward_error_mask),
            ("image_bounds", bounds_mask),
        ]
    )
    return next_points, valid, {
        "count": int(len(points)),
        "forward_status": int(np.count_nonzero(forward_mask)),
        "backward_status": int(np.count_nonzero(backward_mask)),
        "finite": int(np.count_nonzero(finite_mask)),
        "backward_error_le_1px": int(np.count_nonzero(backward_error_mask)),
        "backward_error_gt_1px": int(np.count_nonzero(~backward_error_mask)),
        "image_bounds": int(np.count_nonzero(bounds_mask)),
        "valid": int(np.count_nonzero(valid)),
        "ordered_gates": flow_gates,
    }


def ordered_gate_counts(gates: list[tuple[str, np.ndarray]]) -> dict[str, Any]:
    if not gates:
        return {"total": 0, "overlap_pass": {}, "exclusive_first_failure": {}, "final_pass": 0}
    total = int(len(gates[0][1]))
    alive = np.ones(total, dtype=bool)
    overlap = {}
    exclusive = {}
    for name, mask in gates:
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != (total,):
            raise ValueError(f"{name} gate mask shape mismatch")
        overlap[name] = int(np.count_nonzero(mask))
        exclusive[name] = int(np.count_nonzero(alive & ~mask))
        alive &= mask
    return {
        "total": total,
        "overlap_pass": overlap,
        "exclusive_first_failure": exclusive,
        "final_pass": int(np.count_nonzero(alive)),
    }


def shadow_track_stages(left_images, right_images, calibration, *, max_points=180) -> dict[str, Any]:
    shape = left_images[0].shape
    left, right = calibration["left_intrinsics"], calibration["right_intrinsics"]
    baseline = float(calibration["baseline_m"])
    disparities = [prepare.stereo_disparity(a, b, 128) for a, b in zip(left_images, right_images)]
    disparity0 = disparities[0][0]
    depth0 = left["fx"] * baseline / np.maximum(disparity0, 1e-6)
    source_depth_mask = (disparity0 > DISPARITY_MIN_PX) & (depth0 >= DEPTH_LIMITS_M[0]) & (depth0 <= DEPTH_LIMITS_M[1])
    unmasked_features = cv2.goodFeaturesToTrack(
        left_images[0],
        maxCorners=max_points,
        qualityLevel=0.01,
        minDistance=7,
        mask=None,
        blockSize=7,
    )
    features = cv2.goodFeaturesToTrack(
        left_images[0],
        maxCorners=max_points,
        qualityLevel=0.01,
        minDistance=7,
        mask=source_depth_mask.astype(np.uint8) * 255,
        blockSize=7,
    )
    trace: dict[str, Any] = {
        "diagnostic_only": True,
        "depth_gate_scope": "diagnostic stereo-window source gate only; production/native MASt3R chain has separate depth/feature handling",
        "source_depth_mask_pixels": int(np.count_nonzero(source_depth_mask)),
        "source_unmasked_corners": 0 if unmasked_features is None else int(len(unmasked_features)),
        "source_eligible_corners": 0 if features is None else int(len(features)),
        "fresh_corners_eligible_each_node": [],
        "frame_stages": [],
        "keep_after_source_and_two_node_support": 0,
        "thresholds": {
            "lk_backward_error_px": FLOW_BACKWARD_LIMIT_PX,
            "stereo_epipolar_px": STEREO_EPIPOLAR_LIMIT_PX,
            "disparity_min_px": DISPARITY_MIN_PX,
            "depth_limits_m": list(DEPTH_LIMITS_M),
        },
    }
    for (left_image, right_image), disparity_pair in zip(zip(left_images, right_images), disparities):
        disp = disparity_pair[0]
        depth = left["fx"] * baseline / np.maximum(disp, 1e-6)
        mask = (disp > DISPARITY_MIN_PX) & (depth >= DEPTH_LIMITS_M[0]) & (depth <= DEPTH_LIMITS_M[1])
        corners = cv2.goodFeaturesToTrack(
            left_image,
            maxCorners=max_points,
            qualityLevel=0.01,
            minDistance=7,
            mask=mask.astype(np.uint8) * 255,
            blockSize=7,
        )
        trace["fresh_corners_eligible_each_node"].append(0 if corners is None else int(len(corners)))
    if features is None or len(features) < 20:
        trace["reason"] = "insufficient_source_stereo_features"
        trace["_shadow_result"] = {"accepted": False, "reason": trace["reason"]}
        return trace

    points = features.reshape(-1, 2)
    temporal_valid = np.ones(len(points), dtype=bool)
    observations = []
    validity = []
    for index, (left_image, right_image) in enumerate(zip(left_images, right_images)):
        temporal_flow = None
        if index:
            points, current, temporal_flow = _flow_with_diagnostics(left_images[index - 1], left_image, points)
            temporal_valid &= current
        consistent, disparity_at_points = prepare.left_right_consistent(points, *disparities[index], tolerance_px=1.0)
        guess = points.copy()
        guess[:, 0] -= np.where(np.isfinite(disparity_at_points), disparity_at_points, 0.0)
        guess[:, 0] = np.clip(guess[:, 0], 1, shape[1] - 2)
        matched, stereo_valid, stereo_flow = _flow_with_diagnostics(left_image, right_image, points, guess)
        actual_disparity = points[:, 0] - matched[:, 0]
        actual_depth = left["fx"] * baseline / np.maximum(actual_disparity, 1e-6)
        epipolar = np.abs(points[:, 1] - matched[:, 1]) <= STEREO_EPIPOLAR_LIMIT_PX
        depth_gate = (
            (actual_disparity > DISPARITY_MIN_PX)
            & (actual_depth >= DEPTH_LIMITS_M[0])
            & (actual_depth <= DEPTH_LIMITS_M[1])
        )
        final = temporal_valid & consistent & stereo_valid & epipolar & depth_gate
        observations.append(np.column_stack((points, matched)))
        validity.append(final)
        trace["frame_stages"].append(
            {
                "node": int(index),
                "temporal_lk": temporal_flow,
                "stereo_lk": stereo_flow,
                "gates": ordered_gate_counts(
                    [
                        ("cumulative_temporal_lk", temporal_valid),
                        ("left_right_consistent", consistent),
                        ("stereo_lk", stereo_valid),
                        ("epipolar_y_le_1px", epipolar),
                        ("disparity_depth", depth_gate),
                    ]
                ),
                "recoverable_stereo_without_temporal_count": int(np.count_nonzero(consistent & stereo_valid & epipolar & depth_gate)),
                "final_valid_count": int(np.count_nonzero(final)),
            }
        )
    observations = np.asarray(observations, dtype=float)
    valid = np.asarray(validity, dtype=bool)
    keep = valid[0] & (valid.sum(axis=0) >= 2)
    trace["keep_after_source_and_two_node_support"] = int(np.count_nonzero(keep))
    observations = observations[:, keep]
    valid = valid[:, keep]
    if len(observations[0]) < 20 or any(np.sum(v) < 20 for v in valid):
        trace["reason"] = "insufficient_persistent_stereo_tracks"
        trace["_shadow_result"] = {"accepted": False, "reason": trace["reason"]}
        return trace
    source = observations[0]
    depth = left["fx"] * baseline / (source[:, 0] - source[:, 2])
    initial_points = np.column_stack(
        (
            (source[:, 0] - left["cx"]) * depth / left["fx"],
            (source[:, 1] - left["cy"]) * depth / left["fy"],
            depth,
        )
    )
    trace["_shadow_result"] = {
        "accepted": True,
        "observations": observations,
        "valid": valid,
        "initial_points": initial_points,
        "observation_frame": "infrared_left_camera0",
        "source_depth_limits_m": [0.07, 0.6],
        "tracked_landmarks": int(len(initial_points)),
        "observations_count": int(valid.sum()),
        "policy": "SGBM-gated seeded stereo LK pixels; no learned positions or external reference",
    }
    return trace


def assert_tracking_identity(original: dict[str, Any], instrumented: dict[str, Any]) -> dict[str, Any]:
    keys = ("accepted", "reason", "observation_frame", "source_depth_limits_m", "tracked_landmarks", "observations_count", "policy")
    for key in keys:
        if original.get(key) != instrumented.get(key):
            raise ValueError(f"instrumented tracking changed scalar field {key}")
    array_keys = ("observations", "valid", "initial_points")
    max_deltas = {}
    for key in array_keys:
        if key not in original and key not in instrumented:
            continue
        if key not in original or key not in instrumented:
            raise ValueError(f"instrumented tracking changed presence of {key}")
        left = np.asarray(original[key])
        right = np.asarray(instrumented[key])
        if left.shape != right.shape:
            raise ValueError(f"instrumented tracking changed shape of {key}")
        if left.dtype == bool or right.dtype == bool:
            if not np.array_equal(left, right):
                raise ValueError(f"instrumented tracking changed bool array {key}")
            max_deltas[key] = 0.0
        else:
            finite = np.isfinite(left) | np.isfinite(right)
            delta = 0.0 if not np.any(finite) else float(np.nanmax(np.abs(left - right)))
            if not np.allclose(left, right, equal_nan=True, atol=0.0, rtol=0.0):
                raise ValueError(f"instrumented tracking changed numeric array {key}")
            max_deltas[key] = delta
    return {"bitidentical_arrays": True, "max_abs_deltas": max_deltas}


def trace_stereo_window(left_images, right_images, calibration, *, initialize_poses=False) -> tuple[dict[str, Any], dict[str, Any]]:
    original = prepare.track_stereo_window(
        left_images,
        right_images,
        calibration,
        initialize_poses=initialize_poses,
    )
    trace = shadow_track_stages(left_images, right_images, calibration)
    shadow_result = trace.pop("_shadow_result", None)
    if shadow_result is None:
        raise ValueError("shadow tracker did not return comparable arrays")
    identity = assert_tracking_identity(original, shadow_result)
    trace["original_identity"] = identity
    return original, trace


def trace_training_admission(data: dict[str, Any], calibration: dict[str, Any], train) -> dict[str, Any]:
    base = seam_controls.previous.base
    train = np.asarray(train, dtype=bool)
    out: dict[str, Any] = {
        "diagnostic_only": True,
        "input_tracks": int(len(train)),
        "train_tracks_before_pnp": int(np.count_nonzero(train)),
    }
    try:
        centers, rotations, admission = base.visual_initialization(data, calibration, train)
        supported = base.training_support(train, admission, data["initial_points"])
    except (ValueError, RuntimeError, cv2.error) as error:
        out.update(accepted=False, reason=str(error))
        return out
    out.update(
        accepted=True,
        pnp_admission_counts=admission[:, train].sum(axis=1).astype(int).tolist(),
        training_support_tracks_after_pnp=int(np.count_nonzero(supported)),
        support_counts_after_pnp=admission[:, supported].sum(axis=1).astype(int).tolist(),
        centers_shape=list(np.asarray(centers).shape),
        rotations_count=int(len(rotations)),
    )
    return out


def pnp_trace_for_window(data: dict[str, Any], calibration: dict[str, Any], heldout) -> tuple[dict[str, Any], Any, Any, np.ndarray | None, np.ndarray | None]:
    train = ~np.asarray(heldout, dtype=bool)
    trace = trace_training_admission(data, calibration, train)
    if not trace.get("accepted"):
        return trace, None, None, None, None
    base = seam_controls.previous.base
    centers, rotations, admission = base.visual_initialization(data, calibration, train)
    supported = base.training_support(train, admission, data["initial_points"])
    return trace, centers, rotations, admission, supported


def support_summary(label: str, data: dict[str, Any], admission: np.ndarray | None, supported: np.ndarray | None, heldout) -> dict[str, Any]:
    valid = np.asarray(data.get("valid"), dtype=bool)
    heldout = np.asarray(heldout, dtype=bool)
    train = ~heldout
    out = {
        "label": label,
        "points": int(valid.shape[1]),
        "raw_valid_counts": valid.sum(axis=1).astype(int).tolist(),
        "train_raw_valid_counts": valid[:, train].sum(axis=1).astype(int).tolist(),
        "heldout_points": int(np.count_nonzero(heldout)),
        "train_points": int(np.count_nonzero(train)),
    }
    if admission is not None:
        out["pnp_admission_counts_train"] = admission[:, train].sum(axis=1).astype(int).tolist()
    if supported is not None:
        out["training_support_tracks_after_pnp"] = int(np.count_nonzero(supported))
        out["support_counts_after_pnp"] = admission[:, supported].sum(axis=1).astype(int).tolist()
    return out


def cohort_counts(label: str, valid: np.ndarray, admission: np.ndarray, train: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    mask = np.asarray(mask, dtype=bool)
    train = np.asarray(train, dtype=bool)
    if mask.shape != train.shape or valid.shape[1:] != mask.shape or admission.shape != valid.shape:
        raise ValueError(f"{label} cohort shape")
    train_mask = mask & train
    return {
        "label": label,
        "points": int(np.count_nonzero(mask)),
        "train_points": int(np.count_nonzero(train_mask)),
        "raw_valid_counts": valid[:, mask].sum(axis=1).astype(int).tolist(),
        "train_raw_valid_counts": valid[:, train_mask].sum(axis=1).astype(int).tolist(),
        "pnp_admission_counts_train": admission[:, train_mask].sum(axis=1).astype(int).tolist(),
    }


def compare_frozen_pair_summary(
    case_name: str,
    pair_number: int,
    actual: dict[str, Any],
    *,
    summary_path: Path = FULL_SHAPE_SUMMARY,
) -> dict[str, Any]:
    summary = json.loads(summary_path.read_text())
    frozen_case = next((case for case in summary.get("cases", []) if case.get("case") == case_name), None)
    if frozen_case is None:
        raise ValueError(f"case not found in frozen full-shape summary: {case_name}")
    frozen_pair = next((pair for pair in frozen_case.get("pairs", []) if pair.get("pair") == pair_number), None)
    if frozen_pair is None:
        raise ValueError(f"pair not found in frozen full-shape summary: {case_name} pair {pair_number}")

    checks = {
        "indices": actual.get("indices"),
        "raw_frame_indices": actual.get("raw_frame_indices"),
        "node_train_tracks": actual.get("node_train_tracks_after_combined_pnp"),
        "crosswindow_support_count": actual.get("crosswindow_support_count"),
        "seam_source_candidates": actual.get("seam_replenish", {}).get("seam_source_candidates"),
        "seam_born_added": actual.get("seam_replenish", {}).get("seam_born_added"),
        "seam_born_train": actual.get("seam_replenish", {}).get("seam_born_train"),
        "seam_born_heldout": actual.get("seam_replenish", {}).get("seam_born_heldout"),
        "boundary_matches": actual.get("boundary_matches", {}).get("matched_pairs"),
    }
    expected = {name: frozen_pair.get(name) for name in checks}
    actual_values = dict(checks)
    matches = {name: actual_values.get(name) == expected.get(name) for name in expected}
    return {
        "summary_path": str(summary_path),
        "summary_sha256": sha256_file(summary_path),
        "case": case_name,
        "pair": int(pair_number),
        "expected": expected,
        "actual": actual_values,
        "matches": matches,
        "all_match": all(matches.values()),
        "frozen_ba_accepted": frozen_pair.get("accepted"),
        "frozen_ba_reason": frozen_pair.get("reason"),
        "trace_pre_ba_accepted": actual.get("accepted"),
        "trace_pre_ba_reason": actual.get("reason"),
    }


def frozen_summary_case(case_name: str, *, summary_path: Path = FULL_SHAPE_SUMMARY) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = json.loads(summary_path.read_text())
    frozen_case = next((case for case in summary.get("cases", []) if case.get("case") == case_name), None)
    if frozen_case is None:
        raise ValueError(f"case not found in frozen full-shape summary: {case_name}")
    return summary, frozen_case


def validate_frozen_sources(summary: dict[str, Any]) -> dict[str, Any]:
    expected = summary.get("source_sha256")
    if not isinstance(expected, dict) or len(expected) != 14:
        raise ValueError("frozen full-shape summary must bind exactly 14 producer sources")
    actual = {path: sha256_file(Path(path)) for path in expected}
    if actual != expected:
        changed = sorted(path for path in expected if actual.get(path) != expected.get(path))
        raise ValueError(f"frozen producer source hash mismatch: {changed}")
    return {"source_files_verified": len(actual), "source_sha256": actual}


def validate_frozen_case_inputs(graph_path: Path, frozen_case: dict[str, Any]) -> dict[str, Any]:
    expected = frozen_case.get("input_sha256")
    if not isinstance(expected, dict) or len(expected) != 8:
        raise ValueError("frozen case summary must bind exactly 8 input files")
    actual = {str(path): sha256_file(path) for path in full_shape_controls._case_input_paths(graph_path)}
    if actual != expected:
        raise ValueError("frozen case input hash mismatch")
    return {"input_files_verified": len(actual), "input_sha256": actual}


def validate_frozen_decoded_images(
    frozen_case: dict[str, Any],
    left_images: dict[int, np.ndarray],
    right_images: dict[int, np.ndarray],
) -> dict[str, Any]:
    expected = frozen_case.get("decoded_grayscale_frame_sha256")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("frozen case summary missing decoded grayscale hashes")
    actual = {
        f"{stream}:{number}": sha256_image(image)
        for stream, images in (("left", left_images), ("right", right_images))
        for number, image in images.items()
    }
    expected_selected = {key: expected.get(key) for key in actual}
    if any(value is None for value in expected_selected.values()) or actual != expected_selected:
        raise ValueError("frozen decoded image hash mismatch")
    return {
        "decoded_selected_frames_verified": len(actual),
        "decoded_grayscale_frame_sha256": actual,
    }


def load_case_pair_images(case_name: str, pair_number: int) -> dict[str, Any]:
    graph_map = dict(full_shape_controls.case_graphs())
    if case_name not in graph_map:
        raise ValueError(f"unknown case: {case_name}")
    if pair_number not in range(1, 30):
        raise ValueError("pair number must be 1..29")
    graph_path = graph_map[case_name]
    frozen_summary, frozen_case = frozen_summary_case(case_name)
    frozen_source_identity = validate_frozen_sources(frozen_summary)
    frozen_input_identity = validate_frozen_case_inputs(graph_path, frozen_case)
    graph = json.loads(graph_path.read_text())
    inputs = graph["inputs"]
    report_path = Path(inputs["stereo_report"])
    report = json.loads(report_path.read_text())
    calibration = report["factory_stereo_calibration"]
    trajectory = Path(report["trajectory"])
    timestamps = seam_controls.previous.base.stereo.load_trajectory(trajectory)[0]
    selections = full_shape_controls.full_seam.pair_windows(len(timestamps))
    selection = selections[pair_number - 1]
    expected = TRACE_PAIR_INDICES.get(pair_number)
    if expected is not None and selection.tolist() != expected:
        raise ValueError("fixed pair schedule mismatch")
    local_dense = seam_controls.previous.base.tracking_frames(selection)
    session = Path(inputs["session"])
    left_numbers, right_numbers, _ = seam_controls.previous.base.stereo.match_trajectory_to_stereo_frames(
        session / "d405_frames.csv",
        timestamps,
        trajectory_frame="infrared_left",
    )
    left, right = seam_controls.previous.base.stereo.load_selected_prepared_stereo_images(
        trajectory.parent / "dataset",
        session / "d405_frames.csv",
        {int(left_numbers[i]) for i in local_dense},
        {int(right_numbers[i]) for i in local_dense},
    )
    frozen_decoded_identity = validate_frozen_decoded_images(frozen_case, left, right)
    input_hashes = {str(path): sha256_file(path) for path in full_shape_controls._case_input_paths(graph_path)}
    source_hashes = {
        str(path): sha256_file(path)
        for path in [
            Path(__file__).resolve(),
            Path(seam_controls.__file__).resolve(),
            ROOT / "scripts/join_stereo_windows.py",
            ROOT / "scripts/prepare_stereo_window_observations.py",
            ROOT / "scripts/prepare_seam_stereo_observations.py",
            Path(full_shape_controls.__file__).resolve(),
        ]
    }
    return {
        "case": case_name,
        "pair": int(pair_number),
        "graph_path": str(graph_path),
        "selection": selection,
        "dense": local_dense,
        "timestamps": timestamps,
        "calibration": calibration,
        "left_images": [left[int(left_numbers[i])] for i in local_dense],
        "right_images": [right[int(right_numbers[i])] for i in local_dense],
        "input_sha256": input_hashes,
        "source_sha256": source_hashes,
        "frozen_provenance": {
            "summary_path": str(FULL_SHAPE_SUMMARY),
            "summary_sha256": sha256_file(FULL_SHAPE_SUMMARY),
            **frozen_source_identity,
            **frozen_input_identity,
            **frozen_decoded_identity,
        },
    }


def trace_case_pair(case_name: str, pair_number: int) -> dict[str, Any]:
    loaded = load_case_pair_images(case_name, pair_number)
    before_inputs = dict(loaded["input_sha256"])
    selection = loaded["selection"]
    left_images = loaded["left_images"]
    right_images = loaded["right_images"]
    calibration = loaded["calibration"]
    datasets = []
    window_traces = []
    for part, start in enumerate((0, 20)):
        original, shadow = trace_stereo_window(
            left_images[start : start + 21],
            right_images[start : start + 21],
            calibration,
            initialize_poses=False,
        )
        sampled = dict(original)
        if original.get("accepted"):
            sampled["observations"] = np.asarray(original["observations"])[::5]
            sampled["valid"] = np.asarray(original["valid"])[::5]
        datasets.append(sampled)
        window_traces.append(
            {
                "part": part,
                "indices": selection[part * 4 : part * 4 + 5].astype(int).tolist(),
                "tracking": shadow,
                "accepted": bool(original.get("accepted", False)),
                "reason": original.get("reason"),
            }
        )
    result: dict[str, Any] = {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "bundle_adjustment_run": False,
        "graph_optimizer_run": False,
        "diagnostic_pnp_run": True,
        "threshold_or_selector_changed": False,
        "case": case_name,
        "pair": int(pair_number),
        "indices": selection.astype(int).tolist(),
        "raw_frame_indices": loaded["dense"].astype(int).tolist(),
        "graph_path": loaded["graph_path"],
        "window_traces": window_traces,
        "input_sha256": before_inputs,
        "source_sha256": loaded["source_sha256"],
        "frozen_provenance": loaded["frozen_provenance"],
        "limitations": [
            "seam-born internal LK stage attribution is not shadow-traced in v1",
            "counts stop before BA solve and do not evaluate ATE/covariance/confidence",
            "post-PnP training-support counts are not raw MASt3R matched-point counts",
        ],
    }
    if not all(dataset.get("accepted") for dataset in datasets):
        result["accepted"] = False
        result["reason"] = "raw_window_tracking_refused"
        return result

    matches = seam_controls.previous.boundary_matches(*datasets)
    holdouts = [np.arange(len(datasets[0]["initial_points"])) % 5 == 0, matches["holdout_b"]]
    poses = []
    admissions = []
    supported_masks = []
    independent_support = []
    for part, (data, heldout) in enumerate(zip(datasets, holdouts)):
        pnp_trace, centers, rotations, admission, supported = pnp_trace_for_window(data, calibration, heldout)
        independent_support.append(support_summary(f"independent_part_{part}", data, admission, supported, heldout) | {"pnp_trace": pnp_trace})
        if centers is None:
            result.update(accepted=False, reason=f"independent_part_{part}_pnp_failed", independent_support=independent_support)
            return result
        poses.append((centers, rotations))
        admissions.append(admission)
        supported_masks.append(supported)

    try:
        joined = seam_controls.previous.join_stereo_windows(*datasets, *admissions, *poses[0], *poses[1], first_holdout=holdouts[0], second_holdout=holdouts[1])
    except (ValueError, RuntimeError, cv2.error) as error:
        result.update(accepted=False, reason="join_window_geometry_failed", detail=str(error), independent_support=independent_support)
        return result
    seam = seam_controls.track_seam_stereo_window(
        left_images,
        right_images,
        calibration,
        excluded_left_points=joined["observations"][4, joined["valid"][4], :2],
    )
    result["boundary_matches"] = {
        "matched_pairs": int(len(matches["pairs_a"])),
        "rejected_ambiguous": int(matches.get("rejected_ambiguous", 0)),
    }
    result["independent_support"] = independent_support
    result["joined_pre_replenish"] = support_summary("joined_pre_replenish", joined, joined["admission"], None, joined["heldout"])
    if not seam.get("accepted"):
        result.update(accepted=False, reason="seam_observations_refused", seam_reason=seam.get("reason"))
        return result
    try:
        supplemented, detail = seam_controls.replenish(joined, seam)
    except (ValueError, RuntimeError, cv2.error) as error:
        result.update(accepted=False, reason="seam_replenish_geometry_failed", detail=str(error))
        return result
    try:
        centers, rotations, admission = seam_controls.previous.base.visual_initialization(supplemented, calibration, supplemented["train"])
    except (ValueError, RuntimeError, cv2.error) as error:
        result.update(accepted=False, reason="combined_pnp_failed", detail=str(error), seam_replenish=detail)
        return result
    supplemented.update(initial_centers=centers, initial_rotations=rotations, admitted_train=admission & supplemented["train"][None, :])
    shared = admission[:4].any(axis=0) & admission[4] & admission[5:].any(axis=0) & supplemented["train"]
    first_count = len(datasets[0]["initial_points"])
    joined_count = len(joined["initial_points"])
    total_count = len(supplemented["initial_points"])
    cohort_masks = {
        "partA_original_first_window": np.arange(total_count) < first_count,
        "partB_new_from_second_window": (np.arange(total_count) >= first_count) & (np.arange(total_count) < joined_count),
        "seam_added": np.arange(total_count) >= joined_count,
    }
    result["seam_replenish"] = detail
    result["seam_trace"] = {
        "accepted": True,
        "tracked_landmarks": int(seam["tracked_landmarks"]),
        "observations_count": int(seam["observations_count"]),
        "valid_counts_sampled_nodes": seam["valid"][::5].sum(axis=1).astype(int).tolist(),
    }
    result["joined_post_replenish"] = support_summary("joined_post_replenish", supplemented, admission, None, supplemented["heldout"])
    result["cohort_counts_after_replenish"] = [
        cohort_counts(label, supplemented["valid"], admission, supplemented["train"], mask)
        for label, mask in cohort_masks.items()
    ]
    result["crosswindow_shared_tracks_after_pnp"] = int(np.count_nonzero(shared))
    result["crosswindow_shared_counts_after_pnp"] = admission[:, shared].sum(axis=1).astype(int).tolist()
    result["node_train_tracks_after_combined_pnp"] = (admission & supplemented["train"][None, :]).sum(axis=1).astype(int).tolist()
    result["crosswindow_support_count"] = int(np.count_nonzero(shared))
    result["accepted"] = True
    result["reason"] = "ok_pre_ba_trace"
    result["frozen_summary_identity"] = compare_frozen_pair_summary(case_name, pair_number, result)
    after_inputs = {path: sha256_file(Path(path)) for path in before_inputs}
    if before_inputs != after_inputs:
        raise ValueError("input changed during feature-loss trace")
    return result


def frozen_pair_context(case_name: str, pair_number: int, *, summary_path: Path = FULL_SHAPE_SUMMARY) -> dict[str, Any]:
    _, frozen_case = frozen_summary_case(case_name, summary_path=summary_path)
    frozen_pair = next((pair for pair in frozen_case.get("pairs", []) if pair.get("pair") == pair_number), None)
    if frozen_pair is None:
        raise ValueError(f"pair not found in frozen full-shape summary: {case_name} pair {pair_number}")
    fields = (
        "pair",
        "indices",
        "raw_frame_indices",
        "node_train_tracks",
        "crosswindow_support_count",
        "seam_source_candidates",
        "seam_born_added",
        "seam_born_train",
        "seam_born_heldout",
        "boundary_matches",
        "accepted",
        "reason",
    )
    return {field: frozen_pair.get(field) for field in fields}


def trace_batch() -> dict[str, Any]:
    cases = [case for case, _ in full_shape_controls.case_graphs()]
    if len(cases) != 10 or len(set(cases)) != 10:
        raise ValueError("batch trace requires exactly ten unique frozen cases")
    requested = [(case, pair) for case in cases for pair in TRACE_PAIRS]
    if len(requested) != 30 or len(set(requested)) != 30:
        raise ValueError("batch trace requires exactly 10 cases x 3 unique fixed pairs")
    rows = []
    for case, pair in requested:
        rows.append(trace_case_pair(case, pair))
    seen = {(row.get("case"), row.get("pair")) for row in rows}
    if seen != set(requested):
        raise ValueError("batch trace missing or duplicated case/pair rows")
    count_identity_rows = [row for row in rows if row.get("frozen_summary_identity", {}).get("all_match") is True]
    return {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "bundle_adjustment_run": False,
        "graph_optimizer_run": False,
        "diagnostic_pnp_run": True,
        "threshold_or_selector_changed": False,
        "cases": cases,
        "pairs": list(TRACE_PAIRS),
        "rows": rows,
        "row_count": len(rows),
        "unique_case_pair_count": len(seen),
        "frozen_count_identity_rows": len(count_identity_rows),
        "trace_pre_ba_accepted_count": sum(1 for row in rows if row.get("accepted") is True),
        "frozen_ba_accepted_count": sum(1 for row in rows if frozen_pair_context(row["case"], row["pair"]).get("accepted") is True),
        "limitations": [
            "batch trace is diagnostic-only and still stops before BA/graph optimization",
            "trace_pre_ba_accepted_count and frozen_ba_accepted_count are separate semantics",
        ],
    }


def build_skeleton() -> dict[str, Any]:
    sources = [
        Path(__file__).resolve(),
        ROOT / ".planning/metric_window_bundle_20260928/run_seam_window_controls.py",
        ROOT / "scripts/join_stereo_windows.py",
        ROOT / "scripts/prepare_stereo_window_observations.py",
    ]
    payload = {
        "diagnostic_only": True,
        "skeleton_only": True,
        "external_ground_truth_used": False,
        "bundle_adjustment_run": False,
        "graph_optimizer_run": False,
        "diagnostic_pnp_run": False,
        "threshold_or_selector_changed": False,
        "intended_scope": {
            "cases": "actual ten cases after root review",
            "pairs": list(TRACE_PAIRS),
            "pair_indices": TRACE_PAIR_INDICES,
            "note": "initial output is source-ready scaffold; no real full trace run yet",
        },
        "source_sha256": {str(path): sha256_file(path) for path in sources},
        "limitations": [
            "does not prove video quality is perfect",
            "does not prove algorithm is the sole root cause",
            "training 143->19 style losses are post-tracking train/PnP/support diagnostics inside the diagnostic stereo window, not MASt3R match counts",
            "diagnostic source depth gate is 0.07..0.6 m and is separate from the production/native MASt3R depth behavior",
            "full ten-case pair trace requires root-reviewed run of this scaffold",
        ],
    }
    if FULL_SHAPE_SUMMARY.exists():
        summary = json.loads(FULL_SHAPE_SUMMARY.read_text())
        payload["frozen_full_shape_summary"] = {
            "path": str(FULL_SHAPE_SUMMARY),
            "sha256": sha256_file(FULL_SHAPE_SUMMARY),
            "cases": len(summary.get("cases", [])),
            "pairs": sum(len(case.get("pairs", [])) for case in summary.get("cases", [])),
            "accepted_pairs": sum(1 for case in summary.get("cases", []) for pair in case.get("pairs", []) if pair.get("accepted") is True),
            "refused_pairs": sum(1 for case in summary.get("cases", []) for pair in case.get("pairs", []) if pair.get("accepted") is False),
        }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case")
    parser.add_argument("--pair", type=int)
    parser.add_argument("--batch", action="store_true", help="trace all ten frozen cases for fixed pairs 26/27/28")
    args = parser.parse_args()
    if args.batch and (args.case is not None or args.pair is not None):
        raise ValueError("--batch cannot be combined with --case/--pair")
    if args.batch:
        payload = trace_batch()
        kind = "batch_trace"
    elif args.case is None and args.pair is None:
        payload = build_skeleton()
        kind = "skeleton"
    elif args.case is not None and args.pair is not None:
        payload = trace_case_pair(args.case, args.pair)
        kind = "trace"
    else:
        raise ValueError("--case and --pair must be supplied together")
    write_json_no_overwrite(args.output, payload)
    print(f"wrote {kind} {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
