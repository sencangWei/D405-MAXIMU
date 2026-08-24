from pathlib import Path

import pytest
import yaml

from product_calibration.manual_gripper import (
    CalibrationError,
    ManualGripperCalibration,
    ManualGripperTracker,
    shortest_angle_delta_deg,
)


ROOT = Path(__file__).parents[1]
PROFILE = ROOT / "product_calibration/umi_manual_gripper_20260824.yaml"


def test_profile_reproduces_measured_manual_points():
    calibration = ManualGripperCalibration.load(PROFILE)

    assert calibration.profile_id == "UMI_MANUAL_GRIPPER_20260824_V1"
    assert calibration.estimate_gap_mm(68.408441, "closing") == pytest.approx(50.174)
    assert calibration.estimate_gap_mm(69.158545, "opening") == pytest.approx(50.174)
    assert calibration.estimate_gap_mm(13.857045, "closing") == pytest.approx(4.0)
    assert calibration.estimate_gap_mm(15.144568, "opening") == pytest.approx(4.0)


def test_interpolation_outputs_manual_state_not_loaded_object_size():
    calibration = ManualGripperCalibration.load(PROFILE)
    state = calibration.state(48.320859, "closing")

    assert state.estimated_no_load_gap_mm == pytest.approx(33.45)
    assert state.dual_closing_distance_mm == pytest.approx(33.45)
    assert state.single_jaw_travel_mm == pytest.approx(16.725)
    assert state.closure_ratio == pytest.approx(0.5)
    assert state.no_load_uncertainty_mm == pytest.approx(1.5)
    assert state.loaded_object_size_valid is False
    assert state.status == "MANUAL_NO_LOAD_ESTIMATE"


def test_estimate_clamps_beyond_open_and_soft_pad_contact():
    calibration = ManualGripperCalibration.load(PROFILE)

    assert calibration.estimate_gap_mm(120.0, "closing") == pytest.approx(66.9)
    assert calibration.estimate_gap_mm(0.0, "closing") == pytest.approx(0.0)
    assert calibration.state(0.0, "closing").closure_ratio == pytest.approx(1.0)


def test_direction_specific_curves_do_not_collapse_hysteresis():
    calibration = ManualGripperCalibration.load(PROFILE)

    closing_gap = calibration.estimate_gap_mm(30.0, "closing")
    opening_gap = calibration.estimate_gap_mm(30.0, "opening")

    assert closing_gap != pytest.approx(opening_gap)


def test_tracker_accumulates_small_steps_and_is_wrap_safe():
    calibration = ManualGripperCalibration.load(PROFILE)
    tracker = ManualGripperTracker(calibration)

    first = tracker.update(91.1)
    assert first.direction == "unknown"
    assert first.status == "MANUAL_NO_LOAD_ESTIMATE_DIRECTION_UNKNOWN"

    assert tracker.update(91.05).direction == "unknown"
    closing = tracker.update(90.8)
    assert closing.direction == "closing"
    assert tracker.update(90.78).direction == "closing"

    opening = tracker.update(91.2)
    assert opening.direction == "opening"
    assert shortest_angle_delta_deg(359.9, 0.1) == pytest.approx(-0.2)
    assert shortest_angle_delta_deg(0.1, 359.9) == pytest.approx(0.2)


def test_invalid_encoder_never_emits_distance():
    calibration = ManualGripperCalibration.load(PROFILE)
    state = ManualGripperTracker(calibration).update(42.0, encoder_valid=False)

    assert state.status == "ENCODER_INVALID"
    assert state.estimated_no_load_gap_mm is None
    assert state.closure_ratio is None


def test_profile_rejects_non_monotonic_curve(tmp_path):
    document = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    document["curves"]["closing"][2]["gap_mm"] = 1.0
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(CalibrationError, match="strictly increasing"):
        ManualGripperCalibration.load(path)


def test_release_evidence_is_tracked_and_passes_frozen_holdout_gate():
    document = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    evidence_path = ROOT / document["source"]["evidence"]
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))

    assert evidence["blind_holdout"]["result"] == "PASS"
    assert evidence["blind_holdout"]["maximum_absolute_error_mm"] <= 1.5
    assert evidence["blind_holdout"]["mean_absolute_error_mm"] <= 1.0
    assert evidence["release_semantics"]["manual_state_and_no_load_gap_estimate"] == "PASS"
    assert evidence["release_semantics"]["loaded_object_size"].startswith("NOT_SUPPORTED")
