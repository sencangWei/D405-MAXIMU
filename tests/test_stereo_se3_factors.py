from copy import deepcopy
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.vio.stereo_se3_factors import build_shared_stereo_se3_factors  # noqa: E402


def rotz(degrees):
    return Rotation.from_euler("z", degrees, degrees=True).as_matrix()


def roty(degrees):
    return Rotation.from_euler("y", degrees, degrees=True).as_matrix()


def quat_xyzw(matrix):
    return Rotation.from_matrix(matrix).as_quat().tolist()


def references():
    return np.array([0.0, 0.02, 0.04], dtype=float)


def shared(first=0, second=1, times=None, confidence=0.8):
    if times is None:
        times = references()
    return {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": float(times[first]),
        "second_t_sec": float(times[second]),
        "metric_displacement_camera_i_m": [99.0, 99.0, 99.0],
        "metric_displacement_frame": "body_i",
        "scale": 1.0,
        "pnp_inlier_ratio": confidence,
        "rotation_error_deg": 0.0,
    }


def candidate(
    *,
    eye="left",
    first=0,
    second=1,
    times=None,
    confidence=0.8,
    body_r_camera=None,
    body_t_camera=(0.0, 0.0, 0.0),
    body_delta=(0.1, 0.0, 0.0),
    body_relative_rotation=None,
):
    if times is None:
        times = references()
    rbc = np.eye(3) if body_r_camera is None else np.asarray(body_r_camera, dtype=float)
    r_body = np.eye(3) if body_relative_rotation is None else np.asarray(body_relative_rotation, dtype=float)
    d_cam = rbc.T @ np.asarray(body_delta, dtype=float)
    z_camera = rbc.T @ r_body @ rbc
    return {
        "eye": eye,
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": float(times[first]),
        "second_t_sec": float(times[second]),
        "observation_confidence": confidence,
        "metric_displacement_camera_i_m": d_cam.tolist(),
        "metric_displacement_frame": f"infrared_{eye}_camera_i",
        "body_R_camera": rbc.tolist(),
        "body_t_camera_m": list(body_t_camera),
        "pnp_rotation_quaternion_xyzw": quat_xyzw(z_camera),
        "pnp_rotation_mode": "free",
        "pnp_rotation_constrained": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
    }


def test_tied_lr_factor_with_different_levers_matches_dynamic_body_prediction():
    times = references()
    ri = np.eye(3)
    rj = rotz(20.0)
    translation_body_i = np.array([0.2, -0.03, 0.01])
    left_lever = np.array([0.10, 0.0, 0.0])
    right_lever = np.array([-0.04, 0.03, 0.0])
    left_delta = translation_body_i + ri.T @ rj @ left_lever - left_lever
    right_delta = translation_body_i + ri.T @ rj @ right_lever - right_lever
    left = candidate(
        eye="left",
        confidence=0.9,
        body_r_camera=rotz(5.0),
        body_t_camera=left_lever,
        body_delta=left_delta,
        body_relative_rotation=ri.T @ rj,
    )
    right = candidate(
        eye="right",
        confidence=0.9,
        body_r_camera=rotz(-3.0),
        body_t_camera=right_lever,
        body_delta=right_delta,
        body_relative_rotation=ri.T @ rj,
    )

    factors, diag = build_shared_stereo_se3_factors(
        times,
        [right, left],
        [shared(confidence=0.9)],
    )

    factor = factors[0]
    tbar = 0.5 * (left_lever + right_lever)
    expected_delta = translation_body_i + ri.T @ rj @ tbar - tbar
    np.testing.assert_allclose(factor["virtual_body_t_camera_m"], tbar, atol=1e-12)
    np.testing.assert_allclose(factor["metric_displacement_body_i_m"], expected_delta, atol=1e-12)
    np.testing.assert_allclose(factor["rotation_body_j_from_i_matrix"], ri.T @ rj, atol=1e-12)
    assert factor["provenance"]["eyes"] == ["left", "right"]
    assert diag["tie_row_count"] == 1


def test_single_eye_fixed_rotation_matches_body_delta_and_keeps_row_fields():
    times = references()
    expected_body_delta = np.array([0.02, 0.03, -0.01])
    raw = candidate(
        eye="left",
        confidence=0.75,
        body_r_camera=roty(12.0),
        body_t_camera=(0.1, 0.2, 0.0),
        body_delta=expected_body_delta,
        body_relative_rotation=np.eye(3),
    )
    row = shared(confidence=0.75)

    factors, diag = build_shared_stereo_se3_factors(times, [raw], [row])

    assert diag["output_factor_count"] == 1
    assert factors[0]["first_t_sec"] == row["first_t_sec"]
    assert factors[0]["second_t_sec"] == row["second_t_sec"]
    assert factors[0]["confidence"] == row["pnp_inlier_ratio"]
    np.testing.assert_allclose(
        factors[0]["metric_displacement_body_i_m"],
        expected_body_delta,
        atol=1e-12,
    )


def test_eye_order_symmetry_and_world_frame_equivariance_for_so3_mean():
    times = references()
    left = candidate(
        eye="left",
        confidence=0.8,
        body_r_camera=rotz(7.0),
        body_t_camera=(0.1, 0.0, 0.0),
        body_delta=(0.2, 0.0, 0.0),
        body_relative_rotation=rotz(10.0),
    )
    right = candidate(
        eye="right",
        confidence=0.8,
        body_r_camera=roty(5.0),
        body_t_camera=(0.0, 0.2, 0.0),
        body_delta=(0.0, 0.1, 0.0),
        body_relative_rotation=rotz(20.0),
    )
    rows = [shared(confidence=0.8)]

    factors_a, _ = build_shared_stereo_se3_factors(times, [left, right], rows)
    factors_b, _ = build_shared_stereo_se3_factors(times, [right, left], rows)

    assert factors_a == factors_b
    gauge = roty(30.0)
    transformed = []
    for item in (left, right):
        copy = dict(item)
        copy["body_R_camera"] = (gauge @ np.asarray(item["body_R_camera"])).tolist()
        copy["body_t_camera_m"] = (gauge @ np.asarray(item["body_t_camera_m"])).tolist()
        body_delta = gauge @ np.asarray(
            Rotation.from_matrix(np.asarray(item["body_R_camera"])).as_matrix()
            @ np.asarray(item["metric_displacement_camera_i_m"])
        )
        copy["metric_displacement_camera_i_m"] = (
            np.asarray(copy["body_R_camera"]).T @ body_delta
        ).tolist()
        transformed.append(copy)
    factors_g, _ = build_shared_stereo_se3_factors(times, transformed, rows)

    np.testing.assert_allclose(
        factors_g[0]["metric_displacement_body_i_m"],
        gauge @ np.asarray(factors_a[0]["metric_displacement_body_i_m"]),
        atol=1e-12,
    )
    np.testing.assert_allclose(
        factors_g[0]["rotation_body_j_from_i_matrix"],
        gauge @ np.asarray(factors_a[0]["rotation_body_j_from_i_matrix"]) @ gauge.T,
        atol=1e-12,
    )


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda cands, rows, times: cands.append(dict(cands[0])), "duplicate"),
        (lambda cands, rows, times: cands[0].__setitem__("observation_confidence", 0.7), "confidence"),
        (lambda cands, rows, times: rows[0].__setitem__("first_t_sec", float(times[0] + 0.1)), "timestamps"),
        (lambda cands, rows, times: cands[0].__setitem__("pnp_rotation_mode", "trajectory-fixed"), "free"),
        (lambda cands, rows, times: cands[0].__setitem__("pnp_rotation_constrained", True), "unconstrained"),
        (lambda cands, rows, times: cands[0].__setitem__("pnp_rotation_quaternion_xyzw", [np.nan, 0, 0, 1]), "quaternion"),
        (lambda cands, rows, times: cands[0].__setitem__("external_ground_truth_used", True), "GT"),
        (lambda cands, rows, times: cands[0].__setitem__("slam_supervision", True), "supervision"),
        (lambda cands, rows, times: rows[0].__setitem__("accepted", False), "accepted"),
    ],
)
def test_invalid_inputs_are_rejected(mutate, message):
    times = references()
    cands = [candidate(confidence=0.8)]
    rows = [shared(confidence=0.8)]
    mutate(cands, rows, times)

    with pytest.raises(ValueError, match=message):
        build_shared_stereo_se3_factors(times, cands, rows)


def test_gap_rejected_and_no_rows_dropped():
    times = np.array([0.0, 0.02, 0.08], dtype=float)
    cands = [candidate(first=0, second=2, times=times, confidence=0.8)]
    rows = [shared(first=0, second=2, times=times, confidence=0.8)]

    with pytest.raises(ValueError, match="gap"):
        build_shared_stereo_se3_factors(times, cands, rows)


def test_inputs_are_copied_and_missing_row_candidate_rejected():
    times = references()
    cands = [candidate(first=0, second=1, confidence=0.8)]
    rows = [shared(first=0, second=1, confidence=0.8)]
    before_cands = deepcopy(cands)
    before_rows = deepcopy(rows)

    factors, _ = build_shared_stereo_se3_factors(times, cands, rows)

    assert len(factors) == len(rows)
    assert cands == before_cands
    assert rows == before_rows
    with pytest.raises(ValueError, match="missing"):
        build_shared_stereo_se3_factors(
            times,
            cands,
            [shared(first=0, second=1, confidence=0.8), shared(first=1, second=2, confidence=0.8)],
        )
