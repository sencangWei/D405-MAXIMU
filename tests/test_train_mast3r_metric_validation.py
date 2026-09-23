import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "train_mast3r_d405_ir.py"
SPEC = importlib.util.spec_from_file_location(SCRIPT.stem, SCRIPT)
train = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(train)


def test_legacy_training_criterion_remains_reproducible():
    criterion = train.training_criterion_expression("legacy", 10.0)

    assert "ConfLoss(Regr3D" in criterion
    assert "ConfMatchingLoss" in criterion
    assert "D405RelativeMotionLoss" not in criterion


def test_relative_motion_training_adds_weighted_term():
    criterion = train.training_criterion_expression("relative-motion", 12.5)

    assert criterion.endswith("12.5*D405RelativeMotionLoss()")


def test_relative_motion_training_rejects_nonpositive_weight():
    with pytest.raises(ValueError, match="weight must be positive"):
        train.training_criterion_expression("relative-motion", 0.0)


def test_metric_validation_preserves_absolute_d405_scale():
    criterion = train.validation_criterion_expression("metric")

    assert criterion.startswith("Regr3D(")
    assert "gt_scale=True" in criterion
    assert "ScaleShiftInv" not in criterion
    assert "MatchingLoss" not in criterion


def test_legacy_validation_remains_explicitly_reproducible():
    criterion = train.validation_criterion_expression("scale-shift-invariant")

    assert "Regr3D_ScaleShiftInv" in criterion
    assert "MatchingLoss" in criterion


def test_relative_motion_validation_targets_cross_view_geometry():
    assert (
        train.validation_criterion_expression("relative-motion")
        == "D405RelativeMotionLoss()"
    )


def test_metric_correspondence_training_adds_metric_point_term():
    criterion = train.training_criterion_expression("metric-correspondence", 10.0)

    assert criterion.endswith("10*D405MetricCorrespondenceLoss()")


def test_metric_relative_training_balances_scale_and_temporal_shape():
    criterion = train.training_criterion_expression(
        "metric-relative", 10.0, relative_motion_weight=100.0
    )

    assert "10*D405MetricCorrespondenceLoss()" in criterion
    assert criterion.endswith("100*D405RelativeMotionLoss()")


def test_metric_relative_training_requires_both_positive_weights():
    with pytest.raises(ValueError, match="relative motion weight must be positive"):
        train.training_criterion_expression(
            "metric-relative", 10.0, relative_motion_weight=0.0
        )


def test_metric_correspondence_validation_preserves_metre_scale():
    assert (
        train.validation_criterion_expression("metric-correspondence")
        == "D405MetricCorrespondenceLoss()"
    )


def test_training_dataset_expression_can_repeat_low_observability_turns(tmp_path):
    expression = train.training_dataset_expression(
        "D405IRTemporal",
        tmp_path / "manifest.json",
        seed=7,
        high_motion_repeat=1,
        low_observability_repeat=3,
        low_observability_loss_weight=2.5,
        low_observability_max_tracked_points=160,
        low_observability_min_angular_speed_deg_s=8.0,
    )

    assert "high_motion_repeat=1" in expression
    assert "low_observability_repeat=3" in expression
    assert "low_observability_loss_weight=2.5" in expression
    assert "low_observability_max_tracked_points=160" in expression
    assert "low_observability_min_angular_speed_deg_s=8" in expression


def test_stereo_dataset_expression_does_not_receive_temporal_only_arguments(tmp_path):
    expression = train.training_dataset_expression(
        "D405IRStereo",
        tmp_path / "manifest.json",
        seed=7,
        high_motion_repeat=1,
        low_observability_repeat=3,
    )

    assert "high_motion_repeat=1" in expression
    assert "low_observability" not in expression


def test_unknown_validation_criterion_is_rejected():
    with pytest.raises(ValueError, match="unsupported validation criterion"):
        train.validation_criterion_expression("unknown")


def test_evaluation_disables_periodic_and_final_checkpoint_writes():
    calls = []

    class Misc:
        save_model = lambda *args, **kwargs: calls.append("periodic")

    class Training:
        misc = Misc()
        save_final_model = lambda *args, **kwargs: calls.append("final")

    train.disable_evaluation_checkpoint_writes(Training)
    Training.misc.save_model()
    Training.save_final_model()

    assert calls == []
