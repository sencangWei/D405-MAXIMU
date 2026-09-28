#!/usr/bin/env python3
"""Diagnostic-only right-image temporal LK consistency.

This helper consumes already-computed native right-image point coordinates.  It
does not convert depths, use poses, read GT, classify pass/fail, or write any
production state.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np


LK_OPTIONS = dict(
    winSize=(21, 21),
    maxLevel=3,
    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
)


def _image(name: str, value: Any) -> np.ndarray:
    image = np.asarray(value)
    if image.ndim != 2 or image.dtype != np.uint8:
        raise ValueError(f"{name} must be uint8 grayscale")
    return image


def _points(name: str, value: Any) -> np.ndarray:
    points = np.asarray(value, dtype=np.float32)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError(f"{name} shape")
    if not np.all(np.isfinite(points)):
        raise ValueError(f"{name} nonfinite")
    return points


def _bounds(points: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    height, width = shape
    return (
        (points[:, 0] >= 0.0)
        & (points[:, 0] < float(width))
        & (points[:, 1] >= 0.0)
        & (points[:, 1] < float(height))
    )


def _stats(values: np.ndarray) -> dict[str, Any]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"status": "UNKNOWN", "count": 0, "reason": "no_finite_values"}
    return {
        "status": "OK",
        "count": int(values.size),
        "min": float(np.min(values)),
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }


def _images(values: Any) -> list[np.ndarray]:
    images = [_image(f"images[{i}]", image) for i, image in enumerate(values)]
    if len(images) < 2:
        raise ValueError("images need at least two frames")
    shape = images[0].shape
    if any(image.shape != shape for image in images):
        raise ValueError("image shape mismatch")
    return images


def _lk_step(
    source: np.ndarray,
    target: np.ndarray,
    row_ids: np.ndarray,
    points: np.ndarray,
    total_count: int,
    label: str,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    success = np.zeros(total_count, dtype=bool)
    finite = np.zeros(total_count, dtype=bool)
    in_bounds = np.zeros(total_count, dtype=bool)
    if len(row_ids) == 0:
        return row_ids, points, {
            "label": label,
            "start_count": 0,
            "opencv_success_count": 0,
            "finite_count": 0,
            "in_bounds_count": 0,
            "kept_count": 0,
        }
    forward, status, _ = cv2.calcOpticalFlowPyrLK(
        source,
        target,
        points.reshape(-1, 1, 2).astype(np.float32),
        None,
        flags=0,
        **LK_OPTIONS,
    )
    if forward is None or status is None:
        return np.array([], dtype=int), np.empty((0, 2), dtype=np.float32), {
            "label": label,
            "start_count": int(len(row_ids)),
            "opencv_success_count": 0,
            "finite_count": 0,
            "in_bounds_count": 0,
            "kept_count": 0,
            "reason": "opencv_lk_unavailable",
        }
    next_points = forward.reshape(-1, 2)
    local_success = status.ravel().astype(bool)
    local_finite = np.all(np.isfinite(next_points), axis=1)
    local_bounds = np.zeros(len(next_points), dtype=bool)
    if np.any(local_finite):
        local_bounds[local_finite] = _bounds(next_points[local_finite], target.shape)
    keep = local_success & local_finite & local_bounds
    success[row_ids] = local_success
    finite[row_ids] = local_finite
    in_bounds[row_ids] = local_bounds
    return row_ids[keep], next_points[keep], {
        "label": label,
        "start_count": int(len(row_ids)),
        "opencv_success_count": int(np.count_nonzero(local_success)),
        "finite_count": int(np.count_nonzero(local_finite)),
        "in_bounds_count": int(np.count_nonzero(local_bounds)),
        "kept_count": int(np.count_nonzero(keep)),
        "opencv_success_mask": success.tolist(),
        "finite_mask": finite.tolist(),
        "in_bounds_mask": in_bounds.tolist(),
    }


def summarize_right_temporal_consistency(
    key_right_image: Any,
    current_right_image: Any,
    key_points_right: Any,
    current_expected_right: Any,
) -> dict[str, Any]:
    """Run unseeded right-image LK and compare against expected right points.

    ``current_expected_right`` should come from the caller's native left match
    plus factory disparity path.  This function intentionally does not seed LK
    with those expected points; it measures independent right-image temporal
    optical flow consistency.
    """

    key_image = _image("key_right_image", key_right_image)
    current_image = _image("current_right_image", current_right_image)
    if key_image.shape != current_image.shape:
        raise ValueError("image shape mismatch")
    key_points = _points("key_points_right", key_points_right)
    expected = _points("current_expected_right", current_expected_right)
    if expected.shape != key_points.shape:
        raise ValueError("point length mismatch")

    key_in_bounds = _bounds(key_points, key_image.shape)
    expected_in_bounds = _bounds(expected, current_image.shape)
    native_bounds = key_in_bounds & expected_in_bounds
    count = int(len(key_points))
    result: dict[str, Any] = {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "pose_or_depth_used": False,
        "initial_flow_seed_used": False,
        "classification_threshold_used": False,
        "input_count": count,
        "native_bounds_count": int(np.count_nonzero(native_bounds)),
        "native_bounds_mask": native_bounds.astype(bool).tolist(),
        "semantic_limitations": [
            "right temporal LK is a diagnostic consistency check, not an independent accuracy guarantee",
            "continuous errors are reported without pass/fail thresholding",
        ],
    }
    if count == 0:
        result.update(status="UNKNOWN", reason="no_points")
        return result
    if not np.any(native_bounds):
        result.update(status="UNKNOWN", reason="no_points_inside_native_bounds")
        return result

    idx = np.flatnonzero(native_bounds)
    start = key_points[idx].reshape(-1, 1, 2).astype(np.float32)
    forward, f_status, _ = cv2.calcOpticalFlowPyrLK(
        key_image,
        current_image,
        start,
        None,
        flags=0,
        **LK_OPTIONS,
    )
    forward_ok = forward is not None and f_status is not None
    tracked = np.full_like(key_points, np.nan, dtype=np.float32)
    forward_success = np.zeros(count, dtype=bool)
    backward_success = np.zeros(count, dtype=bool)
    finite_forward = np.zeros(count, dtype=bool)
    finite_backward = np.zeros(count, dtype=bool)
    forward_in_bounds = np.zeros(count, dtype=bool)
    fb_norm = np.full(count, np.nan, dtype=float)
    expected_norm = np.full(count, np.nan, dtype=float)
    if not forward_ok:
        result.update(
            status="UNKNOWN",
            reason="opencv_forward_lk_unavailable",
            opencv_forward_success_mask=forward_success.tolist(),
            opencv_backward_success_mask=backward_success.tolist(),
        )
        return result

    forward_points = forward.reshape(-1, 2)
    forward_mask = f_status.ravel().astype(bool)
    forward_finite_local = np.all(np.isfinite(forward_points), axis=1)
    forward_bounds_local = np.zeros(len(forward_points), dtype=bool)
    if np.any(forward_finite_local):
        forward_bounds_local[forward_finite_local] = _bounds(forward_points[forward_finite_local], current_image.shape)
    tracked[idx] = forward_points
    forward_success[idx] = forward_mask
    finite_forward[idx] = forward_finite_local
    forward_in_bounds[idx] = forward_bounds_local

    backward_start_local = forward_mask & forward_finite_local & forward_bounds_local
    back_idx = idx[backward_start_local]
    if len(back_idx):
        backward, b_status, _ = cv2.calcOpticalFlowPyrLK(
            current_image,
            key_image,
            forward_points[backward_start_local].reshape(-1, 1, 2).astype(np.float32),
            None,
            flags=0,
            **LK_OPTIONS,
        )
    else:
        backward, b_status = None, None
    if backward is not None and b_status is not None:
        backward_points = backward.reshape(-1, 2)
        backward_mask = b_status.ravel().astype(bool)
        backward_finite_local = np.all(np.isfinite(backward_points), axis=1)
        good_local = backward_mask & backward_finite_local
        backward_success[back_idx] = backward_mask
        finite_backward[back_idx] = backward_finite_local
        fb_norm[back_idx[good_local]] = np.linalg.norm(backward_points[good_local] - key_points[back_idx[good_local]], axis=1)
    good_forward = forward_mask & forward_finite_local & forward_bounds_local
    expected_norm[idx[good_forward]] = np.linalg.norm(forward_points[good_forward] - expected[idx[good_forward]], axis=1)

    result.update(
        status="OK",
        opencv_forward_success_mask=forward_success.tolist(),
        opencv_backward_success_mask=backward_success.tolist(),
        finite_forward_mask=finite_forward.tolist(),
        finite_backward_mask=finite_backward.tolist(),
        forward_in_bounds_mask=forward_in_bounds.tolist(),
        usable_forward_backward_count=int(np.count_nonzero(np.isfinite(fb_norm))),
        usable_expected_comparison_count=int(np.count_nonzero(np.isfinite(expected_norm))),
        fb_closure_norm_px=fb_norm.tolist(),
        forward_vs_expected_norm_px=expected_norm.tolist(),
        actual_projected_flow_px=(tracked - key_points).astype(float).tolist(),
        actual_current_points_right=tracked.astype(float).tolist(),
        fb_closure_norm_px_stats=_stats(fb_norm),
        forward_vs_expected_norm_px_stats=_stats(expected_norm),
    )
    return result


def summarize_right_temporal_chain(
    images: Any,
    key_points_right: Any,
    current_expected_right: Any,
) -> dict[str, Any]:
    """Track right points through adjacent frames, then close the chain backward."""

    frames = _images(images)
    key_points = _points("key_points_right", key_points_right)
    expected = _points("current_expected_right", current_expected_right)
    if expected.shape != key_points.shape:
        raise ValueError("point length mismatch")
    count = int(len(key_points))
    key_in_bounds = _bounds(key_points, frames[0].shape)
    expected_in_bounds = _bounds(expected, frames[-1].shape)
    native_bounds = key_in_bounds & expected_in_bounds
    result: dict[str, Any] = {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "pose_or_depth_used": False,
        "initial_flow_seed_used": False,
        "classification_threshold_used": False,
        "frame_count": len(frames),
        "input_count": count,
        "native_bounds_count": int(np.count_nonzero(native_bounds)),
        "native_bounds_mask": native_bounds.astype(bool).tolist(),
        "semantic_limitations": [
            "adjacent right temporal LK is diagnostic only, not an independent accuracy guarantee",
            "continuous errors are reported without pass/fail thresholding",
            "flows are never seeded with MASt3R endpoints, IMU, stereo, or pose priors",
        ],
    }
    if count == 0:
        result.update(status="UNKNOWN", reason="no_points")
        return result
    if not np.any(native_bounds):
        result.update(status="UNKNOWN", reason="no_points_inside_native_bounds")
        return result

    row_ids = np.flatnonzero(native_bounds)
    points = key_points[row_ids].copy()
    forward_steps = []
    for step in range(len(frames) - 1):
        row_ids, points, info = _lk_step(frames[step], frames[step + 1], row_ids, points, count, f"forward_{step}_{step+1}")
        forward_steps.append(info)
        if len(row_ids) == 0:
            break
    endpoints = np.full_like(key_points, np.nan, dtype=np.float32)
    endpoints[row_ids] = points
    forward_chain_mask = np.zeros(count, dtype=bool)
    forward_chain_mask[row_ids] = True

    expected_norm = np.full(count, np.nan, dtype=float)
    if len(row_ids):
        expected_norm[row_ids] = np.linalg.norm(points - expected[row_ids], axis=1)

    back_ids = row_ids.copy()
    back_points = points.copy()
    backward_steps = []
    for step in range(len(frames) - 1, 0, -1):
        back_ids, back_points, info = _lk_step(frames[step], frames[step - 1], back_ids, back_points, count, f"backward_{step}_{step-1}")
        backward_steps.append(info)
        if len(back_ids) == 0:
            break
    closure_points = np.full_like(key_points, np.nan, dtype=np.float32)
    closure_points[back_ids] = back_points
    backward_chain_mask = np.zeros(count, dtype=bool)
    backward_chain_mask[back_ids] = True
    fb_norm = np.full(count, np.nan, dtype=float)
    if len(back_ids):
        fb_norm[back_ids] = np.linalg.norm(back_points - key_points[back_ids], axis=1)

    result.update(
        status="OK",
        forward_steps=forward_steps,
        backward_steps=backward_steps,
        forward_chain_valid_mask=forward_chain_mask.tolist(),
        backward_chain_valid_mask=backward_chain_mask.tolist(),
        usable_forward_endpoint_count=int(np.count_nonzero(forward_chain_mask)),
        usable_forward_backward_count=int(np.count_nonzero(np.isfinite(fb_norm))),
        usable_expected_comparison_count=int(np.count_nonzero(np.isfinite(expected_norm))),
        actual_current_points_right=endpoints.astype(float).tolist(),
        actual_projected_flow_px=(endpoints - key_points).astype(float).tolist(),
        backward_closed_key_points_right=closure_points.astype(float).tolist(),
        fb_closure_norm_px=fb_norm.tolist(),
        forward_vs_expected_norm_px=expected_norm.tolist(),
        fb_closure_norm_px_stats=_stats(fb_norm),
        forward_vs_expected_norm_px_stats=_stats(expected_norm),
    )
    return result
