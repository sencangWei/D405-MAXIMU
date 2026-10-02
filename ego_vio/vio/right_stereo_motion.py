"""RIGHT-centric stereo motion via mirrored native LEFT PnP; experimental only."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import align_mast3r_scale_with_stereo as stereo  # noqa: E402


MIRROR = np.diag([-1.0, 1.0, 1.0])


def estimate_right_motion_from_correspondences(
    points_right_i: np.ndarray,
    points_right_j: np.ndarray,
    correspondence_valid: np.ndarray,
    disparity_left: np.ndarray,
    disparity_right: np.ndarray,
    mast3r_position_i: np.ndarray,
    mast3r_position_j: np.ndarray,
    mast3r_rotation_i: Rotation,
    mast3r_rotation_j: Rotation,
    calibration: dict[str, Any],
    min_depth_m: float,
    max_depth_m: float,
    method: str,
    *,
    pnp_iterations: int = 200,
    pnp_reprojection_error_px: float = 2.0,
    refine_pnp: bool = False,
    pnp_rotation_mode: str = "free",
) -> dict[str, Any]:
    left_disp, right_disp = _validate_disparities(disparity_left, disparity_right)
    width = left_disp.shape[1]
    height = left_disp.shape[0]
    virtual_calibration = _virtual_calibration(calibration, width)
    _validate_camera_dimensions(calibration, width, height)
    virtual_result = stereo.estimate_motion_from_correspondences(
        _mirror_points(points_right_i, width),
        _mirror_points(points_right_j, width),
        np.asarray(correspondence_valid, dtype=bool).copy(),
        -np.fliplr(right_disp),
        -np.fliplr(left_disp),
        MIRROR @ _vec3(mast3r_position_i, "mast3r_position_i"),
        MIRROR @ _vec3(mast3r_position_j, "mast3r_position_j"),
        _mirror_rotation(mast3r_rotation_i),
        _mirror_rotation(mast3r_rotation_j),
        virtual_calibration,
        min_depth_m,
        max_depth_m,
        method,
        trajectory_frame="infrared_left",
        pnp_iterations=pnp_iterations,
        pnp_reprojection_error_px=pnp_reprojection_error_px,
        refine_pnp=refine_pnp,
        pnp_rotation_mode=pnp_rotation_mode,
    )
    return _restore_result(virtual_result)


def _validate_disparities(disparity_left: np.ndarray, disparity_right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    left = np.asarray(disparity_left, dtype=float)
    right = np.asarray(disparity_right, dtype=float)
    if left.ndim != 2 or right.ndim != 2 or left.shape != right.shape:
        raise ValueError("left/right disparity maps must be matching 2D arrays")
    if not np.all(np.isfinite(left)) or not np.all(np.isfinite(right)):
        raise ValueError("disparity maps must be finite")
    return left.copy(), right.copy()


def _virtual_calibration(calibration: dict[str, Any], width: int) -> dict[str, Any]:
    if width <= 1:
        raise ValueError("image width must be greater than one")
    left = calibration.get("left")
    right = calibration.get("right")
    if not isinstance(right, dict) or not isinstance(left, dict):
        raise ValueError("calibration must contain left/right intrinsics")
    right = dict(right)
    _validate_rectified_factory(calibration)
    for key in ("fx", "fy", "cx", "cy"):
        if not np.isfinite(float(right[key])):
            raise ValueError(f"right intrinsic {key} must be finite")
    virtual_right = {**right, "cx": float(width - 1 - float(right["cx"]))}
    virtual = dict(calibration)
    virtual["left"] = virtual["right"] = virtual_right
    return virtual


def _validate_camera_dimensions(calibration: dict[str, Any], width: int, height: int) -> None:
    for camera in ("left", "right"):
        intrinsics = calibration.get(camera, {})
        if int(intrinsics.get("width", -1)) != width or int(intrinsics.get("height", -1)) != height:
            raise ValueError("disparity map shape must match calibrated camera dimensions")
        if float(intrinsics.get("fx", 0.0)) <= 0.0 or float(intrinsics.get("fy", 0.0)) <= 0.0:
            raise ValueError("camera fx/fy must be positive")


def _validate_rectified_factory(calibration: dict[str, Any]) -> None:
    rotation = np.asarray(calibration.get("right_rotation_from_left"), dtype=float)
    translation = np.asarray(calibration.get("right_translation_from_left_m"), dtype=float)
    baseline = float(calibration.get("baseline_m"))
    if rotation.shape != (3, 3) or not np.all(np.isfinite(rotation)):
        raise ValueError("factory right_rotation_from_left must be a finite 3x3 matrix")
    if not np.allclose(rotation, np.eye(3), atol=1e-9, rtol=0.0):
        raise ValueError("RIGHT-centric wrapper requires rectified factory rotation")
    if translation.shape != (3,) or not np.all(np.isfinite(translation)):
        raise ValueError("factory right_translation_from_left_m must be finite")
    if not np.isfinite(baseline) or baseline <= 0.0:
        raise ValueError("factory baseline_m must be positive")
    if abs(float(translation[0]) + baseline) > 1e-4 or not np.allclose(translation[1:], [0.0, 0.0], atol=1e-9, rtol=0.0):
        raise ValueError("factory baseline must match rectified RIGHT translation")
    for camera in ("left", "right"):
        coeffs = np.asarray(calibration.get(camera, {}).get("coeffs", []), dtype=float)
        if coeffs.size and (not np.all(np.isfinite(coeffs)) or np.max(np.abs(coeffs)) > 1e-12):
            raise ValueError("RIGHT-centric wrapper requires zero distortion coefficients")


def _mirror_points(points: np.ndarray, width: int) -> np.ndarray:
    mirrored = np.asarray(points, dtype=np.float32).copy()
    if mirrored.ndim != 2 or mirrored.shape[1] != 2 or not np.all(np.isfinite(mirrored)):
        raise ValueError("RIGHT points must be finite Nx2 arrays")
    mirrored[:, 0] = float(width - 1) - mirrored[:, 0]
    return mirrored


def _mirror_rotation(rotation: Rotation) -> Rotation:
    matrix = MIRROR @ rotation.as_matrix() @ MIRROR
    if not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-9) or np.linalg.det(matrix) <= 0.0:
        raise ValueError("mirrored rotation is not proper")
    return Rotation.from_matrix(matrix)


def _vec3(value: Any, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must be a finite 3-vector")
    return vector.copy()


def _restore_result(result: dict[str, Any]) -> dict[str, Any]:
    restored = dict(result)
    restored["source_provenance"] = {
        "right_pixels": True,
        "mirror_representation": "u_prime_equals_width_minus_1_minus_u",
        "native_estimator": "align_mast3r_scale_with_stereo.estimate_motion_from_correspondences",
        "external_ground_truth_used": False,
    }
    if not result.get("accepted"):
        return restored
    displacement = _vec3(result.get("metric_displacement_camera_i_m"), "metric_displacement_camera_i_m")
    rotation = Rotation.from_quat(result["pnp_rotation_quaternion_xyzw"])
    right_rotation = Rotation.from_matrix(MIRROR @ rotation.as_matrix() @ MIRROR)
    right_displacement = MIRROR @ displacement
    restored["metric_displacement_camera_i_m"] = right_displacement.tolist()
    restored["metric_displacement_frame"] = "infrared_right_camera_i"
    restored["pnp_rotation_quaternion_xyzw"] = right_rotation.as_quat().tolist()
    restored["right_centric_motion_source"] = "independent_right_pixels_negative_disparity"
    return restored
