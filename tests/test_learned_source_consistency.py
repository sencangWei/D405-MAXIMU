from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.vio.learned_source_consistency import sync_existing_factor_source_metadata


SYNC_FIELDS = (
    "own_stereo_residual_m",
    "own_observation_confidence",
    "own_confidence",
    "confidence",
)


def factor(eye="left", first=0, second=1, target=(1.0, 2.0, 3.0), **fields):
    row = {
        "eye": eye,
        "first_index": first,
        "second_index": second,
        "target_position_delta_m": list(target),
        "target_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
        "own_stereo_residual_m": 0.001,
        "own_observation_confidence": 0.9,
        "own_confidence": 0.8,
        "confidence": 0.7,
        "extra": {"kept": True},
    }
    row.update(fields)
    return row


def test_empty_original_returns_empty_copy_and_diagnostic():
    synced, diag = sync_existing_factor_source_metadata([], [])

    assert synced == []
    assert diag["factor_count"] == 0
    assert diag["updated_field_count"] == 0
    assert diag["external_ground_truth_used"] is False


def test_original_metadata_exact_when_source_same_and_inputs_not_mutated():
    original = [factor("left", 0, 1), factor("right", 1, 3, target=(3.0, 2.0, 1.0))]
    rebuilt = deepcopy(original)
    original_before = deepcopy(original)
    rebuilt_before = deepcopy(rebuilt)

    synced, diag = sync_existing_factor_source_metadata(original, rebuilt)

    assert synced == original
    assert synced is not original
    assert original == original_before
    assert rebuilt == rebuilt_before
    assert diag["changed_factor_count"] == 0


def test_left_and_right_source_metadata_changes_preserve_targets_and_order():
    original = [
        factor("left", 0, 1, target=(1.0, 0.0, 0.0)),
        factor("right", 0, 1, target=(0.0, 1.0, 0.0)),
    ]
    rebuilt = [
        factor(
            "right",
            0,
            1,
            target=(99.0, 99.0, 99.0),
            own_stereo_residual_m=0.003,
            own_observation_confidence=0.6,
            own_confidence=0.5,
            confidence=0.4,
        ),
        factor(
            "left",
            0,
            1,
            target=(88.0, 88.0, 88.0),
            own_stereo_residual_m=0.002,
            own_observation_confidence=0.7,
            own_confidence=0.65,
            confidence=0.55,
        ),
    ]

    synced, diag = sync_existing_factor_source_metadata(original, rebuilt)

    assert [row["eye"] for row in synced] == ["left", "right"]
    assert synced[0]["target_position_delta_m"] == [1.0, 0.0, 0.0]
    assert synced[1]["target_position_delta_m"] == [0.0, 1.0, 0.0]
    assert [synced[0][field] for field in SYNC_FIELDS] == pytest.approx([0.002, 0.7, 0.65, 0.55])
    assert [synced[1][field] for field in SYNC_FIELDS] == pytest.approx([0.003, 0.6, 0.5, 0.4])
    assert diag["changed_factor_count"] == 2
    assert diag["updated_field_count"] == 8


def test_permuted_rebuilt_source_order_keeps_original_output_order():
    original = [factor("left", 0, 1), factor("right", 1, 2), factor("left", 2, 3)]
    rebuilt = [
        factor("left", 2, 3, confidence=0.1),
        factor("left", 0, 1, confidence=0.2),
        factor("right", 1, 2, confidence=0.3),
    ]

    synced, _diag = sync_existing_factor_source_metadata(original, rebuilt)

    assert [(row["eye"], row["first_index"], row["second_index"]) for row in synced] == [
        ("left", 0, 1),
        ("right", 1, 2),
        ("left", 2, 3),
    ]
    assert [row["confidence"] for row in synced] == pytest.approx([0.2, 0.3, 0.1])


@pytest.mark.parametrize(
    "original,rebuilt,match",
    [
        ([factor("left", 0, 1)], [], "keyset"),
        ([factor("left", 0, 1)], [factor("left", 0, 1), factor("right", 1, 2)], "keyset"),
        ([factor("left", 0, 1), factor("left", 0, 1)], [factor("left", 0, 1)], "duplicate"),
        ([factor("left", 0, 1)], [factor("left", 0, 1), factor("left", 0, 1)], "duplicate"),
    ],
)
def test_missing_extra_or_duplicate_keys_rejected(original, rebuilt, match):
    with pytest.raises(ValueError, match=match):
        sync_existing_factor_source_metadata(original, rebuilt)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda row: row.__setitem__("eye", "center"), "eye"),
        (lambda row: row.__setitem__("first_index", True), "first_index"),
        (lambda row: row.__setitem__("first_index", -1), "first_index"),
        (lambda row: row.__setitem__("second_index", 0), "ordered"),
        (lambda row: row.__setitem__("own_stereo_residual_m", float("nan")), "own_stereo_residual_m"),
        (lambda row: row.__setitem__("own_stereo_residual_m", -0.1), "own_stereo_residual_m"),
        (lambda row: row.__setitem__("own_observation_confidence", float("inf")), "own_observation_confidence"),
        (lambda row: row.__setitem__("own_confidence", -0.01), "own_confidence"),
        (lambda row: row.__setitem__("confidence", 1.01), "confidence"),
    ],
)
def test_malformed_keys_or_source_values_rejected(mutate, match):
    original = [factor("left", 0, 1)]
    rebuilt = [factor("left", 0, 1)]
    mutate(rebuilt[0])

    with pytest.raises(ValueError, match=match):
        sync_existing_factor_source_metadata(original, rebuilt)


def test_original_malformed_source_metadata_rejected_too():
    original = [factor("left", 0, 1, confidence=float("nan"))]
    rebuilt = [factor("left", 0, 1)]

    with pytest.raises(ValueError, match="confidence"):
        sync_existing_factor_source_metadata(original, rebuilt)
