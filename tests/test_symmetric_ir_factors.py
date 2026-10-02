from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.vio.symmetric_ir_factors import build_symmetric_ir_factors


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


def body_t_camera(translation=(0.0, 0.0, 0.0), rotation=None):
    matrix = np.eye(4)
    matrix[:3, :3] = np.eye(3) if rotation is None else rotation
    matrix[:3, 3] = translation
    return matrix


def observation(first, second, times, camera_delta, eye="left", **updates):
    result = {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": float(times[first]),
        "second_t_sec": float(times[second]),
        "metric_displacement_frame": f"infrared_{eye}_camera_i",
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


def track(
    eye,
    *,
    times=None,
    body_positions=None,
    body_rotations=None,
    extrinsic=None,
    observations=None,
    confidences=None,
):
    times = (
        np.array([0.0, 0.02], dtype=float)
        if times is None
        else np.asarray(times, dtype=float)
    )
    body_positions = (
        np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]], dtype=float)
        if body_positions is None
        else np.asarray(body_positions, dtype=float)
    )
    body_rotations = (
        np.repeat(np.eye(3)[None, :, :], times.size, axis=0)
        if body_rotations is None
        else np.asarray(body_rotations, dtype=float)
    )
    extrinsic = body_t_camera() if extrinsic is None else extrinsic
    positions, rotations = poses_from_body(body_positions, body_rotations, extrinsic)
    if observations is None:
        camera_delta = rotations[0].T @ (positions[1] - positions[0])
        observations = [observation(0, 1, times, camera_delta, eye=eye)]
    return {
        "eye": eye,
        "times": times,
        "metric_camera_positions": positions,
        "camera_rotations": rotations,
        "body_t_camera": extrinsic,
        "observations": observations,
        "observation_confidences": [1.0] if confidences is None else confidences,
    }


def build(tracks):
    return build_symmetric_ir_factors(
        np.array([0.0, 0.02], dtype=float),
        np.repeat(np.eye(3)[None, :, :], 2, axis=0),
        tracks,
    )


def canonical_motion(factors):
    return [
        (
            factor["first_index"],
            factor["second_index"],
            factor["eye"],
            tuple(np.round(factor["metric_displacement_world_m"], 12)),
            round(factor["confidence"], 12),
        )
        for factor in factors
    ]


def canonical_stereo(rows):
    return [
        (
            row["first_index"],
            row["second_index"],
            tuple(np.round(row["metric_displacement_camera_i_m"], 12)),
            round(row["pnp_inlier_ratio"], 12),
        )
        for row in rows
    ]


def test_eye_swap_yields_identical_canonical_factors_and_stereo_rows():
    left = track("left", confidences=[0.8])
    right = track("right", confidences=[0.6])

    factors_a, stereo_a, summary_a = build([left, right])
    factors_b, stereo_b, summary_b = build([right, left])

    assert canonical_motion(factors_a) == canonical_motion(factors_b)
    assert canonical_stereo(stereo_a) == canonical_stereo(stereo_b)
    assert summary_a["factor_count"] == summary_b["factor_count"] == 2
    assert (
        summary_a["stereo_observation_count"]
        == summary_b["stereo_observation_count"]
        == 1
    )
    assert sum(factor["confidence"] for factor in factors_a) == pytest.approx(0.8)


def test_independent_track_worlds_and_rigid_lever_pure_rotation_zero_body_delta():
    times = np.array([0.0, 0.02])
    lever = np.array([0.1, 0.0, 0.0])
    body_rotations = np.array([np.eye(3), rotz(90.0)])
    camera_positions = np.array([rotation @ lever for rotation in body_rotations])
    camera_delta = camera_positions[1] - camera_positions[0]
    extrinsic = body_t_camera(lever)
    left = track(
        "left",
        times=times,
        body_positions=np.zeros((2, 3)),
        body_rotations=body_rotations,
        extrinsic=extrinsic,
        observations=[observation(0, 1, times, camera_delta, eye="left")],
        confidences=[0.9],
    )
    right_world_r = roty(33.0)
    right = track(
        "right",
        times=times,
        body_positions=np.zeros((2, 3)),
        body_rotations=right_world_r @ body_rotations,
        extrinsic=extrinsic,
        observations=[observation(0, 1, times, camera_delta, eye="right")],
        confidences=[0.9],
    )

    factors, stereo_rows, summary = build([left, right])

    assert summary["factor_count"] == 2
    for factor in factors:
        np.testing.assert_allclose(
            factor["metric_displacement_world_m"], [0.0, 0.0, 0.0], atol=1e-12
        )
    assert len(stereo_rows) == 1
    np.testing.assert_allclose(
        stereo_rows[0]["metric_displacement_camera_i_m"],
        [0.0, 0.0, 0.0],
        atol=1e-12,
    )


def test_alternate_degradation_suppresses_each_eye_by_own_stereo_residual():
    times = np.array([0.0, 0.02])
    left_bad = track(
        "left",
        body_positions=[[0.0, 0.0, 0.0], [0.116, 0.0, 0.0]],
        observations=[observation(0, 1, times, [0.100, 0.0, 0.0], eye="left")],
        confidences=[1.0],
    )
    right_good = track("right", confidences=[1.0])

    factors, _, _ = build([left_bad, right_good])
    by_eye = {factor["eye"]: factor for factor in factors}

    assert by_eye["left"]["own_stereo_residual_m"] == pytest.approx(0.016)
    assert by_eye["right"]["own_stereo_residual_m"] == pytest.approx(0.0)
    assert by_eye["left"]["own_confidence"] == pytest.approx(0.5)
    assert by_eye["right"]["own_confidence"] == pytest.approx(1.0)
    assert by_eye["left"]["confidence"] < by_eye["right"]["confidence"]
    assert sum(factor["confidence"] for factor in factors) == pytest.approx(1.0)


def test_missing_one_endpoint_lets_other_eye_survive():
    missing = track(
        "left",
        times=[0.2, 0.22],
        observations=[observation(0, 1, [0.2, 0.22], [0.1, 0.0, 0.0], eye="left")],
    )
    right = track("right")

    factors, stereo_rows, summary = build([missing, right])

    assert [factor["eye"] for factor in factors] == ["right"]
    assert len(stereo_rows) == 1
    assert summary["missing_endpoint_count"] == 1


def test_both_bad_confidence_stays_low_and_is_not_boosted_by_normalization():
    times = np.array([0.0, 0.02])
    left = track(
        "left",
        body_positions=[[0.0, 0.0, 0.0], [0.2, 0.0, 0.0]],
        observations=[observation(0, 1, times, [0.1, 0.0, 0.0], eye="left")],
        confidences=[0.2],
    )
    right = track(
        "right",
        body_positions=[[0.0, 0.0, 0.0], [0.2, 0.0, 0.0]],
        observations=[observation(0, 1, times, [0.1, 0.0, 0.0], eye="right")],
        confidences=[0.2],
    )

    factors, _, _ = build([left, right])

    assert [factor["own_confidence"] for factor in factors] == pytest.approx([0.016, 0.016])
    assert [factor["confidence"] for factor in factors] == pytest.approx([0.008, 0.008])
    assert sum(factor["confidence"] for factor in factors) == pytest.approx(0.016)


def test_no_double_count_stereo_and_exact_tie_averages_unbiased_target():
    times = np.array([0.0, 0.02])
    left = track(
        "left",
        observations=[observation(0, 1, times, [0.1, 0.02, 0.0], eye="left")],
        confidences=[0.7],
    )
    right = track(
        "right",
        observations=[observation(0, 1, times, [0.1, -0.02, 0.0], eye="right")],
        confidences=[0.7],
    )

    _, stereo_rows, summary = build([right, left])

    assert summary["stereo_observation_count"] == 1
    assert len(stereo_rows) == 1
    np.testing.assert_allclose(
        stereo_rows[0]["metric_displacement_camera_i_m"],
        [0.1, 0.0, 0.0],
        atol=1e-12,
    )
    assert stereo_rows[0]["pnp_inlier_ratio"] == pytest.approx(0.7)


def test_same_eye_duplicate_rows_do_not_increase_total_vote_or_stereo_rows():
    times = np.array([0.0, 0.02])
    repeated_observations = [
        observation(0, 1, times, [0.1, 0.0, 0.0], eye="left")
        for _ in range(5)
    ]

    single_factors, single_stereo, single_summary = build(
        [track("left", observations=repeated_observations[:1], confidences=[0.8])]
    )
    repeated_factors, repeated_stereo, repeated_summary = build(
        [track("left", observations=repeated_observations, confidences=[0.8] * 5)]
    )

    assert canonical_motion(repeated_factors) == canonical_motion(single_factors)
    assert canonical_stereo(repeated_stereo) == canonical_stereo(single_stereo)
    assert repeated_summary["factor_count"] == single_summary["factor_count"] == 1
    assert (
        repeated_summary["stereo_observation_count"]
        == single_summary["stereo_observation_count"]
        == 1
    )
    assert repeated_summary["duplicate_motion_pair_count"] == 4
    assert repeated_summary["stereo_duplicate_count"] == 4


def test_same_eye_exact_ties_average_targets_independent_of_row_order():
    times = np.array([0.0, 0.02])
    forward = [
        observation(0, 1, times, [0.1, 0.02, 0.0], eye="left"),
        observation(0, 1, times, [0.1, -0.02, 0.0], eye="left"),
    ]
    reverse = list(reversed(forward))

    factors_a, stereo_a, summary_a = build(
        [track("left", observations=forward, confidences=[0.7, 0.7])]
    )
    factors_b, stereo_b, summary_b = build(
        [track("left", observations=reverse, confidences=[0.7, 0.7])]
    )

    assert canonical_motion(factors_a) == canonical_motion(factors_b)
    assert canonical_stereo(stereo_a) == canonical_stereo(stereo_b)
    assert summary_a["factor_count"] == summary_b["factor_count"] == 1
    np.testing.assert_allclose(
        factors_a[0]["metric_displacement_world_m"], [0.1, 0.0, 0.0], atol=1e-12
    )
    np.testing.assert_allclose(
        stereo_a[0]["metric_displacement_camera_i_m"], [0.1, 0.0, 0.0], atol=1e-12
    )


def test_wrong_eye_frame_raises_before_any_frame_conversion():
    wrong = track(
        "left",
        observations=[
            observation(
                0,
                1,
                [0.0, 0.02],
                [0.1, 0.0, 0.0],
                eye="right",
            )
        ],
    )

    with pytest.raises(ValueError, match="infrared_left_camera_i"):
        build([wrong])
