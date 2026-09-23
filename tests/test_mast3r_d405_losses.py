import sys
from pathlib import Path

import torch
import pytest


TRAINING_REPO = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-training")
sys.path[:0] = [str(TRAINING_REPO), str(TRAINING_REPO / "dust3r")]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mast3r_d405_losses import (
    D405MetricCorrespondenceLoss,
    D405RelativeMotionLoss,
    D405WindowScaleLoss,
    correspondence_metric_errors,
    correspondence_motion_errors,
    window_log_scale_errors,
)
from mast3r_d405_ir_dataset import (
    low_observability_sample_weight,
    low_observability_temporal_samples,
)


def test_low_observability_temporal_samples_require_weak_geometry_and_turning():
    samples = [
        {"tracked_depth_points": 120, "angular_speed_deg_s": 12.0},
        {"tracked_depth_points": 240, "angular_speed_deg_s": 12.0},
        {"tracked_depth_points": 120, "angular_speed_deg_s": 4.0},
    ]

    selected = low_observability_temporal_samples(samples, 160, 8.0)

    assert selected == samples[:1]


def test_low_observability_temporal_samples_reject_invalid_thresholds():
    with pytest.raises(ValueError, match="tracked depth points"):
        low_observability_temporal_samples([], 0, 8.0)
    with pytest.raises(ValueError, match="angular speed"):
        low_observability_temporal_samples([], 160, -1.0)


def test_low_observability_selection_does_not_depend_on_existing_repetition():
    sample = {"tracked_depth_points": 120, "angular_speed_deg_s": 12.0}

    selected = low_observability_temporal_samples([sample], 160, 8.0)

    assert selected == [sample]


def test_low_observability_sample_weight_only_selects_weak_turns():
    weak_turn = {"tracked_depth_points": 120, "angular_speed_deg_s": 12.0}
    strong_turn = {"tracked_depth_points": 240, "angular_speed_deg_s": 12.0}

    assert low_observability_sample_weight(weak_turn, 160, 8.0, 3.0) == 3.0
    assert low_observability_sample_weight(strong_turn, 160, 8.0, 3.0) == 1.0


def test_low_observability_sample_weight_rejects_nonpositive_weight():
    sample = {"tracked_depth_points": 120, "angular_speed_deg_s": 12.0}

    with pytest.raises(ValueError, match="loss weight"):
        low_observability_sample_weight(sample, 160, 8.0, 0.0)


def test_correspondence_motion_error_cancels_common_scene_offset():
    gt1 = torch.tensor([[[[0.0, 0.0, 1.0], [1.0, 0.0, 1.0]]]])
    gt2 = gt1.clone()
    pred1 = gt1 + torch.tensor([0.2, -0.1, 0.3])
    pred2 = gt2 + torch.tensor([0.2, -0.1, 0.3])
    coordinates = torch.tensor([[[0, 0], [1, 0]]])

    errors = correspondence_motion_errors(
        gt1,
        gt2,
        pred1,
        pred2,
        coordinates,
        coordinates,
        torch.tensor([[True, True]]),
    )

    assert torch.allclose(errors, torch.zeros_like(errors), atol=1e-7)


def test_correspondence_motion_error_exposes_relative_translation():
    gt1 = torch.tensor([[[[0.0, 0.0, 1.0]]]])
    gt2 = gt1.clone()
    pred1 = gt1.clone()
    pred2 = gt2 + torch.tensor([0.012, 0.0, 0.0])
    coordinates = torch.tensor([[[0, 0]]])

    errors = correspondence_motion_errors(
        gt1,
        gt2,
        pred1,
        pred2,
        coordinates,
        coordinates,
        torch.tensor([[True]]),
    )

    assert errors.item() == pytest.approx(0.012, abs=1e-7)


def test_correspondence_motion_error_ignores_invalid_matches():
    points = torch.zeros((1, 1, 2, 3))
    coordinates = torch.tensor([[[0, 0], [1, 0]]])

    errors = correspondence_motion_errors(
        points,
        points,
        points,
        points + 0.1,
        coordinates,
        coordinates,
        torch.tensor([[True, False]]),
    )

    assert errors.shape == (1,)


def test_metric_correspondence_error_exposes_common_scale_error():
    gt1 = torch.tensor([[[[0.2, 0.0, 1.0]]]])
    gt2 = torch.tensor([[[[0.2, 0.0, 1.0]]]])
    coordinates = torch.tensor([[[0, 0]]])

    errors = correspondence_metric_errors(
        gt1,
        gt2,
        gt1 * 0.9,
        gt2 * 0.9,
        coordinates,
        coordinates,
        torch.tensor([[True]]),
    )

    expected = torch.linalg.vector_norm(gt1[0, 0, 0] * 0.1)
    assert errors.shape == (2,)
    assert torch.allclose(errors, expected.expand_as(errors), atol=1e-7)


def test_metric_correspondence_error_is_zero_for_metric_predictions():
    points = torch.tensor([[[[0.2, -0.1, 0.5]]]])
    coordinates = torch.tensor([[[0, 0]]])

    errors = correspondence_metric_errors(
        points,
        points,
        points.clone(),
        points.clone(),
        coordinates,
        coordinates,
        torch.tensor([[True]]),
    )

    assert torch.allclose(errors, torch.zeros_like(errors), atol=1e-7)


def test_window_log_scale_error_ignores_shape_preserving_metric_points():
    points = torch.tensor([[[[0.2, 0.0, 1.0], [0.0, 0.3, 1.2]]]])
    coordinates = torch.tensor([[[0, 0], [1, 0]]])

    errors = window_log_scale_errors(
        points,
        points,
        points.clone(),
        points.clone(),
        coordinates,
        coordinates,
        torch.tensor([[True, True]]),
    )

    assert torch.allclose(errors, torch.zeros_like(errors), atol=1e-7)


def test_window_log_scale_error_reports_global_scale_without_pointwise_shape_loss():
    points = torch.tensor([[[[0.2, 0.0, 1.0], [0.0, 0.3, 1.2]]]])
    coordinates = torch.tensor([[[0, 0], [1, 0]]])

    errors = window_log_scale_errors(
        points,
        points,
        points * 1.1,
        points * 1.1,
        coordinates,
        coordinates,
        torch.tensor([[True, True]]),
    )

    assert errors.item() == pytest.approx(torch.log(torch.tensor(1.1)).item())


def test_window_scale_loss_applies_temporal_sample_weight():
    camera_pose = torch.eye(4).repeat(2, 1, 1)
    points = torch.tensor([[[[0.0, 0.0, 1.0]]]]).repeat(2, 1, 1, 1)
    coordinates = torch.zeros((2, 1, 2), dtype=torch.long)
    gt1 = {
        "camera_pose": camera_pose,
        "pts3d": points,
        "corres": coordinates,
        "valid_corres": torch.ones((2, 1), dtype=torch.bool),
        "temporal_loss_weight": torch.tensor([1.0, 3.0]),
    }
    gt2 = {"camera_pose": camera_pose, "pts3d": points, "corres": coordinates}
    pred1 = points.clone()
    pred2 = points.clone()
    pred1[0] *= 1.1
    pred2[0] *= 1.1
    pred1[1] *= 1.2
    pred2[1] *= 1.2

    loss, details = D405WindowScaleLoss().compute_loss(
        gt1,
        gt2,
        {"pts3d": pred1},
        {"pts3d_in_other_view": pred2},
    )

    expected = (torch.log(torch.tensor(1.1)) + 3 * torch.log(torch.tensor(1.2))) / 2
    assert loss.item() == pytest.approx(expected.item())
    assert details["window_scale_samples"] == 2


def test_metric_correspondence_loss_transforms_world_points_to_camera1():
    camera1_pose = torch.eye(4).unsqueeze(0)
    camera1_pose[:, 0, 3] = 1.0
    camera2_pose = torch.eye(4).unsqueeze(0)
    camera2_pose[:, 1, 3] = 0.2
    world_point = torch.tensor([[[[1.0, 0.0, 1.0]]]])
    camera1_point = torch.tensor([[[[0.0, 0.0, 1.0]]]])
    coordinates = torch.tensor([[[0, 0]]])
    gt1 = {
        "camera_pose": camera1_pose,
        "pts3d": world_point,
        "corres": coordinates,
        "valid_corres": torch.tensor([[True]]),
    }
    gt2 = {
        "camera_pose": camera2_pose,
        "pts3d": world_point,
        "corres": coordinates,
    }

    loss, details = D405MetricCorrespondenceLoss().compute_loss(
        gt1,
        gt2,
        {"pts3d": camera1_point.clone()},
        {"pts3d_in_other_view": camera1_point.clone()},
    )

    assert loss.item() == pytest.approx(0.0, abs=1e-7)
    assert details["metric_correspondence_points"] == 2


def test_relative_motion_loss_weights_only_selected_sample_objective():
    camera_pose = torch.eye(4).repeat(2, 1, 1)
    points = torch.zeros((2, 1, 1, 3))
    coordinates = torch.zeros((2, 1, 2), dtype=torch.long)
    gt1 = {
        "camera_pose": camera_pose,
        "pts3d": points,
        "corres": coordinates,
        "valid_corres": torch.ones((2, 1), dtype=torch.bool),
        "temporal_loss_weight": torch.tensor([1.0, 3.0]),
    }
    gt2 = {"camera_pose": camera_pose, "pts3d": points, "corres": coordinates}
    pred2 = points.clone()
    pred2[0, 0, 0, 0] = 1.0
    pred2[1, 0, 0, 0] = 3.0

    loss, details = D405RelativeMotionLoss().compute_loss(
        gt1,
        gt2,
        {"pts3d": points.clone()},
        {"pts3d_in_other_view": pred2},
    )

    assert details["relative_motion_l21_m"] == pytest.approx(2.0)
    assert details["relative_motion_weighted_l21_m"] == pytest.approx(5.0)
    assert loss.item() == pytest.approx(5.0)


def test_relative_motion_loss_weight_does_not_cancel_for_batch_size_one():
    camera_pose = torch.eye(4).unsqueeze(0)
    points = torch.zeros((1, 1, 1, 3))
    coordinates = torch.zeros((1, 1, 2), dtype=torch.long)
    gt1 = {
        "camera_pose": camera_pose,
        "pts3d": points,
        "corres": coordinates,
        "valid_corres": torch.ones((1, 1), dtype=torch.bool),
        "temporal_loss_weight": torch.tensor([3.0]),
    }
    gt2 = {"camera_pose": camera_pose, "pts3d": points, "corres": coordinates}
    pred2 = points.clone()
    pred2[0, 0, 0, 0] = 2.0

    loss, details = D405RelativeMotionLoss().compute_loss(
        gt1,
        gt2,
        {"pts3d": points.clone()},
        {"pts3d_in_other_view": pred2},
    )

    assert details["relative_motion_l21_m"] == pytest.approx(2.0)
    assert details["relative_motion_weighted_l21_m"] == pytest.approx(6.0)
    assert loss.item() == pytest.approx(6.0)


def test_metric_correspondence_loss_applies_sample_weight_after_matching():
    camera_pose = torch.eye(4).repeat(2, 1, 1)
    points = torch.zeros((2, 1, 1, 3))
    coordinates = torch.zeros((2, 1, 2), dtype=torch.long)
    gt1 = {
        "camera_pose": camera_pose,
        "pts3d": points,
        "corres": coordinates,
        "valid_corres": torch.ones((2, 1), dtype=torch.bool),
        "temporal_loss_weight": torch.tensor([1.0, 3.0]),
    }
    gt2 = {"camera_pose": camera_pose, "pts3d": points, "corres": coordinates}
    pred1 = points.clone()
    pred2 = points.clone()
    pred1[0, 0, 0, 0] = pred2[0, 0, 0, 0] = 1.0
    pred1[1, 0, 0, 0] = pred2[1, 0, 0, 0] = 3.0

    loss, details = D405MetricCorrespondenceLoss().compute_loss(
        gt1,
        gt2,
        {"pts3d": pred1},
        {"pts3d_in_other_view": pred2},
    )

    assert details["metric_correspondence_l21_m"] == pytest.approx(2.0)
    assert details["metric_correspondence_weighted_l21_m"] == pytest.approx(5.0)
    assert loss.item() == pytest.approx(5.0)
