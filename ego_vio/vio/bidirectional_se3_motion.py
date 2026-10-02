"""SE(3) midpoint for already-accepted bidirectional stereo PnP motions."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation


NEAR_PI_TOLERANCE_RAD = 1e-7


def combine_bidirectional_se3_motion(
    forward: dict[str, Any],
    reverse: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return equal-weight SE(3) midpoint geometry for native bidirectional PnP.

    Forward PnP rotation maps camera_i -> camera_j with displacement ``df`` in
    camera_i, so ``Tf = [Rf, -Rf*df]``.  Reverse PnP rotation maps camera_j ->
    camera_i with displacement ``dr`` in camera_j; its inverse i->j proposal is
    ``Tb = [Rr.T, dr]``.  The returned transform is ``Tf * sqrt(Tf^-1 * Tb)``.
    """

    if forward.get("accepted") is not True or reverse.get("accepted") is not True:
        raise ValueError("bidirectional SE3 midpoint requires both inputs accepted")

    frame = forward.get("metric_displacement_frame")
    if not isinstance(frame, str) or not frame.endswith("_camera_i"):
        raise ValueError("forward metric displacement frame must be a camera_i frame")
    if reverse.get("metric_displacement_frame") != frame:
        raise ValueError("forward and reverse must use the same camera frame label")

    df = _vector3(forward.get("metric_displacement_camera_i_m"), "forward displacement")
    dr = _vector3(reverse.get("metric_displacement_camera_i_m"), "reverse displacement")
    rf = _rotation(forward.get("pnp_rotation_quaternion_xyzw"), "forward rotation")
    rr = _rotation(reverse.get("pnp_rotation_quaternion_xyzw"), "reverse rotation")

    tf_r, tf_t = rf, -rf.apply(df)
    tb_r, tb_t = rr.inv(), dr
    h_r, h_t = _compose(_inverse(tf_r, tf_t), (tb_r, tb_t))
    h_angle = float(h_r.magnitude())
    rotation_closure = rr * rf
    mapped_reverse = -rf.inv().apply(dr)
    vector_closure = df - mapped_reverse
    near_pi_rotation_ambiguity = bool(abs(np.pi - h_angle) <= NEAR_PI_TOLERANCE_RAD)
    diagnostic = {
        "schema": "bidirectional_se3_midpoint_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "se3_midpoint_policy": "equal_weight_group_midpoint",
        "forward_metric_displacement_camera_i_m": df.tolist(),
        "reverse_metric_displacement_camera_j_m": dr.tolist(),
        "forward_rotation_z_ji_quaternion_xyzw": rf.as_quat().tolist(),
        "reverse_rotation_z_ij_quaternion_xyzw": rr.as_quat().tolist(),
        "reverse_inverse_mapped_by_forward_camera_i_m": mapped_reverse.tolist(),
        "vector_closure_m": float(np.linalg.norm(vector_closure)),
        "vector_closure_mm": float(1000.0 * np.linalg.norm(vector_closure)),
        "rotation_closure_quaternion_xyzw": rotation_closure.as_quat().tolist(),
        "rotation_closure_angle_rad": float(rotation_closure.magnitude()),
        "relative_transform_rotation_angle_rad": h_angle,
        "near_pi_rotation_ambiguity": near_pi_rotation_ambiguity,
    }
    if near_pi_rotation_ambiguity:
        return {
            "accepted": False,
            "reason": "bidirectional_se3_rotation_ambiguous",
            "metric_displacement_frame": frame,
            "bidirectional_se3_midpoint": True,
        }, diagnostic

    rh = Rotation.from_rotvec(0.5 * h_r.as_rotvec())
    try:
        vh = np.linalg.solve(np.eye(3) + rh.as_matrix(), h_t)
    except np.linalg.LinAlgError as exc:
        raise ValueError("bidirectional SE3 square-root translation is singular") from exc
    mid_r, mid_t = _compose((tf_r, tf_t), (rh, vh))
    midpoint_displacement = -mid_r.inv().apply(mid_t)

    geometry = {
        "accepted": True,
        "metric_displacement_camera_i_m": midpoint_displacement.tolist(),
        "metric_displacement_frame": frame,
        "pnp_rotation_quaternion_xyzw": mid_r.as_quat().tolist(),
        "bidirectional_se3_midpoint": True,
    }
    return geometry, diagnostic


def _compose(
    left: tuple[Rotation, np.ndarray],
    right: tuple[Rotation, np.ndarray],
) -> tuple[Rotation, np.ndarray]:
    left_r, left_t = left
    right_r, right_t = right
    return left_r * right_r, left_r.apply(right_t) + left_t


def _inverse(rotation: Rotation, translation: np.ndarray) -> tuple[Rotation, np.ndarray]:
    inverse_rotation = rotation.inv()
    return inverse_rotation, inverse_rotation.apply(-translation)


def _vector3(value: Any, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must be a finite 3-vector")
    return vector


def _rotation(value: Any, label: str) -> Rotation:
    quat = np.asarray(value, dtype=float)
    if quat.shape != (4,) or not np.all(np.isfinite(quat)):
        raise ValueError(f"{label} must be a finite unit quaternion")
    norm = float(np.linalg.norm(quat))
    if not np.isclose(norm, 1.0, atol=1e-6, rtol=0.0):
        raise ValueError(f"{label} must be a finite unit quaternion")
    rotation = Rotation.from_quat(quat)
    matrix = rotation.as_matrix()
    if not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-9) or not np.isclose(np.linalg.det(matrix), 1.0, atol=1e-9):
        raise ValueError(f"{label} must be a proper rotation")
    return rotation
