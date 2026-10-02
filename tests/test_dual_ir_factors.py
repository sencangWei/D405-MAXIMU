from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.vio.dual_ir_factors import build_secondary_visual_factors


def rotz(degrees):
    radians = np.deg2rad(degrees)
    c = np.cos(radians)
    s = np.sin(radians)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def roty(degrees):
    radians = np.deg2rad(degrees)
    c = np.cos(radians)
    s = np.sin(radians)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rotx(degrees):
    radians = np.deg2rad(degrees)
    c = np.cos(radians)
    s = np.sin(radians)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def body_t_camera(translation=(0.0, 0.0, 0.0), rotation=None):
    matrix = np.eye(4)
    matrix[:3, :3] = np.eye(3) if rotation is None else rotation
    matrix[:3, 3] = translation
    return matrix


def observation(first, second, times, camera_delta, **updates):
    result = {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": float(times[first]),
        "second_t_sec": float(times[second]),
        "metric_displacement_frame": "infrared_right_camera_i",
        "metric_displacement_camera_i_m": list(camera_delta),
    }
    result.update(updates)
    return result


def poses_from_body(body_positions, world_r_body, extrinsic):
    body_r_camera = extrinsic[:3, :3]
    body_t_cam = extrinsic[:3, 3]
    camera_rotations = world_r_body @ body_r_camera
    camera_positions = body_positions + np.einsum("nij,j->ni", world_r_body, body_t_cam)
    return camera_positions, camera_rotations


def build(
    *,
    primary_times=None,
    primary_rotations=None,
    secondary_times=None,
    secondary_positions=None,
    secondary_rotations=None,
    extrinsic=None,
    primary_positions=None,
    primary_extrinsic=None,
    observations=None,
    confidences=None,
):
    return build_secondary_visual_factors(
        np.array([0.0, 0.02], dtype=float) if primary_times is None else primary_times,
        np.repeat(np.eye(3)[None, :, :], 2, axis=0)
        if primary_rotations is None
        else primary_rotations,
        np.array([0.0, 0.02], dtype=float) if secondary_times is None else secondary_times,
        np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]], dtype=float)
        if secondary_positions is None
        else secondary_positions,
        np.repeat(np.eye(3)[None, :, :], 2, axis=0)
        if secondary_rotations is None
        else secondary_rotations,
        body_t_camera() if extrinsic is None else extrinsic,
        [observation(0, 1, [0.0, 0.02], [0.1, 0.0, 0.0])]
        if observations is None
        else observations,
        [1.0] if confidences is None else confidences,
        primary_metric_camera_positions=primary_positions,
        primary_body_t_camera=primary_extrinsic,
    )


def test_rotation_and_baseline_lever_compensation_pure_body_rotation_zero_delta():
    times = np.array([0.0, 0.02])
    lever = np.array([0.10, 0.0, 0.0])
    body_rotations = np.array([np.eye(3), rotz(90.0)])
    camera_positions = np.array([rotation @ lever for rotation in body_rotations])
    observed_camera_delta = camera_positions[1] - camera_positions[0]

    factors, summary = build(
        secondary_times=times,
        secondary_positions=camera_positions,
        secondary_rotations=body_rotations,
        extrinsic=body_t_camera(lever),
        observations=[observation(0, 1, times, observed_camera_delta)],
        confidences=[0.9],
    )

    assert summary["factor_count"] == 1
    np.testing.assert_allclose(
        factors[0]["metric_displacement_world_m"], [0.0, 0.0, 0.0], atol=1e-12
    )
    assert factors[0]["secondary_stereo_residual_m"] == pytest.approx(0.0)
    assert factors[0]["confidence"] == pytest.approx(0.9)


def test_secondary_world_rotation_and_translation_leave_factor_invariant():
    times = np.array([0.0, 0.02])
    extrinsic = body_t_camera(
        translation=(0.08, -0.03, 0.02),
        rotation=rotx(7.0) @ roty(-5.0),
    )
    secondary_body_rotations = np.array([rotz(30.0), rotz(47.0)])
    secondary_body_positions = np.array([[1.0, 2.0, 0.3], [1.2, 2.1, 0.35]])
    secondary_positions, secondary_rotations = poses_from_body(
        secondary_body_positions, secondary_body_rotations, extrinsic
    )
    camera_delta = secondary_rotations[0].T @ (
        secondary_positions[1] - secondary_positions[0]
    )
    primary_rotations = np.array([roty(11.0), roty(11.0) @ rotz(4.0)])
    obs = [observation(0, 1, times, camera_delta)]
    primary_extrinsic = body_t_camera(
        translation=(-0.03, 0.02, 0.04),
        rotation=rotx(3.0) @ roty(4.0),
    )
    primary_body_positions = np.array([[0.0, 0.0, 0.0], [0.3, -0.1, 0.2]])
    primary_positions, _ = poses_from_body(
        primary_body_positions, primary_rotations, primary_extrinsic
    )
    primary_positions[1] += np.array([0.012, -0.006, 0.004])

    base_factors, _ = build(
        primary_rotations=primary_rotations,
        primary_positions=primary_positions,
        primary_extrinsic=primary_extrinsic,
        secondary_times=times,
        secondary_positions=secondary_positions,
        secondary_rotations=secondary_rotations,
        extrinsic=extrinsic,
        observations=obs,
        confidences=[1.0],
    )

    secondary_world_r_new = roty(25.0)
    secondary_world_t_new = np.array([4.0, -2.0, 0.7])
    transformed_positions = (
        secondary_world_r_new @ secondary_positions.T
    ).T + secondary_world_t_new
    transformed_secondary_rotations = secondary_world_r_new @ secondary_rotations

    transformed_factors, _ = build(
        primary_rotations=primary_rotations,
        primary_positions=primary_positions,
        primary_extrinsic=primary_extrinsic,
        secondary_times=times,
        secondary_positions=transformed_positions,
        secondary_rotations=transformed_secondary_rotations,
        extrinsic=extrinsic,
        observations=obs,
        confidences=[1.0],
    )

    np.testing.assert_allclose(
        transformed_factors[0]["metric_displacement_world_m"],
        base_factors[0]["metric_displacement_world_m"],
        atol=1e-12,
    )


def test_primary_world_rotation_equivariance_and_translation_irrelevance():
    times = np.array([0.0, 0.02])
    extrinsic = body_t_camera(
        translation=(0.04, 0.03, -0.02),
        rotation=rotx(-6.0) @ roty(3.0),
    )
    secondary_body_rotations = np.array([rotz(-12.0), rotz(15.0)])
    secondary_body_positions = np.array([[0.4, -0.2, 0.1], [0.55, -0.05, 0.18]])
    secondary_positions, secondary_rotations = poses_from_body(
        secondary_body_positions, secondary_body_rotations, extrinsic
    )
    camera_delta = secondary_rotations[0].T @ (
        secondary_positions[1] - secondary_positions[0]
    )
    primary_rotations = np.array([roty(18.0), roty(18.0)])
    obs = [observation(0, 1, times, camera_delta)]
    primary_extrinsic = body_t_camera(
        translation=(-0.02, 0.05, 0.01),
        rotation=rotx(2.0) @ roty(-8.0),
    )
    primary_body_positions = np.array([[0.1, 0.2, -0.2], [0.2, 0.4, -0.1]])
    primary_positions, _ = poses_from_body(
        primary_body_positions, primary_rotations, primary_extrinsic
    )
    primary_positions[1] += np.array([0.010, 0.0, -0.005])

    base_factors, _ = build(
        primary_rotations=primary_rotations,
        primary_positions=primary_positions,
        primary_extrinsic=primary_extrinsic,
        secondary_times=times,
        secondary_positions=secondary_positions,
        secondary_rotations=secondary_rotations,
        extrinsic=extrinsic,
        observations=obs,
        confidences=[1.0],
    )

    primary_world_r_new = rotx(20.0) @ rotz(10.0)
    rotated_factors, _ = build(
        primary_rotations=primary_world_r_new @ primary_rotations,
        primary_positions=(primary_world_r_new @ primary_positions.T).T
        + np.array([5.0, -3.0, 2.0]),
        primary_extrinsic=primary_extrinsic,
        secondary_times=times,
        secondary_positions=secondary_positions,
        secondary_rotations=secondary_rotations,
        extrinsic=extrinsic,
        observations=obs,
        confidences=[1.0],
    )

    np.testing.assert_allclose(
        rotated_factors[0]["metric_displacement_world_m"],
        primary_world_r_new @ np.asarray(base_factors[0]["metric_displacement_world_m"]),
        atol=1e-12,
    )


def test_primary_biased_and_accurate_right_gets_positive_local_advantage():
    times = np.array([0.0, 0.02])
    primary_extrinsic = body_t_camera((0.03, -0.02, 0.01), rotx(4.0))
    secondary_extrinsic = body_t_camera((0.08, 0.01, -0.02), roty(-3.0))
    rotations = np.array([rotz(5.0), rotz(5.0)])
    body_positions = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]])
    secondary_positions, secondary_rotations = poses_from_body(
        body_positions, rotations, secondary_extrinsic
    )
    primary_positions, _ = poses_from_body(body_positions, rotations, primary_extrinsic)
    primary_positions[1] += np.array([0.020, 0.0, 0.0])
    camera_delta = secondary_rotations[0].T @ (
        secondary_positions[1] - secondary_positions[0]
    )

    factors, summary = build(
        primary_rotations=rotations,
        primary_positions=primary_positions,
        primary_extrinsic=primary_extrinsic,
        secondary_times=times,
        secondary_positions=secondary_positions,
        secondary_rotations=secondary_rotations,
        extrinsic=secondary_extrinsic,
        observations=[observation(0, 1, times, camera_delta)],
        confidences=[0.6],
    )

    assert summary["preferred_count"] == 1
    assert summary["right_disabled_count"] == 0
    assert len(factors) == 1
    assert factors[0]["primary_stereo_residual_m"] == pytest.approx(0.020)
    assert factors[0]["secondary_stereo_residual_m"] == pytest.approx(0.0, abs=1e-12)
    assert 0.0 < factors[0]["local_advantage"] < 1.0
    assert factors[0]["confidence"] == pytest.approx(
        0.6 * factors[0]["local_advantage"]
    )


@pytest.mark.parametrize("primary_bias", [0.0, 0.004])
def test_right_worse_or_equal_to_primary_disables_factor(primary_bias):
    times = np.array([0.0, 0.02])
    primary_extrinsic = body_t_camera((0.01, 0.02, 0.03), rotx(2.0))
    secondary_extrinsic = body_t_camera((0.04, -0.01, 0.02), roty(5.0))
    rotations = np.array([np.eye(3), np.eye(3)])
    body_positions = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]])
    secondary_positions, secondary_rotations = poses_from_body(
        body_positions, rotations, secondary_extrinsic
    )
    primary_positions, _ = poses_from_body(body_positions, rotations, primary_extrinsic)
    primary_positions[1] += np.array([primary_bias, 0.0, 0.0])
    observed_delta = secondary_rotations[0].T @ (
        secondary_positions[1] - secondary_positions[0] + np.array([0.008, 0.0, 0.0])
    )

    factors, summary = build(
        primary_rotations=rotations,
        primary_positions=primary_positions,
        primary_extrinsic=primary_extrinsic,
        secondary_times=times,
        secondary_positions=secondary_positions,
        secondary_rotations=secondary_rotations,
        extrinsic=secondary_extrinsic,
        observations=[observation(0, 1, times, observed_delta)],
        confidences=[1.0],
    )

    assert factors == []
    assert summary["right_disabled_count"] == 1
    assert summary["preferred_count"] == 0


def test_tiny_primary_bias_yields_continuous_small_local_advantage():
    times = np.array([0.0, 0.02])
    primary_extrinsic = body_t_camera((0.02, -0.02, 0.01), rotx(-2.0))
    secondary_extrinsic = body_t_camera((0.05, 0.0, -0.01), roty(2.0))
    rotations = np.array([np.eye(3), np.eye(3)])
    body_positions = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]])
    secondary_positions, secondary_rotations = poses_from_body(
        body_positions, rotations, secondary_extrinsic
    )
    primary_positions, _ = poses_from_body(body_positions, rotations, primary_extrinsic)
    primary_positions[1] += np.array([0.001, 0.0, 0.0])
    camera_delta = secondary_rotations[0].T @ (
        secondary_positions[1] - secondary_positions[0]
    )

    factors, _ = build(
        primary_rotations=rotations,
        primary_positions=primary_positions,
        primary_extrinsic=primary_extrinsic,
        secondary_times=times,
        secondary_positions=secondary_positions,
        secondary_rotations=secondary_rotations,
        extrinsic=secondary_extrinsic,
        observations=[observation(0, 1, times, camera_delta)],
        confidences=[1.0],
    )

    assert len(factors) == 1
    assert factors[0]["primary_stereo_residual_m"] == pytest.approx(0.001)
    assert factors[0]["local_advantage"] == pytest.approx(
        0.001**2 / (0.001**2 + 0.008**2)
    )


def test_biased_right_motion_gets_lower_confidence_without_left_position_comparison():
    times = np.array([0.0, 0.02])
    observations = [observation(0, 1, times, [0.100, 0.0, 0.0])]

    clean, _ = build(observations=observations, confidences=[0.8])
    biased, _ = build(
        secondary_positions=np.array([[0.0, 0.0, 0.0], [0.116, 0.0, 0.0]]),
        observations=observations,
        confidences=[0.8],
    )

    assert clean[0]["confidence"] == pytest.approx(0.8)
    assert biased[0]["secondary_stereo_residual_m"] == pytest.approx(0.016)
    assert biased[0]["confidence"] == pytest.approx(0.4)


def test_missing_primary_endpoint_and_sample_gaps_are_skipped():
    times = np.array([0.0, 0.02])
    missing_factors, missing_summary = build(
        primary_times=np.array([0.20, 0.22]),
        observations=[observation(0, 1, times, [0.1, 0.0, 0.0])],
        confidences=[1.0],
    )
    assert missing_factors == []
    assert missing_summary["missing_endpoint_count"] == 1

    gap_factors, gap_summary = build(
        primary_times=np.array([0.0, 0.10]),
        primary_rotations=np.repeat(np.eye(3)[None, :, :], 2, axis=0),
        secondary_times=np.array([0.0, 0.10]),
        secondary_positions=np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]]),
        secondary_rotations=np.repeat(np.eye(3)[None, :, :], 2, axis=0),
        observations=[observation(0, 1, [0.0, 0.10], [0.1, 0.0, 0.0])],
        confidences=[1.0],
    )
    assert gap_factors == []
    assert gap_summary["gap_rejected_count"] == 1


def test_duplicate_primary_pair_keeps_highest_confidence_measurement():
    times = np.array([0.0, 0.02])
    observations = [
        observation(0, 1, times, [0.108, 0.0, 0.0]),
        observation(0, 1, times, [0.1, 0.0, 0.0]),
    ]

    factors, summary = build(observations=observations, confidences=[1.0, 0.7])

    assert len(factors) == 1
    assert summary["duplicate_count"] == 1
    assert summary["factor_count"] == 1
    assert factors[0]["confidence"] == pytest.approx(1.0)
    assert factors[0]["secondary_stereo_residual_m"] == pytest.approx(0.008)


def test_rejected_missing_and_zero_confidence_observations_skip():
    times = np.array([0.0, 0.02])
    factors, summary = build(
        observations=[
            None,
            {"accepted": False},
            observation(0, 1, times, [0.1, 0.0, 0.0]),
        ],
        confidences=[0.5, 0.5, 0.0],
    )

    assert factors == []
    assert summary["missing_observation_count"] == 1
    assert summary["rejected_observation_count"] == 1
    assert summary["zero_confidence_count"] == 1


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"primary_times": np.array([0.0, 0.0])}, "strictly monotonic"),
        ({"confidences": [1.2]}, r"\[0, 1\]"),
        ({"extrinsic": np.diag([2.0, 1.0, 1.0, 1.0])}, "orthonormal"),
        ({"primary_positions": np.zeros((2, 3))}, "supplied together"),
        ({"primary_extrinsic": np.eye(4)}, "supplied together"),
        (
            {
                "primary_positions": np.zeros((1, 3)),
                "primary_extrinsic": np.eye(4),
            },
            "primary_metric_camera_positions",
        ),
        (
            {
                "observations": [
                    observation(
                        0,
                        1,
                        [0.0, 0.02],
                        [0.1, 0.0, 0.0],
                        metric_displacement_frame="infrared_left_camera_i",
                    )
                ]
            },
            "infrared_right_camera_i",
        ),
        (
            {
                "observations": [
                    observation(1, 0, [0.0, 0.02], [0.1, 0.0, 0.0])
                ]
            },
            "not ordered",
        ),
        (
            {
                "observations": [
                    observation(
                        0,
                        1,
                        [0.0, 0.02],
                        [0.1, 0.0, 0.0],
                        first_t_sec=0.2,
                    )
                ]
            },
            "bind timestamps",
        ),
    ],
)
def test_invalid_data_rejected(kwargs, match):
    with pytest.raises(ValueError, match=match):
        build(**kwargs)
