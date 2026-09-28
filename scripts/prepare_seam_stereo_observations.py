"""Prepare supplementary raw stereo tracks seeded at the seam frame."""
from __future__ import annotations

import cv2
import numpy as np

from align_mast3r_scale_with_stereo import left_right_consistent, stereo_disparity
from prepare_stereo_window_observations import _flow


DEPTH_MIN_M = 0.07
DEPTH_MAX_M = 0.6


def _reject(reason: str) -> dict:
    return {"accepted": False, "reason": reason}


def _check_inputs(left_images, right_images, calibration, seam_index: int, max_points: int):
    if len(left_images) != len(right_images) or len(left_images) != 41:
        raise ValueError("need synchronized 41-frame stereo sequence")
    if seam_index != 20:
        raise ValueError("seam_index must be 20")
    shape = left_images[0].shape
    if len(shape) != 2 or any(
        image.shape != shape or image.dtype != np.uint8
        for image in list(left_images) + list(right_images)
    ):
        raise ValueError("stereo frames must have identical grayscale uint8 shape")
    if max_points < 4:
        raise ValueError("max_points")
    left = calibration["left_intrinsics"]
    right = calibration["right_intrinsics"]
    baseline = float(calibration["baseline_m"])
    if not np.isfinite(baseline) or baseline <= 0:
        raise ValueError("need positive factory baseline")
    if any(any(float(c) != 0 for c in k.get("coeffs", [])) for k in (left, right)):
        raise ValueError("images must be rectified; nonzero distortion unsupported")
    if any(left[k] != right[k] for k in ("fx", "fy", "cx", "cy")):
        raise ValueError("source disparity initialization requires equal rectified intrinsics")
    return shape, left, baseline


def _exclude_existing_points(mask: np.ndarray, excluded_left_points, radius_px: int = 7) -> tuple[np.ndarray, int, int]:
    out = mask.copy()
    if excluded_left_points is None:
        return out, 0, int(np.count_nonzero(out))
    points = np.asarray(excluded_left_points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("excluded_left_points shape")
    finite = points[np.all(np.isfinite(points), axis=1)]
    for point in finite:
        cv2.circle(out, tuple(np.rint(point).astype(int)), radius_px, 0, thickness=-1)
    return out, int(len(finite)), int(np.count_nonzero(out))


def _stereo_observe(left_image, right_image, points, disparity_pair, intrinsics, baseline):
    consistent, disparity_at_points = left_right_consistent(
        points, *disparity_pair, tolerance_px=1.0
    )
    guess = points.copy()
    guess[:, 0] -= np.where(np.isfinite(disparity_at_points), disparity_at_points, 0.0)
    guess[:, 0] = np.clip(guess[:, 0], 1, left_image.shape[1] - 2)
    matched, stereo_valid = _flow(left_image, right_image, points, guess)
    actual_disparity = points[:, 0] - matched[:, 0]
    actual_depth = intrinsics["fx"] * baseline / np.maximum(actual_disparity, 1e-6)
    valid = consistent & stereo_valid
    valid &= np.abs(points[:, 1] - matched[:, 1]) <= 1.0
    valid &= (actual_disparity > 0.5) & (actual_depth >= DEPTH_MIN_M) & (actual_depth <= DEPTH_MAX_M)
    return np.column_stack((points, matched)), valid, actual_depth


def _geometry_supported(points: np.ndarray) -> bool:
    if len(points) < 4 or not np.all(np.isfinite(points)):
        return False
    singular = np.linalg.svd(points - np.mean(points, axis=0), compute_uv=False)
    return singular[0] > 1e-8 and singular[1] > singular[0] * 1e-6


def track_seam_stereo_window(
    left_images,
    right_images,
    calibration,
    *,
    seam_index: int = 20,
    excluded_left_points=None,
    max_points: int = 180,
) -> dict:
    """Track seam-seeded stereo points over 41 raw frames.

    No poses, learned outputs, GT, or IO are used. Landmarks are initialized in
    the seam camera frame and retained only if they have at least one raw stereo
    observation before and after the seam.
    """
    shape, left, baseline = _check_inputs(
        left_images, right_images, calibration, seam_index, max_points
    )
    disparities = [stereo_disparity(a, b, 128) for a, b in zip(left_images, right_images)]
    seam_disparity = disparities[seam_index][0]
    seam_depth = left["fx"] * baseline / np.maximum(seam_disparity, 1e-6)
    mask = (
        (seam_disparity > 0.5)
        & (seam_depth >= DEPTH_MIN_M)
        & (seam_depth <= DEPTH_MAX_M)
    ).astype(np.uint8) * 255
    mask, excluded_count, mask_candidate_pixels = _exclude_existing_points(
        mask, excluded_left_points, radius_px=7
    )
    features = cv2.goodFeaturesToTrack(
        left_images[seam_index],
        maxCorners=max_points,
        qualityLevel=0.01,
        minDistance=7,
        mask=mask,
        blockSize=7,
    )
    if features is None or len(features) < 20:
        return _reject("insufficient_seam_stereo_features")
    seam_points = features.reshape(-1, 2).astype(float)
    observations = np.full((41, len(seam_points), 4), np.nan, dtype=float)
    valid = np.zeros((41, len(seam_points)), dtype=bool)

    def observe_frame(index, points):
        obs, ok, _ = _stereo_observe(
            left_images[index],
            right_images[index],
            points,
            disparities[index],
            left,
            baseline,
        )
        observations[index] = obs
        valid[index] = ok

    observe_frame(seam_index, seam_points)
    back_points = seam_points.copy()
    back_alive = np.ones(len(seam_points), dtype=bool)
    for index in range(seam_index - 1, -1, -1):
        back_points, tracked = _flow(left_images[index + 1], left_images[index], back_points)
        back_alive &= tracked
        observe_frame(index, back_points)
        valid[index] &= back_alive

    forward_points = seam_points.copy()
    forward_alive = np.ones(len(seam_points), dtype=bool)
    for index in range(seam_index + 1, 41):
        forward_points, tracked = _flow(left_images[index - 1], left_images[index], forward_points)
        forward_alive &= tracked
        observe_frame(index, forward_points)
        valid[index] &= forward_alive

    seam_obs = observations[seam_index]
    seam_depth = left["fx"] * baseline / (seam_obs[:, 0] - seam_obs[:, 2])
    initial_points = np.column_stack((
        (seam_obs[:, 0] - left["cx"]) * seam_depth / left["fx"],
        (seam_obs[:, 1] - left["cy"]) * seam_depth / left["fy"],
        seam_depth,
    ))
    keep = valid[seam_index] & valid[:seam_index].any(axis=0) & valid[seam_index + 1 :].any(axis=0)
    observations = observations[:, keep]
    valid = valid[:, keep]
    initial_points = initial_points[keep]
    source_indices = np.flatnonzero(keep).astype(int)
    if len(initial_points) < 4:
        return _reject("insufficient_pre_post_seam_tracks")
    if not _geometry_supported(initial_points):
        return _reject("seam_geometry_degenerate")
    return {
        "accepted": True,
        "reason": "ok",
        "observations": observations,
        "valid": valid,
        "initial_points": initial_points,
        "observation_frame": "infrared_left_camera20",
        "source_depth_limits_m": [DEPTH_MIN_M, DEPTH_MAX_M],
        "source_indices": {
            "seam_index": int(seam_index),
            "feature_indices": source_indices.tolist(),
            "feature_id_policy": "sequential seam GFTT ids before pre/post support filtering",
            "tracked_backward_frames": int(seam_index),
            "tracked_forward_frames": int(40 - seam_index),
        },
        "exclusion": {
            "radius_px": 7,
            "excluded_left_points": int(excluded_count),
            "candidate_mask_pixels_after_exclusion": int(mask_candidate_pixels),
        },
        "tracked_landmarks": int(len(initial_points)),
        "observations_count": int(np.count_nonzero(valid)),
        "policy": "seam GFTT SGBM-gated seeded stereo LK; independent backward/forward temporal validity; no poses/GT/learned IO",
    }
