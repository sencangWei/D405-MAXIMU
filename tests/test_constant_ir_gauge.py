from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.vio.constant_ir_gauge import transform_existing_motion_factors  # noqa: E402


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
    matrix[:3, 3] = np.asarray(translation, dtype=float)
    return matrix


def references():
    times = np.array([0.00, 0.02, 0.04, 0.06], dtype=float)
    rotations = np.repeat(np.eye(3)[None, :, :], times.size, axis=0)
    return times, rotations


def track(eye, *, times=None, positions=None, rotations=None, extrinsic=None):
    times = np.array([0.00, 0.02, 0.04, 0.06], dtype=float) if times is None else np.asarray(times, dtype=float)
    positions = (
        np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.1, 0.2, 0.0], [0.0, 0.2, 0.0]])
        if positions is None
        else np.asarray(positions, dtype=float)
    )
    rotations = (
        np.repeat(np.eye(3)[None, :, :], times.size, axis=0)
        if rotations is None
        else np.asarray(rotations, dtype=float)
    )
    return {
        "eye": eye,
        "times": times,
        "metric_camera_positions": positions,
        "camera_rotations": rotations,
        "body_t_camera": body_t_camera() if extrinsic is None else extrinsic,
    }


def factor(first, second, eye, confidence=0.7):
    return {
        "first_index": first,
        "second_index": second,
        "eye": eye,
        "metric_displacement_world_m": [999.0, 999.0, 999.0],
        "confidence": confidence,
        "own_confidence": 0.5,
        "own_observation_confidence": 0.9,
        "own_stereo_residual_m": 0.001,
    }


def by_key(factors):
    return {(item["first_index"], item["second_index"], item["eye"]): item for item in factors}


def test_constant_gauge_preserves_cycle_closure_and_metadata():
    times, rotations = references()
    motion = [
        factor(0, 1, "left"),
        factor(1, 2, "left"),
        factor(0, 2, "left"),
    ]
    left = track(
        "left",
        positions=[[0, 0, 0], [0.1, 0, 0], [0.1, 0.2, 0], [0, 0.2, 0]],
    )
    before = [dict(item) for item in motion]

    transformed, diagnostic = transform_existing_motion_factors(times, rotations, [left], motion)

    assert motion == before
    assert diagnostic["accepted"] is False
    assert diagnostic["external_ground_truth_used"] is False
    assert diagnostic["slam_supervision"] is False
    assert diagnostic["eye_diagnostics"]["left"]["matched_orientation_count"] == 4
    assert [item["confidence"] for item in transformed] == [0.7, 0.7, 0.7]
    keyed = by_key(transformed)
    np.testing.assert_allclose(
        np.asarray(keyed[(0, 1, "left")]["metric_displacement_world_m"])
        + np.asarray(keyed[(1, 2, "left")]["metric_displacement_world_m"]),
        keyed[(0, 2, "left")]["metric_displacement_world_m"],
        atol=1e-12,
    )


def test_nonzero_lever_pure_body_rotation_produces_zero_body_delta():
    times = np.array([0.0, 0.02], dtype=float)
    reference_rotations = np.array([np.eye(3), rotz(90.0)])
    lever = np.array([0.1, 0.0, 0.0])
    camera_positions = np.array([reference_rotations[0] @ lever, reference_rotations[1] @ lever])
    camera_rotations = reference_rotations.copy()
    left = track(
        "left",
        times=times,
        positions=camera_positions,
        rotations=camera_rotations,
        extrinsic=body_t_camera(lever),
    )
    motion = [factor(0, 1, "left")]

    transformed, diagnostic = transform_existing_motion_factors(
        times,
        reference_rotations,
        [left],
        motion,
    )

    assert diagnostic["eye_diagnostics"]["left"]["proper_rotation"] is True
    np.testing.assert_allclose(
        transformed[0]["metric_displacement_world_m"],
        [0.0, 0.0, 0.0],
        atol=1e-12,
    )


def test_constant_world_equivariance():
    times, rotations = references()
    gauge = roty(30.0)
    left = track("left")
    motion = [factor(0, 1, "left"), factor(1, 2, "left")]
    transformed_a, _ = transform_existing_motion_factors(times, rotations, [left], motion)

    rotated_reference = gauge @ rotations
    rotated_track = track(
        "left",
        positions=np.einsum("ij,nj->ni", gauge, left["metric_camera_positions"]),
        rotations=gauge @ left["camera_rotations"],
    )
    transformed_b, _ = transform_existing_motion_factors(
        times,
        rotated_reference,
        [rotated_track],
        motion,
    )

    for a, b in zip(transformed_a, transformed_b):
        np.testing.assert_allclose(
            b["metric_displacement_world_m"],
            gauge @ np.asarray(a["metric_displacement_world_m"]),
            atol=1e-12,
        )


def test_eye_swap_is_symmetric_and_input_immutable():
    times, rotations = references()
    left = track("left")
    right = track("right", positions=np.asarray(left["metric_camera_positions"]) + [0.0, 0.01, 0.0])
    motion = [factor(0, 1, "left"), factor(0, 1, "right")]
    before_tracks = [
        {key: np.array(value).copy() if key in {"times", "metric_camera_positions", "camera_rotations", "body_t_camera"} else value for key, value in tr.items()}
        for tr in [left, right]
    ]
    before_motion = [dict(item) for item in motion]

    a, diag_a = transform_existing_motion_factors(times, rotations, [left, right], motion)
    b, diag_b = transform_existing_motion_factors(times, rotations, [right, left], list(reversed(motion)))

    assert sorted((item["eye"], tuple(item["metric_displacement_world_m"])) for item in a) == sorted(
        (item["eye"], tuple(item["metric_displacement_world_m"])) for item in b
    )
    assert diag_a["eye_order"] == ["left", "right"]
    assert diag_b["eye_order"] == ["right", "left"]
    assert motion == before_motion
    for original, current in zip(before_tracks, [left, right]):
        for key in ("times", "metric_camera_positions", "camera_rotations", "body_t_camera"):
            np.testing.assert_allclose(current[key], original[key])


def test_static_orientation_is_finite_and_degenerate():
    times, rotations = references()
    left = track("left", positions=np.zeros((4, 3)))
    transformed, diagnostic = transform_existing_motion_factors(
        times,
        rotations,
        [left],
        [factor(0, 1, "left")],
    )

    assert diagnostic["eye_diagnostics"]["left"]["orientation_degenerate"] is True
    assert np.all(np.isfinite(transformed[0]["metric_displacement_world_m"]))


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda times, rotations, tracks, motion: tracks.append(track("left")), "unique"),
        (lambda times, rotations, tracks, motion: tracks[0].__setitem__("eye", "center"), "eye"),
        (lambda times, rotations, tracks, motion: tracks[0].__setitem__("camera_rotations", np.zeros((4, 3, 3))), "rotation"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("first_index", 1.2), "first_index"),
        (lambda times, rotations, tracks, motion: motion.append(dict(motion[0])), "duplicate"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("confidence", 1.2), "confidence"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("own_confidence", -0.1), "own_confidence"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("own_observation_confidence", np.inf), "own_observation_confidence"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("own_stereo_residual_m", -0.001), "own_stereo_residual_m"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("metric_displacement_world_m", [np.nan, 0.0, 0.0]), "metric_displacement_world_m"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("second_index", 0), "ordered"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("eye", "right"), "missing track"),
        (lambda times, rotations, tracks, motion: tracks[0].__setitem__("times", np.array([0.00, 0.04, 0.08, 0.12])), "bind"),
        (lambda times, rotations, tracks, motion: tracks[0].__setitem__("times", np.array([0.01, 0.04, 0.06, 0.08])), "same physical"),
        (lambda times, rotations, tracks, motion: motion[0].__setitem__("second_index", 99), "out of range"),
    ],
)
def test_bad_inputs_are_rejected(mutation, message):
    times, rotations = references()
    tracks = [track("left")]
    motion = [factor(0, 1, "left")]
    mutation(times, rotations, tracks, motion)

    with pytest.raises(ValueError, match=message):
        transform_existing_motion_factors(times, rotations, tracks, motion)


def test_bound_factor_interval_with_gap_is_rejected():
    times = np.array([0.00, 0.02, 0.08, 0.10], dtype=float)
    rotations = np.repeat(np.eye(3)[None, :, :], times.size, axis=0)
    tracks = [track("left", times=times)]
    motion = [factor(0, 2, "left")]

    with pytest.raises(ValueError, match="gap"):
        transform_existing_motion_factors(times, rotations, tracks, motion)
