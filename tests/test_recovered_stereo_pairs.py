from copy import deepcopy
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.vio.physical_stereo_lever import transform_shared_stereo_body_lever
from ego_vio.vio.recovered_stereo_pairs import build_recovered_shared_rows


def body_t_camera(x=0.0):
    transform = np.eye(4)
    transform[0, 3] = x
    return transform.tolist()


def candidate(
    eye,
    first,
    second,
    confidence,
    *,
    raw_first=None,
    raw_second=None,
    d=(0.1, 0.0, 0.0),
    t0=None,
    t1=None,
    body_x=0.0,
):
    return {
        "eye": eye,
        "first_index": first if raw_first is None else raw_first,
        "second_index": second if raw_second is None else raw_second,
        "first_t_sec": 0.02 * first if t0 is None else t0,
        "second_t_sec": 0.02 * second if t1 is None else t1,
        "metric_displacement_camera_i_m": list(d),
        "metric_displacement_frame": f"infrared_{eye}_camera_i",
        "observation_confidence": confidence,
        "body_t_camera": body_t_camera(body_x),
        "reference_first_index": first,
        "reference_second_index": second,
    }


def shared_row(first, second, confidence=0.5, **extra):
    row = {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": 0.02 * first,
        "second_t_sec": 0.02 * second,
        "metric_displacement_camera_i_m": [9.0, 9.0, 9.0],
        "metric_displacement_frame": "body_i",
        "scale": 1.0,
        "pnp_inlier_ratio": confidence,
        "rotation_error_deg": 0.0,
    }
    row.update(extra)
    return row


def test_empty_recovery_preserves_original_order_and_refreshes_confidence_without_mutation():
    times = np.asarray([0.0, 0.02, 0.04, 0.06])
    rows = [shared_row(0, 1, 0.2), shared_row(1, 3, 0.8, tag="kept")]
    original_candidates = [
        candidate("left", 0, 1, 0.7),
        candidate("right", 0, 1, 0.6),
        candidate("right", 1, 3, 0.8),
    ]
    rows_before = deepcopy(rows)
    candidates_before = deepcopy(original_candidates)

    templates, all_candidates, diag = build_recovered_shared_rows(
        times, rows, original_candidates, []
    )

    assert rows == rows_before
    assert original_candidates == candidates_before
    assert all_candidates == original_candidates
    assert [row["tag"] for row in templates if "tag" in row] == ["kept"]
    assert [row["pnp_inlier_ratio"] for row in templates] == pytest.approx([0.7, 0.8])
    assert {row["confidence_source"] for row in templates} == {"current_candidate_winner"}
    assert diag["appended_pair_count"] == 0
    assert diag["confidence_changed_original_row_count"] == 1


def test_recovered_left_only_missing_pair_appends_one_template_and_candidate():
    times = np.asarray([0.0, 0.02, 0.04])
    templates, all_candidates, diag = build_recovered_shared_rows(
        times,
        [shared_row(0, 1, 0.4)],
        [candidate("left", 0, 1, 0.4)],
        [candidate("left", 1, 2, 0.9, d=(0.2, 0.0, 0.0))],
    )

    assert len(templates) == 2
    appended = templates[-1]
    assert appended["first_index"] == 1
    assert appended["second_index"] == 2
    assert appended["pnp_inlier_ratio"] == pytest.approx(0.9)
    assert appended["metric_displacement_camera_i_m"] == [0.0, 0.0, 0.0]
    assert appended["recovery_appended"] is True
    assert appended["recovered_eyes"] == ["left"]
    assert appended["confidence_source"] == "recovered_candidate_winner"
    assert len(all_candidates) == 2
    assert diag["appended_pair_count"] == 1
    assert diag["appended_pairs"] == [[1, 2]]


def test_recovered_right_only_missing_pair_appends_one_template():
    times = np.asarray([0.0, 0.02, 0.04])
    templates, all_candidates, _diag = build_recovered_shared_rows(
        times,
        [shared_row(0, 1, 0.4)],
        [candidate("left", 0, 1, 0.4)],
        [candidate("right", 1, 2, 0.75, d=(0.0, 0.2, 0.0))],
    )

    assert templates[-1]["recovered_eyes"] == ["right"]
    assert templates[-1]["pnp_inlier_ratio"] == pytest.approx(0.75)
    assert all_candidates[-1]["eye"] == "right"


def test_same_physical_pair_from_both_eyes_emits_one_template_and_real_transform_can_tie_average():
    times = np.asarray([0.0, 0.02, 0.04])
    rotations = np.repeat(np.eye(3)[None, :, :], len(times), axis=0)
    recovered = [
        candidate("right", 1, 2, 0.9, d=(0.0, 0.4, 0.0)),
        candidate("left", 1, 2, 0.9, d=(0.2, 0.0, 0.0)),
    ]

    templates_a, candidates_a, diag_a = build_recovered_shared_rows(
        times, [shared_row(0, 1, 0.4)], [candidate("left", 0, 1, 0.4)], recovered
    )
    templates_b, candidates_b, diag_b = build_recovered_shared_rows(
        times, [shared_row(0, 1, 0.4)], [candidate("left", 0, 1, 0.4)], list(reversed(recovered))
    )

    assert templates_a == templates_b
    assert diag_a["appended_pair_count"] == diag_b["appended_pair_count"] == 1
    assert templates_a[-1]["recovered_eyes"] == ["left", "right"]
    transformed, report = transform_shared_stereo_body_lever(times, rotations, candidates_a, templates_a)
    np.testing.assert_allclose(transformed[-1]["metric_displacement_camera_i_m"], [0.1, 0.2, 0.0])
    assert report["tie_row_count"] == 1
    transformed_b, _ = transform_shared_stereo_body_lever(times, rotations, candidates_b, templates_b)
    assert transformed_b[-1] == transformed[-1]


def test_recovered_candidate_for_existing_pair_refreshes_confidence_but_does_not_append():
    times = np.asarray([0.0, 0.02])
    templates, all_candidates, diag = build_recovered_shared_rows(
        times,
        [shared_row(0, 1, 0.4)],
        [candidate("left", 0, 1, 0.4)],
        [candidate("right", 0, 1, 0.8)],
    )

    assert len(templates) == 1
    assert templates[0]["pnp_inlier_ratio"] == pytest.approx(0.8)
    assert diag["appended_pair_count"] == 0
    assert len(all_candidates) == 2


def test_same_eye_recovery_keeps_higher_confidence_and_ties_are_deterministic():
    times = np.asarray([0.0, 0.02, 0.04])
    low = candidate("left", 1, 2, 0.6, d=(0.6, 0.0, 0.0))
    high = candidate("left", 1, 2, 0.9, d=(0.9, 0.0, 0.0))
    templates, all_candidates, diag = build_recovered_shared_rows(
        times, [shared_row(0, 1, 0.4)], [candidate("left", 0, 1, 0.4)], [low, high]
    )
    assert templates[-1]["pnp_inlier_ratio"] == pytest.approx(0.9)
    assert all_candidates[-1]["metric_displacement_camera_i_m"] == [0.9, 0.0, 0.0]
    assert diag["deduplicated_recovered_same_eye_count"] == 1

    tie_a = candidate("right", 1, 2, 0.8, d=(0.1, 0.0, 0.0), body_x=0.1)
    tie_b = candidate("right", 1, 2, 0.8, d=(0.1, 0.0, 0.0), body_x=0.2)
    first = build_recovered_shared_rows(
        times, [shared_row(0, 1, 0.4)], [candidate("left", 0, 1, 0.4)], [tie_b, tie_a]
    )
    second = build_recovered_shared_rows(
        times, [shared_row(0, 1, 0.4)], [candidate("left", 0, 1, 0.4)], [tie_a, tie_b]
    )
    assert first[1][-1] == second[1][-1]


def test_duplicate_original_same_eye_or_duplicate_original_pair_rejected():
    times = np.asarray([0.0, 0.02])
    with pytest.raises(ValueError, match="duplicate same-eye"):
        build_recovered_shared_rows(
            times,
            [shared_row(0, 1, 0.4)],
            [candidate("left", 0, 1, 0.4), candidate("left", 0, 1, 0.5)],
            [],
        )
    with pytest.raises(ValueError, match="duplicate original shared pair"):
        build_recovered_shared_rows(
            times,
            [shared_row(0, 1, 0.4), shared_row(0, 1, 0.5)],
            [candidate("left", 0, 1, 0.4)],
            [],
        )


def test_original_candidate_pairs_must_exactly_cover_original_shared_pairs():
    times = np.asarray([0.0, 0.02, 0.04])
    with pytest.raises(ValueError, match="missing original eye candidate"):
        build_recovered_shared_rows(
            times,
            [shared_row(0, 1, 0.4), shared_row(1, 2, 0.5)],
            [candidate("left", 0, 1, 0.4)],
            [],
        )
    with pytest.raises(ValueError, match="absent from original shared rows"):
        build_recovered_shared_rows(
            times,
            [shared_row(0, 1, 0.4)],
            [candidate("left", 0, 1, 0.4), candidate("right", 1, 2, 0.5)],
            [],
        )


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda c: c.__setitem__("observation_confidence", float("nan")), "observation_confidence"),
        (lambda c: c.__setitem__("metric_displacement_camera_i_m", [float("nan"), 0.0, 0.0]), "metric_displacement_camera_i_m"),
        (lambda c: c.__setitem__("reference_second_index", 3), "out of range"),
        (lambda c: c.__setitem__("second_t_sec", 0.20), "reference index/timestamp mismatch"),
        (lambda c: c.__setitem__("second_index", 1), "endpoints must be ordered"),
    ],
)
def test_invalid_recovered_candidate_rejected(mutate, match):
    times = np.asarray([0.0, 0.02, 0.04])
    recovered = candidate("left", 1, 2, 0.8)
    mutate(recovered)
    with pytest.raises(ValueError, match=match):
        build_recovered_shared_rows(
            times,
            [shared_row(0, 1, 0.4)],
            [candidate("left", 0, 1, 0.4)],
            [recovered],
        )


def test_reference_time_gap_rejected():
    times = np.asarray([0.0, 0.02, 0.50])
    with pytest.raises(ValueError, match="interval has a gap"):
        build_recovered_shared_rows(
            times,
            [shared_row(0, 1, 0.4)],
            [candidate("left", 0, 1, 0.4)],
            [candidate("left", 1, 2, 0.8)],
        )
