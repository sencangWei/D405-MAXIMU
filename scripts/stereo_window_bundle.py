"""Minimal shared-landmark stereo window bundle adjustment.

Prototype core only: no file IO, no MASt3R inputs, no external ground truth, and
no calibrated covariance claim.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation


PIXEL_P95_LIMIT_PX = 2.0
PIXEL_INLIER_LIMIT_PX = 2.0
PIXEL_INLIER_FRACTION_MIN = 0.95
GYRO_P95_LIMIT_RAD = np.deg2rad(5.0)
GYRO_BIAS_COMPONENT_LIMIT_RAD_S = 0.01


def _reject(reason: str, detail: str | None = None) -> dict:
    result = {"accepted": False, "reason": reason}
    if detail:
        result["detail"] = detail
    return result


def _intrinsics(values: dict, name: str) -> tuple[float, float, float, float]:
    try:
        fx = float(values["fx"])
        fy = float(values["fy"])
        cx = float(values["cx"])
        cy = float(values["cy"])
    except Exception as error:
        raise ValueError(f"{name}_intrinsics_malformed") from error
    if not np.all(np.isfinite([fx, fy, cx, cy])) or fx <= 0.0 or fy <= 0.0:
        raise ValueError(f"{name}_intrinsics_malformed")
    return fx, fy, cx, cy


def _as_rotation_sequence(value, count: int, name: str):
    if not isinstance(value, Rotation) or len(value) != count:
        raise ValueError(f"{name}_rotation_count")
    return value


def _pack(rotations: Rotation, centers: np.ndarray, points: np.ndarray) -> np.ndarray:
    return np.concatenate(
        (
            rotations.as_rotvec()[1:].ravel(),
            centers[1:].ravel(),
            points.ravel(),
            np.zeros(3),
        )
    )


def _unpack(x: np.ndarray, frames: int, points: int) -> tuple[Rotation, np.ndarray, np.ndarray, np.ndarray]:
    pose_values = 3 * (frames - 1)
    center_values = 3 * (frames - 1)
    point_values = 3 * points
    rotvecs = np.zeros((frames, 3), dtype=float)
    centers = np.zeros((frames, 3), dtype=float)
    rotvecs[1:] = x[:pose_values].reshape(frames - 1, 3)
    centers[1:] = x[pose_values : pose_values + center_values].reshape(frames - 1, 3)
    start = pose_values + center_values
    landmarks = x[start : start + point_values].reshape(points, 3)
    bias = x[start + point_values : start + point_values + 3]
    return Rotation.from_rotvec(rotvecs), centers, landmarks, bias


def _project_many(
    points: np.ndarray,
    center: np.ndarray,
    rotation: Rotation,
    intrinsics: tuple[float, float, float, float],
    baseline_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    camera = rotation.inv().apply(points - center) - np.array([baseline_m, 0.0, 0.0])
    depth = camera[:, 2]
    valid_depth = depth > 1e-9
    projected = np.full((len(points), 2), 1e6, dtype=float)
    fx, fy, cx, cy = intrinsics
    projected[valid_depth, 0] = (
        fx * camera[valid_depth, 0] / depth[valid_depth] + cx
    )
    projected[valid_depth, 1] = (
        fy * camera[valid_depth, 1] / depth[valid_depth] + cy
    )
    return projected, depth


def _pixel_errors(
    rotations: Rotation,
    centers: np.ndarray,
    landmarks: np.ndarray,
    observations: np.ndarray,
    valid: np.ndarray,
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    baseline_m: float,
) -> tuple[np.ndarray, float]:
    errors = []
    minimum_depth = np.inf
    for frame in range(len(centers)):
        point_indices = np.flatnonzero(valid[frame])
        if len(point_indices) == 0:
            continue
        frame_points = landmarks[point_indices]
        left_px, left_depth = _project_many(
            frame_points, centers[frame], rotations[frame], left, 0.0
        )
        right_px, right_depth = _project_many(
            frame_points, centers[frame], rotations[frame], right, baseline_m
        )
        minimum_depth = min(
            minimum_depth, float(np.min(left_depth)), float(np.min(right_depth))
        )
        frame_errors = np.column_stack(
            (
                left_px - observations[frame, point_indices, :2],
                right_px - observations[frame, point_indices, 2:],
            )
        )
        errors.extend(frame_errors.ravel())
    return np.asarray(errors, dtype=float), float(minimum_depth)


def _support_connected(valid: np.ndarray) -> bool:
    frame_count, point_count = valid.shape
    seen_frames = {0}
    seen_points: set[int] = set()
    changed = True
    while changed:
        changed = False
        for point in range(point_count):
            if point not in seen_points and np.any(valid[list(seen_frames), point]):
                seen_points.add(point)
                changed = True
        for frame in range(frame_count):
            if frame not in seen_frames and np.any(valid[frame, list(seen_points)]):
                seen_frames.add(frame)
                changed = True
    return len(seen_frames) == frame_count


def _rank_observable(points: np.ndarray, valid: np.ndarray) -> bool:
    supported = np.count_nonzero(valid, axis=0) >= 2
    if np.count_nonzero(supported) < 4:
        return False
    centered = points[supported] - np.mean(points[supported], axis=0)
    singular_values = np.linalg.svd(centered, compute_uv=False)
    scale = max(float(singular_values[0]), 1e-12)
    return int(np.count_nonzero(singular_values > scale * 1e-6)) >= 2


def _gyro_errors(
    rotations: Rotation,
    gyro_deltas: Rotation,
    times: np.ndarray,
    bias: np.ndarray,
    gyro_bias_jacobians: np.ndarray,
) -> np.ndarray:
    errors = []
    for frame in range(len(times) - 1):
        relative = rotations[frame].inv() * rotations[frame + 1]
        errors.append(
            (gyro_deltas[frame].inv() * relative).as_rotvec()
            - gyro_bias_jacobians[frame] @ bias
        )
    return np.asarray(errors).reshape(-1, 3)


def _diagnostics(
    result,
    valid: np.ndarray,
    pixel_errors: np.ndarray,
    gyro_errors: np.ndarray,
    minimum_depth: float,
    bias: np.ndarray,
    bias_jacobian_source: str,
) -> dict:
    pixel_norms = np.linalg.norm(pixel_errors.reshape(-1, 2), axis=1)
    gyro_norms = np.linalg.norm(gyro_errors, axis=1)
    pixel_inlier_fraction = float(
        np.mean(pixel_norms <= PIXEL_INLIER_LIMIT_PX)
    )
    return {
        "solver_success": bool(result.success),
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        "cost": float(result.cost),
        "nfev": int(result.nfev),
        "observed_pixels": int(np.count_nonzero(valid) * 4),
        "valid_observations": int(np.count_nonzero(valid)),
        "tracks": int(valid.shape[1]),
        "frames": int(valid.shape[0]),
        "pixel_rmse_px": float(np.sqrt(np.mean(pixel_errors**2))),
        "pixel_p95_px": float(np.percentile(pixel_norms, 95)),
        "pixel_inlier_limit_px": PIXEL_INLIER_LIMIT_PX,
        "pixel_inlier_fraction": pixel_inlier_fraction,
        "minimum_pixel_inlier_fraction": PIXEL_INLIER_FRACTION_MIN,
        "gyro_rmse_rad": float(np.sqrt(np.mean(gyro_errors**2))),
        "gyro_p95_rad": float(np.percentile(gyro_norms, 95)),
        "gyro_p95_limit_rad": GYRO_P95_LIMIT_RAD,
        "gyro_p95_limit_deg": 5.0,
        "gyro_bias_component_abs_max_rad_s": float(np.max(np.abs(bias))),
        "gyro_bias_component_limit_rad_s": GYRO_BIAS_COMPONENT_LIMIT_RAD_S,
        "minimum_depth_m": float(minimum_depth),
        "is_calibrated_covariance": False,
        "pixel_loss": "soft_l1",
        "pixel_gate": "stereo_reprojection_p95_le_2px_or_95pct_2px_inliers",
        "pixel_gate_source": "existing_pixel_pnp_2px_tolerance_not_accuracy_guarantee",
        "gyro_delta_convention": "camera_to_window_R_i_inverse_R_j",
        "gyro_factor": "soft_log_delta_measured_inverse_R_i_inverse_R_j_minus_Jb_bias",
        "gyro_bias_model": "Delta(b)_approximately_Delta0_Exp_Jb_bias_right_tangent",
        "gyro_bias_jacobian_source": bias_jacobian_source,
        "gyro_bias_model_is_exact": False,
        "gyro_noise_policy": "provisional_not_calibrated",
        "bias_gate_source": "accepted_initialization_guard_0p01_rad_s",
        "diagnostic_only": False,
    }


def _postfit_failures(diagnostics: dict) -> list[str]:
    failures = []
    pixel_supported = (
        diagnostics["pixel_p95_px"] <= PIXEL_P95_LIMIT_PX
        or diagnostics["pixel_inlier_fraction"] >= PIXEL_INLIER_FRACTION_MIN
    )
    if not pixel_supported:
        failures.append("stereo_reprojection_inconsistent")
    if diagnostics["gyro_p95_rad"] > GYRO_P95_LIMIT_RAD:
        failures.append("gyro_inconsistent")
    if (
        diagnostics["gyro_bias_component_abs_max_rad_s"]
        > GYRO_BIAS_COMPONENT_LIMIT_RAD_S
    ):
        failures.append("gyro_bias_exceeds_guard")
    return failures


def _residuals(
    x: np.ndarray,
    frames: int,
    points: int,
    observations: np.ndarray,
    valid: np.ndarray,
    times: np.ndarray,
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    baseline_m: float,
    gyro_deltas: Rotation,
    gyro_noise_density: float,
    gyro_bias_sigma: float,
    pixel_sigma_px: float,
    gyro_bias_jacobians: np.ndarray,
) -> np.ndarray:
    rotations, centers, landmarks, bias = _unpack(x, frames, points)
    pixel_errors, _ = _pixel_errors(
        rotations, centers, landmarks, observations, valid, left, right, baseline_m
    )
    scaled_pixels = pixel_errors / pixel_sigma_px
    robust_pixels = np.sign(scaled_pixels) * np.sqrt(
        2.0 * (np.sqrt(1.0 + scaled_pixels**2) - 1.0)
    )
    residuals = [robust_pixels]
    for frame, error in enumerate(
        _gyro_errors(rotations, gyro_deltas, times, bias, gyro_bias_jacobians)
    ):
        dt = float(times[frame + 1] - times[frame])
        sigma = max(float(gyro_noise_density) * np.sqrt(dt), 1e-9)
        residuals.append(error / sigma)
    residuals.append(bias / gyro_bias_sigma)
    return np.concatenate(residuals)


def _sparsity(frames: int, points: int, valid: np.ndarray) -> lil_matrix:
    pose_values = 3 * (frames - 1)
    center_values = 3 * (frames - 1)
    landmark_offset = pose_values + center_values
    bias_offset = landmark_offset + 3 * points
    pixel_rows = int(np.count_nonzero(valid) * 4)
    rows = pixel_rows + 3 * (frames - 1) + 3
    cols = bias_offset + 3
    pattern = lil_matrix((rows, cols), dtype=bool)
    row = 0
    for frame, point in np.argwhere(valid):
        for component in range(4):
            if frame > 0:
                rot = 3 * (frame - 1)
                center = pose_values + 3 * (frame - 1)
                pattern[row + component, rot : rot + 3] = True
                pattern[row + component, center : center + 3] = True
            landmark = landmark_offset + 3 * point
            pattern[row + component, landmark : landmark + 3] = True
        row += 4
    for frame in range(frames - 1):
        for component in range(3):
            if frame > 0:
                rot_i = 3 * (frame - 1)
                pattern[row + component, rot_i : rot_i + 3] = True
            rot_j = 3 * frame
            pattern[row + component, rot_j : rot_j + 3] = True
            pattern[row + component, bias_offset : bias_offset + 3] = True
        row += 3
    pattern[row : row + 3, bias_offset : bias_offset + 3] = True
    return pattern


def _validate(
    observations,
    valid,
    times,
    left_intrinsics,
    right_intrinsics,
    baseline_m,
    initial_points,
    initial_centers,
    initial_rotations,
    gyro_deltas,
    gyro_noise_density,
    gyro_bias_sigma,
    pixel_sigma_px,
    gyro_bias_jacobians,
):
    observations = np.asarray(observations, dtype=float)
    valid = np.asarray(valid, dtype=bool)
    times = np.asarray(times, dtype=float)
    initial_points = np.asarray(initial_points, dtype=float)
    initial_centers = np.asarray(initial_centers, dtype=float)
    if observations.ndim != 3 or observations.shape[2] != 4:
        return _reject("malformed_shape"), None
    frames, points, _ = observations.shape
    if frames < 2 or points < 4:
        return _reject("insufficient_support"), None
    if valid.shape != (frames, points) or times.shape != (frames,):
        return _reject("malformed_shape"), None
    if initial_points.shape != (points, 3) or initial_centers.shape != (frames, 3):
        return _reject("malformed_shape"), None
    try:
        initial_rotations = _as_rotation_sequence(initial_rotations, frames, "initial")
        gyro_deltas = _as_rotation_sequence(gyro_deltas, frames - 1, "gyro_delta")
        left = _intrinsics(left_intrinsics, "left")
        right = _intrinsics(right_intrinsics, "right")
    except ValueError as error:
        return _reject(str(error)), None
    if not np.isfinite(baseline_m) or float(baseline_m) <= 0.0:
        return _reject("invalid_baseline"), None
    finite_arrays = (
        np.all(np.isfinite(observations[valid]))
        and np.all(np.isfinite(times))
        and np.all(np.isfinite(initial_points))
        and np.all(np.isfinite(initial_centers))
    )
    if not finite_arrays:
        return _reject("nonfinite"), None
    if not (np.isfinite(gyro_noise_density) and float(gyro_noise_density) > 0.0):
        return _reject("invalid_noise"), None
    if not (np.isfinite(gyro_bias_sigma) and float(gyro_bias_sigma) > 0.0):
        return _reject("invalid_noise"), None
    if not (np.isfinite(pixel_sigma_px) and float(pixel_sigma_px) > 0.0):
        return _reject("invalid_noise"), None
    if gyro_bias_jacobians is None:
        gyro_bias_jacobians = np.asarray(
            [
                -float(times[frame + 1] - times[frame]) * np.eye(3)
                for frame in range(frames - 1)
            ],
            dtype=float,
        )
        bias_jacobian_source = "default_minus_dt_identity_first_order"
    else:
        gyro_bias_jacobians = np.asarray(gyro_bias_jacobians, dtype=float)
        bias_jacobian_source = "caller_supplied_right_tangent"
    if gyro_bias_jacobians.shape != (frames - 1, 3, 3):
        return _reject("malformed_gyro_bias_jacobians"), None
    if not np.all(np.isfinite(gyro_bias_jacobians)):
        return _reject("nonfinite"), None
    if np.any(np.diff(times) <= 0.0):
        return _reject("invalid_times"), None
    if np.count_nonzero(valid) < max(8, frames * 4):
        return _reject("insufficient_support"), None
    if np.any(np.count_nonzero(valid, axis=1) < 4):
        return _reject("insufficient_support"), None
    if np.any(np.count_nonzero(valid, axis=0) < 2):
        return _reject("insufficient_support"), None
    if not _support_connected(valid):
        return _reject("disconnected_support"), None
    if not _rank_observable(initial_points, valid):
        return _reject("rank_deficient_geometry"), None
    if np.any(initial_points[:, 2] <= 1e-6):
        return _reject("invalid_initial_depth"), None
    _, initial_minimum_depth = _pixel_errors(
        initial_rotations,
        initial_centers - initial_centers[0],
        initial_points,
        observations,
        valid,
        left,
        right,
        float(baseline_m),
    )
    if initial_minimum_depth <= 1e-6 or not np.isfinite(initial_minimum_depth):
        return _reject("invalid_initial_cheirality"), None
    data = (
        observations,
        valid,
        times,
        left,
        right,
        float(baseline_m),
        initial_points,
        initial_centers,
        initial_rotations,
        gyro_deltas,
        float(gyro_noise_density),
        float(gyro_bias_sigma),
        float(pixel_sigma_px),
        gyro_bias_jacobians,
        bias_jacobian_source,
    )
    return None, data


def solve_stereo_window(
    observations,
    valid,
    times,
    intrinsics_left: dict,
    intrinsics_right: dict,
    baseline_m: float,
    initial_points,
    initial_centers,
    initial_rotations,
    gyro_deltas,
    gyro_noise_density: float,
    gyro_bias_sigma: float,
    pixel_sigma_px: float = 1.0,
    gyro_bias_jacobians=None,
) -> dict:
    """Solve a metric stereo window with shared landmarks and soft gyro factors."""
    rejection, data = _validate(
        observations,
        valid,
        times,
        intrinsics_left,
        intrinsics_right,
        baseline_m,
        initial_points,
        initial_centers,
        initial_rotations,
        gyro_deltas,
        gyro_noise_density,
        gyro_bias_sigma,
        pixel_sigma_px,
        gyro_bias_jacobians,
    )
    if rejection is not None:
        return rejection
    (
        observations,
        valid,
        times,
        left,
        right,
        baseline_m,
        initial_points,
        initial_centers,
        initial_rotations,
        gyro_deltas,
        gyro_noise_density,
        gyro_bias_sigma,
        pixel_sigma_px,
        gyro_bias_jacobians,
        bias_jacobian_source,
    ) = data
    frames, points, _ = observations.shape
    initial = _pack(initial_rotations, initial_centers - initial_centers[0], initial_points)
    sparsity = _sparsity(frames, points, valid)
    try:
        result = least_squares(
            _residuals,
            initial,
            args=(
                frames,
                points,
                observations,
                valid,
                times,
                left,
                right,
                baseline_m,
                gyro_deltas,
                gyro_noise_density,
                gyro_bias_sigma,
                pixel_sigma_px,
                gyro_bias_jacobians,
            ),
            jac_sparsity=sparsity,
            x_scale="jac",
            max_nfev=200,
        )
    except Exception as error:
        return _reject("solve_failed", str(error))
    if not result.success or not np.all(np.isfinite(result.x)):
        return _reject("solve_failed", result.message)
    rotations, centers, landmarks, bias = _unpack(result.x, frames, points)
    pixel_errors, minimum_depth = _pixel_errors(
        rotations, centers, landmarks, observations, valid, left, right, baseline_m
    )
    if minimum_depth <= 1e-6 or not np.isfinite(minimum_depth):
        return _reject("negative_depth_solution")
    gyro_errors = _gyro_errors(rotations, gyro_deltas, times, bias, gyro_bias_jacobians)
    diagnostics = _diagnostics(
        result,
        valid,
        pixel_errors,
        gyro_errors,
        minimum_depth,
        bias,
        bias_jacobian_source,
    )
    failures = _postfit_failures(diagnostics)
    if failures:
        diagnostics = {**diagnostics, "diagnostic_only": True}
        return {
            "accepted": False,
            "reason": "model_consistency_failed",
            "failures": failures,
            "centers": centers,
            "rotations": rotations,
            "landmarks": landmarks,
            "gyro_bias": bias,
            "diagnostics": diagnostics,
        }
    return {
        "accepted": True,
        "reason": "ok",
        "centers": centers,
        "rotations": rotations,
        "landmarks": landmarks,
        "gyro_bias": bias,
        "diagnostics": diagnostics,
    }
