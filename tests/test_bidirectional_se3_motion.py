from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.vio.bidirectional_se3_motion import combine_bidirectional_se3_motion


def obs(displacement, rotation=Rotation.identity(), *, accepted=True, frame="infrared_left_camera_i"):
    return {
        "accepted": accepted,
        "metric_displacement_camera_i_m": list(displacement),
        "metric_displacement_frame": frame,
        "pnp_rotation_quaternion_xyzw": rotation.as_quat().tolist(),
        "pnp_inlier_ratio": 0.8,
    }


def transform_from_motion(rotation, displacement):
    return rotation, -rotation.apply(displacement)


def compose(a, b):
    ra, ta = a
    rb, tb = b
    return ra * rb, ra.apply(tb) + ta


def invert(t):
    rotation, translation = t
    inv = rotation.inv()
    return inv, inv.apply(-translation)


def displacement_from_transform(t):
    rotation, translation = t
    return -rotation.inv().apply(translation)


def assert_motion_close(geometry, rotation, displacement, atol=1e-12):
    actual_rotation = Rotation.from_quat(geometry["pnp_rotation_quaternion_xyzw"])
    assert (actual_rotation.inv() * rotation).magnitude() < atol
    np.testing.assert_allclose(
        geometry["metric_displacement_camera_i_m"],
        displacement,
        atol=atol,
    )


def reverse_observation_from_forward(rotation, displacement):
    # Invert Tf=[Rf,-Rf*df].  Reverse PnP reports Rr Cj->Ci and dr in Cj.
    return obs(-rotation.apply(displacement), rotation.inv())


def test_identity_inverse_exact_returns_forward_geometry_and_zero_closure():
    forward = obs([0.2, -0.1, 0.03])
    reverse = obs([-0.2, 0.1, -0.03])

    geometry, diagnostic = combine_bidirectional_se3_motion(forward, reverse)

    assert geometry["accepted"] is True
    assert geometry["metric_displacement_frame"] == "infrared_left_camera_i"
    assert_motion_close(geometry, Rotation.identity(), [0.2, -0.1, 0.03])
    assert diagnostic["vector_closure_m"] == pytest.approx(0.0)
    assert diagnostic["rotation_closure_angle_rad"] == pytest.approx(0.0)


def test_general_rotation_inverse_exact_returns_forward_motion():
    rotation = Rotation.from_euler("zyx", [20.0, -7.0, 11.0], degrees=True)
    displacement = np.asarray([0.08, -0.02, 0.03])
    forward = obs(displacement, rotation)
    reverse = reverse_observation_from_forward(rotation, displacement)

    geometry, diagnostic = combine_bidirectional_se3_motion(forward, reverse)

    assert_motion_close(geometry, rotation, displacement, atol=1e-12)
    assert diagnostic["rotation_closure_angle_rad"] == pytest.approx(0.0, abs=1e-12)
    assert diagnostic["vector_closure_m"] == pytest.approx(0.0, abs=1e-12)


def test_midpoint_is_true_se3_midpoint_not_vector_average():
    rf = Rotation.from_euler("xyz", [5.0, -8.0, 17.0], degrees=True)
    rr = Rotation.from_euler("zyx", [-3.0, 6.0, -9.0], degrees=True)
    df = np.asarray([0.10, 0.02, -0.04])
    dr = np.asarray([0.07, -0.03, 0.05])
    forward = obs(df, rf)
    reverse = obs(dr, rr)

    geometry, diagnostic = combine_bidirectional_se3_motion(forward, reverse)

    tf = transform_from_motion(rf, df)
    tb = (rr.inv(), dr)
    mid = transform_from_motion(Rotation.from_quat(geometry["pnp_rotation_quaternion_xyzw"]),
                                np.asarray(geometry["metric_displacement_camera_i_m"]))
    left_error = compose(invert(tf), mid)
    right_error = compose(invert(mid), tb)
    assert (left_error[0].inv() * right_error[0]).magnitude() < 1e-12
    np.testing.assert_allclose(left_error[1], right_error[1], atol=1e-12)
    assert diagnostic["se3_midpoint_policy"] == "equal_weight_group_midpoint"


def test_lever_independent_result_does_not_read_extraneous_fields():
    rotation = Rotation.from_euler("z", 15.0, degrees=True)
    displacement = np.asarray([0.03, 0.04, 0.0])
    forward = obs(displacement, rotation)
    reverse = reverse_observation_from_forward(rotation, displacement)
    forward["body_t_camera"] = np.eye(4).tolist()
    reverse["body_t_camera"] = (np.eye(4) * 2.0).tolist()

    geometry, _diagnostic = combine_bidirectional_se3_motion(forward, reverse)

    assert_motion_close(geometry, rotation, displacement, atol=1e-12)


def test_inputs_are_not_mutated():
    forward = obs([0.1, 0.0, 0.0])
    reverse = obs([0.1, 0.0, 0.0])
    before = (dict(forward), dict(reverse))

    combine_bidirectional_se3_motion(forward, reverse)

    assert forward == before[0]
    assert reverse == before[1]


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda f, r: f.__setitem__("accepted", False), "accepted"),
        (lambda f, r: r.__setitem__("accepted", False), "accepted"),
        (lambda f, r: f.__setitem__("metric_displacement_camera_i_m", [np.nan, 0.0, 0.0]), "finite 3-vector"),
        (lambda f, r: f.__setitem__("pnp_rotation_quaternion_xyzw", [0.0, 0.0, 0.0, 0.0]), "unit quaternion"),
        (lambda f, r: r.__setitem__("pnp_rotation_quaternion_xyzw", [0.0, 0.0, 0.0, 2.0]), "unit quaternion"),
        (lambda f, r: r.__setitem__("metric_displacement_frame", "infrared_right_camera_i"), "same camera frame"),
    ],
)
def test_invalid_inputs_rejected(mutate, match):
    forward = obs([0.1, 0.0, 0.0])
    reverse = obs([0.1, 0.0, 0.0])
    mutate(forward, reverse)

    with pytest.raises(ValueError, match=match):
        combine_bidirectional_se3_motion(forward, reverse)


def test_physical_reverse_camera_j_frame_mapping_matches_prompt_equation():
    rf = Rotation.from_euler("y", 30.0, degrees=True)
    df = np.asarray([0.12, 0.01, 0.02])
    forward = obs(df, rf)
    # dr is reverse displacement in camera_j; closure uses df + Rf.T dr.
    dr = -rf.apply(df)
    reverse = obs(dr, Rotation.identity())

    _geometry, diagnostic = combine_bidirectional_se3_motion(forward, reverse)

    assert diagnostic["vector_closure_m"] == pytest.approx(0.0, abs=1e-12)
    np.testing.assert_allclose(
        diagnostic["reverse_inverse_mapped_by_forward_camera_i_m"],
        df,
        atol=1e-12,
    )


def test_swapping_time_direction_inverts_midpoint_motion():
    rf = Rotation.from_euler("xyz", [4.0, 6.0, -12.0], degrees=True)
    rr = Rotation.from_euler("zyx", [3.0, -5.0, 9.0], degrees=True)
    df = np.asarray([0.09, -0.02, 0.01])
    dr = np.asarray([-0.04, 0.03, 0.08])

    geometry, _diag = combine_bidirectional_se3_motion(obs(df, rf), obs(dr, rr))
    swapped, _diag_swapped = combine_bidirectional_se3_motion(obs(dr, rr), obs(df, rf))

    t_mid = transform_from_motion(
        Rotation.from_quat(geometry["pnp_rotation_quaternion_xyzw"]),
        np.asarray(geometry["metric_displacement_camera_i_m"]),
    )
    t_swapped = transform_from_motion(
        Rotation.from_quat(swapped["pnp_rotation_quaternion_xyzw"]),
        np.asarray(swapped["metric_displacement_camera_i_m"]),
    )
    identity = compose(t_mid, t_swapped)
    assert identity[0].magnitude() < 1e-12
    np.testing.assert_allclose(identity[1], [0.0, 0.0, 0.0], atol=1e-12)


def test_near_pi_relative_rotation_is_reported_not_hidden():
    forward = obs([0.1, 0.0, 0.0], Rotation.identity())
    reverse = obs([0.1, 0.0, 0.0], Rotation.from_euler("x", 179.999999, degrees=True))

    geometry, diagnostic = combine_bidirectional_se3_motion(forward, reverse)

    assert geometry["accepted"] is False
    assert geometry["reason"] == "bidirectional_se3_rotation_ambiguous"
    assert "metric_displacement_camera_i_m" not in geometry
    assert diagnostic["near_pi_rotation_ambiguity"] is True
    assert diagnostic["forward_metric_displacement_camera_i_m"] == [0.1, 0.0, 0.0]
