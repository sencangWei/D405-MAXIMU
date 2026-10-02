from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.vio.learned_segment_reliability import (  # noqa: E402
    apply_learned_segment_reliability,
)


def references(count=80, step=0.1):
    times = np.arange(count, dtype=float) * step
    rotations = np.repeat(np.eye(3)[None, :, :], count, axis=0)
    return times, rotations


def stereo_row(first, second, times, delta=(0.0, 0.0, 0.0), **updates):
    row = {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": float(times[first]),
        "second_t_sec": float(times[second]),
        "metric_displacement_frame": "body_i",
        "metric_displacement_camera_i_m": list(delta),
    }
    row.update(updates)
    return row


def factor(first, second, eye, delta=(0.020, 0.0, 0.0), confidence=1.0, own=0.011):
    return {
        "first_index": first,
        "second_index": second,
        "eye": eye,
        "metric_displacement_world_m": list(delta),
        "confidence": confidence,
        "own_confidence": confidence,
        "own_observation_confidence": confidence,
        "own_stereo_residual_m": own,
    }


def paired_factor(first, second, *, left_delta=(0.020, 0.0, 0.0), right_delta=(0.020, 0.0, 0.0), confidence=1.0, own=0.011):
    return [
        factor(first, second, "left", left_delta, confidence=confidence, own=own),
        factor(first, second, "right", right_delta, confidence=confidence, own=own),
    ]


def test_duration_registered_rule_zeroes_only_long_paired_edges_and_reports_windows():
    times, rotations = references()
    shared = []
    motion = []
    affected_pairs = []
    for offset in range(8):
        first = 10 + offset
        second = first + 12
        affected_pairs.append((first, second))
        shared.append(stereo_row(first, second, times))
        motion.extend(paired_factor(first, second))

    short_pair = (18, 19)
    shared.append(stereo_row(*short_pair, times))
    motion.extend(paired_factor(*short_pair))

    solo_pair = (24, 36)
    shared.append(stereo_row(*solo_pair, times))
    motion.append(factor(*solo_pair, "left"))

    before = [dict(item) for item in motion]
    filtered, diagnostic = apply_learned_segment_reliability(
        times,
        rotations,
        motion,
        shared,
    )

    assert motion == before
    assert len(filtered) == len(motion)
    assert diagnostic["accepted"] is False
    assert diagnostic["external_ground_truth_used"] is False
    assert diagnostic["slam_supervision"] is False
    assert diagnostic["status"] == "EXPERIMENTAL_NOT_ACCEPTED"
    assert diagnostic["rule"]["duration_min_sec"] == 1.0
    assert diagnostic["hit_window_count"] > 0
    assert diagnostic["affected_pair_count"] == 8
    assert diagnostic["zeroed_factor_count"] == 16
    assert diagnostic["unpaired_factor_count"] == 1

    by_key = {
        (item["first_index"], item["second_index"], item["eye"]): item
        for item in filtered
    }
    for first, second in affected_pairs:
        assert by_key[(first, second, "left")]["confidence"] == 0.0
        assert by_key[(first, second, "right")]["confidence"] == 0.0
    for eye in ("left", "right"):
        assert by_key[(*short_pair, eye)]["confidence"] == pytest.approx(1.0)
    assert by_key[(*solo_pair, "left")]["confidence"] == pytest.approx(1.0)


def test_rule_uses_final_confidence_not_own_observation_confidence():
    times, rotations = references()
    shared = []
    motion = []
    for offset in range(8):
        first = 10 + offset
        second = first + 12
        shared.append(stereo_row(first, second, times))
        # Final confidence gives the right eye all graph weight on the good delta.
        left = factor(first, second, "left", delta=(0.020, 0.0, 0.0), confidence=0.0, own=0.011)
        right = factor(first, second, "right", delta=(0.0, 0.0, 0.0), confidence=1.0, own=0.011)
        # Deliberately make own_observation_confidence disagree with graph confidence.
        left["own_observation_confidence"] = 1.0
        right["own_observation_confidence"] = 0.0
        motion.extend([left, right])

    filtered, diagnostic = apply_learned_segment_reliability(times, rotations, motion, shared)

    assert diagnostic["hit_window_count"] == 0
    assert diagnostic["zeroed_factor_count"] == 0
    assert [item["confidence"] for item in filtered] == [item["confidence"] for item in motion]


def test_left_right_swap_is_symmetric():
    times, rotations = references()
    shared = []
    motion = []
    for offset in range(8):
        first = 10 + offset
        second = first + 12
        shared.append(stereo_row(first, second, times))
        motion.extend(
            paired_factor(
                first,
                second,
                left_delta=(0.021, 0.0, 0.0),
                right_delta=(0.019, 0.0, 0.0),
                confidence=0.7,
            )
        )

    swapped = [
        {**item, "eye": "right" if item["eye"] == "left" else "left"}
        for item in reversed(motion)
    ]
    filtered_a, diagnostic_a = apply_learned_segment_reliability(times, rotations, motion, shared)
    filtered_b, diagnostic_b = apply_learned_segment_reliability(times, rotations, swapped, shared)

    keys_a = sorted((item["first_index"], item["second_index"], item["eye"], item["confidence"]) for item in filtered_a)
    keys_b = sorted((item["first_index"], item["second_index"], item["eye"], item["confidence"]) for item in filtered_b)
    assert keys_a == keys_b
    assert diagnostic_a["affected_pair_count"] == diagnostic_b["affected_pair_count"] == 8


def test_no_long_paired_edges_returns_unchanged_copy_with_diagnostic():
    times, rotations = references()
    shared = [stereo_row(10, 11, times)]
    motion = paired_factor(10, 11)

    filtered, diagnostic = apply_learned_segment_reliability(times, rotations, motion, shared)

    assert filtered == motion
    assert filtered is not motion
    assert diagnostic["long_paired_edge_count"] == 0
    assert diagnostic["hit_window_count"] == 0
    assert diagnostic["zeroed_factor_count"] == 0


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda times, rotations, motion, shared: shared[0].__setitem__("metric_displacement_frame", "camera_i"), "body_i"),
        (lambda times, rotations, motion, shared: motion.append(dict(motion[0])), "duplicate"),
        (lambda times, rotations, motion, shared: motion[0].__setitem__("confidence", float("nan")), "finite"),
        (lambda times, rotations, motion, shared: motion[0].__setitem__("confidence", 1.1), "confidence"),
        (lambda times, rotations, motion, shared: shared[0].__setitem__("second_index", len(times)), "index"),
        (lambda times, rotations, motion, shared: shared.clear(), "missing stereo"),
        (lambda times, rotations, motion, shared: shared[0].pop("accepted"), "missing stereo"),
        (lambda times, rotations, motion, shared: shared[0].__setitem__("first_t_sec", times[10] + 0.1), "reference"),
    ],
)
def test_invalid_inputs_are_rejected(mutation, message):
    times, rotations = references()
    shared = [stereo_row(10, 22, times)]
    motion = paired_factor(10, 22)
    mutation(times, rotations, motion, shared)

    with pytest.raises(ValueError, match=message):
        apply_learned_segment_reliability(times, rotations, motion, shared)


@pytest.mark.parametrize(
    "target, value",
    [
        ("stereo", 10.9),
        ("stereo", "10"),
        ("stereo", True),
        ("factor", 10.9),
        ("factor", "10"),
        ("factor", True),
    ],
)
def test_indices_must_be_integer_and_not_bool(target, value):
    times, rotations = references()
    shared = [stereo_row(10, 22, times)]
    motion = paired_factor(10, 22)
    if target == "stereo":
        shared[0]["first_index"] = value
    else:
        motion[0]["first_index"] = value

    with pytest.raises(ValueError, match="first_index"):
        apply_learned_segment_reliability(times, rotations, motion, shared)


def test_invalid_reference_rotation_is_rejected():
    times, rotations = references()
    rotations[0, 0, 0] = 2.0
    shared = [stereo_row(10, 22, times)]
    motion = paired_factor(10, 22)

    with pytest.raises(ValueError, match="rotation"):
        apply_learned_segment_reliability(times, rotations, motion, shared)


def test_solo_factor_without_shared_stereo_is_rejected():
    times, rotations = references()
    shared = [stereo_row(10, 22, times)]
    motion = paired_factor(10, 22)
    motion.append(factor(30, 42, "left"))

    with pytest.raises(ValueError, match="missing stereo"):
        apply_learned_segment_reliability(times, rotations, motion, shared)
