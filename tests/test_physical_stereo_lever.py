from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.vio.physical_stereo_lever import transform_shared_stereo_body_lever  # noqa: E402


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
    matrix[:3, :3] = np.eye(3) if rotation is None else np.asarray(rotation, dtype=float)
    matrix[:3, 3] = np.asarray(translation, dtype=float)
    return matrix


def references():
    times = np.array([0.0, 0.02, 0.04], dtype=float)
    rotations = np.repeat(np.eye(3)[None, :, :], times.size, axis=0)
    return times, rotations


def shared(first=0, second=1, times=None, delta=(999.0, 999.0, 999.0), confidence=0.8):
    if times is None:
        times, _ = references()
    return {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": float(times[first]),
        "second_t_sec": float(times[second]),
        "metric_displacement_camera_i_m": list(delta),
        "metric_displacement_frame": "body_i",
        "scale": 1.0,
        "pnp_inlier_ratio": confidence,
        "rotation_error_deg": 0.0,
    }


def candidate(first=0, second=1, times=None, eye="left", confidence=0.8, d_cam=(0.1, 0.0, 0.0), extrinsic=None):
    if times is None:
        times, _ = references()
    return {
        "eye": eye,
        "first_index": first,
        "second_index": second,
        "first_t_sec": float(times[first]),
        "second_t_sec": float(times[second]),
        "metric_displacement_camera_i_m": list(d_cam),
        "metric_displacement_frame": f"infrared_{eye}_camera_i",
        "observation_confidence": confidence,
        "body_t_camera": body_t_camera() if extrinsic is None else extrinsic,
    }


def test_pure_body_rotation_with_nonzero_lever_produces_zero_body_delta():
    times = np.array([0.0, 0.02], dtype=float)
    rotations = np.array([np.eye(3), rotz(90.0)])
    lever = np.array([0.1, 0.0, 0.0])
    # Physical camera-center displacement from pure body rotation.
    d_cam = rotations[0].T @ ((rotations[1] @ lever) - (rotations[0] @ lever))
    rows, diag = transform_shared_stereo_body_lever(
        times,
        rotations,
        [candidate(0, 1, times, d_cam=d_cam, extrinsic=body_t_camera(lever))],
        [shared(0, 1, times)],
    )

    assert diag["accepted"] is False
    np.testing.assert_allclose(rows[0]["metric_displacement_camera_i_m"], [0.0, 0.0, 0.0], atol=1e-12)


def test_symmetric_average_tie_is_order_invariant_and_preserves_fields():
    times, rotations = references()
    left = candidate(0, 1, times, eye="left", confidence=0.9, d_cam=(0.1, 0.0, 0.0))
    right = candidate(0, 1, times, eye="right", confidence=0.9, d_cam=(0.0, 0.2, 0.0))
    original = [shared(0, 1, times, confidence=0.9)]
    before_rows = [dict(row) for row in original]
    before_candidates = [dict(left), dict(right)]

    rows_a, diag_a = transform_shared_stereo_body_lever(times, rotations, [left, right], original)
    rows_b, diag_b = transform_shared_stereo_body_lever(times, rotations, [right, left], original)

    np.testing.assert_allclose(rows_a[0]["metric_displacement_camera_i_m"], [0.05, 0.1, 0.0], atol=1e-12)
    assert rows_a == rows_b
    assert diag_a["tie_row_count"] == diag_b["tie_row_count"] == 1
    assert original == before_rows
    assert [left, right] == before_candidates
    for key in ("pnp_inlier_ratio", "scale", "rotation_error_deg"):
        assert rows_a[0][key] == original[0][key]


def test_constant_world_equivariance():
    times, rotations = references()
    gauge = roty(30.0)
    raw = [candidate(0, 1, times, d_cam=(0.1, 0.0, 0.0))]
    original = [shared(0, 1, times)]
    rows_a, _ = transform_shared_stereo_body_lever(times, rotations, raw, original)

    rotated_rows, _ = transform_shared_stereo_body_lever(
        times,
        gauge @ rotations,
        raw,
        original,
    )

    np.testing.assert_allclose(
        rotated_rows[0]["metric_displacement_camera_i_m"],
        rotations[0].T @ gauge.T @ gauge @ rotations[0] @ rows_a[0]["metric_displacement_camera_i_m"],
        atol=1e-12,
    )


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda times, rotations, cands, rows: cands[0].__setitem__("first_index", 0.2), "first_index"),
        (lambda times, rotations, cands, rows: rows[0].__setitem__("second_index", 99), "out of range"),
        (lambda times, rotations, cands, rows: cands[0].__setitem__("metric_displacement_frame", "body_i"), "frame"),
        (lambda times, rotations, cands, rows: cands[0].__setitem__("observation_confidence", 1.2), "confidence"),
        (lambda times, rotations, cands, rows: cands[0].__setitem__("metric_displacement_camera_i_m", [np.nan, 0, 0]), "metric_displacement_camera_i_m"),
        (lambda times, rotations, cands, rows: rows[0].__setitem__("metric_displacement_frame", "infrared_left_camera_i"), "body_i"),
        (lambda times, rotations, cands, rows: rows[0].__setitem__("pnp_inlier_ratio", -0.1), "pnp_inlier_ratio"),
        (lambda times, rotations, cands, rows: rows[0].__setitem__("first_t_sec", times[0] + 0.1), "timeline"),
        (lambda times, rotations, cands, rows: (rows[0].__setitem__("second_index", 2), rows[0].__setitem__("second_t_sec", times[2])), "unmatched"),
        (lambda times, rotations, cands, rows: cands[0].__setitem__("second_index", 0), "ordered"),
        (lambda times, rotations, cands, rows: rows[0].__setitem__("accepted", False), "accepted"),
        (lambda times, rotations, cands, rows: (cands[0].__setitem__("second_index", 2), rows[0].__setitem__("second_index", 2), cands[0].__setitem__("second_t_sec", times[2]), rows[0].__setitem__("second_t_sec", times[2]), cands[0].__setitem__("timeline_gap_override", True)), "gap"),
    ],
)
def test_bad_inputs_are_rejected(mutate, message):
    times, rotations = references()
    cands = [candidate(0, 1, times)]
    rows = [shared(0, 1, times)]
    if message == "gap":
        times = np.array([0.0, 0.02, 0.08], dtype=float)
        rotations = np.repeat(np.eye(3)[None, :, :], times.size, axis=0)
        cands = [candidate(0, 1, times)]
        rows = [shared(0, 1, times)]
    mutate(times, rotations, cands, rows)

    with pytest.raises(ValueError, match=message):
        transform_shared_stereo_body_lever(times, rotations, cands, rows)


def test_all_rows_must_match_exact_current_selection():
    times, rotations = references()
    rows = [shared(0, 1, times), shared(1, 2, times)]
    cands = [candidate(0, 1, times)]

    with pytest.raises(ValueError, match="unmatched"):
        transform_shared_stereo_body_lever(times, rotations, cands, rows)


def test_shared_confidence_must_match_selected_candidate():
    times, rotations = references()
    rows = [shared(0, 1, times, confidence=0.7)]
    cands = [candidate(0, 1, times, confidence=0.8)]

    with pytest.raises(ValueError, match="confidence"):
        transform_shared_stereo_body_lever(times, rotations, cands, rows)


def test_duplicate_same_eye_candidates_are_rejected_instead_of_rededuped():
    times, rotations = references()
    first = candidate(0, 1, times, eye="left", confidence=0.8, d_cam=(0.1, 0.0, 0.0))
    second = candidate(0, 1, times, eye="left", confidence=0.9, d_cam=(0.2, 0.0, 0.0))

    with pytest.raises(ValueError, match="duplicate same-eye"):
        transform_shared_stereo_body_lever(
            times,
            rotations,
            [first, second],
            [shared(0, 1, times, confidence=0.9)],
        )


def test_actual_like_offset_timestamps_bind_nearest_reference_without_rewriting_times():
    reference_times = np.array([10.0, 10.033333333, 10.066666667], dtype=float)
    rotations = np.repeat(np.eye(3)[None, :, :], reference_times.size, axis=0)
    raw_first_t = float(reference_times[0] + 0.004)
    raw_second_t = float(reference_times[1] - 0.003)
    raw_candidate = candidate(0, 1, reference_times)
    raw_candidate["first_index"] = 101
    raw_candidate["second_index"] = 106
    raw_candidate["first_t_sec"] = raw_first_t
    raw_candidate["second_t_sec"] = raw_second_t
    row = shared(0, 1, reference_times)
    row["first_t_sec"] = raw_first_t
    row["second_t_sec"] = raw_second_t

    rows, _ = transform_shared_stereo_body_lever(
        reference_times,
        rotations,
        [raw_candidate],
        [row],
    )

    assert rows[0]["first_t_sec"] == raw_first_t
    assert rows[0]["second_t_sec"] == raw_second_t
    np.testing.assert_allclose(
        rows[0]["metric_displacement_camera_i_m"],
        raw_candidate["metric_displacement_camera_i_m"],
        atol=1e-12,
    )


def test_shared_indices_must_match_nearest_reference_pair():
    reference_times = np.array([10.0, 10.033333333, 10.066666667], dtype=float)
    rotations = np.repeat(np.eye(3)[None, :, :], reference_times.size, axis=0)
    raw_candidate = candidate(0, 1, reference_times)
    raw_candidate["first_index"] = 101
    raw_candidate["second_index"] = 106
    raw_candidate["first_t_sec"] = float(reference_times[0] + 0.004)
    raw_candidate["second_t_sec"] = float(reference_times[1] - 0.003)
    row = shared(0, 2, reference_times)
    row["first_t_sec"] = raw_candidate["first_t_sec"]
    row["second_t_sec"] = raw_candidate["second_t_sec"]

    with pytest.raises(ValueError, match="indices"):
        transform_shared_stereo_body_lever(
            reference_times,
            rotations,
            [raw_candidate],
            [row],
        )
