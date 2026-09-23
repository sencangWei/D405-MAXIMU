"""D405-specific MASt3R losses that target temporal relative motion."""

from __future__ import annotations

import torch

import mast3r.utils.path_to_dust3r  # noqa: F401
from dust3r.losses import MultiLoss
from dust3r.utils.geometry import geotrf, inv


def temporal_loss_weights(
    gt1: dict,
    batch_size: int,
    reference: torch.Tensor,
) -> torch.Tensor:
    """Return one positive motion-loss weight per temporal sample."""
    value = gt1.get("temporal_loss_weight")
    if value is None:
        return torch.ones(batch_size, device=reference.device, dtype=reference.dtype)
    if isinstance(value, torch.Tensor):
        weights = value.to(device=reference.device, dtype=reference.dtype).reshape(-1)
    else:
        weights = torch.as_tensor(
            value, device=reference.device, dtype=reference.dtype
        ).reshape(-1)
    if weights.numel() == 1 and batch_size > 1:
        weights = weights.expand(batch_size)
    if weights.numel() != batch_size:
        raise ValueError("temporal loss weight count does not match batch size")
    if not torch.all(torch.isfinite(weights) & (weights > 0)):
        raise ValueError("temporal loss weights must be finite and positive")
    return weights


def weighted_batch_error_mean(
    error_batches: list[torch.Tensor],
    sample_weights: torch.Tensor,
    zero_reference: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return weighted training loss and concatenated unweighted diagnostics."""
    errors = [batch for batch in error_batches if batch.numel()]
    if not errors:
        zero = zero_reference.sum() * 0.0
        return zero, zero.new_empty((0,))
    numerator = zero_reference.sum() * 0.0
    denominator = zero_reference.new_zeros(())
    for sample_errors, weight in zip(error_batches, sample_weights):
        if sample_errors.numel() == 0:
            continue
        numerator = numerator + weight * sample_errors.sum()
        denominator = denominator + sample_errors.numel()
    return numerator / denominator, torch.cat(errors)


def correspondence_motion_errors(
    gt_points1: torch.Tensor,
    gt_points2: torch.Tensor,
    pred_points1: torch.Tensor,
    pred_points2: torch.Tensor,
    correspondence1: torch.Tensor,
    correspondence2: torch.Tensor,
    valid_correspondence: torch.Tensor,
) -> torch.Tensor:
    """Return metric errors in matched-point displacement, in metres."""
    batch_size, match_count = correspondence1.shape[:2]
    batch_index = torch.arange(
        batch_size, device=pred_points1.device
    )[:, None].expand(batch_size, match_count)
    x1, y1 = correspondence1.long().unbind(-1)
    x2, y2 = correspondence2.long().unbind(-1)

    gt_delta = (
        gt_points2[batch_index, y2, x2] - gt_points1[batch_index, y1, x1]
    )
    pred_delta = (
        pred_points2[batch_index, y2, x2]
        - pred_points1[batch_index, y1, x1]
    )
    errors = torch.linalg.vector_norm(pred_delta - gt_delta, dim=-1)
    valid = valid_correspondence.bool() & torch.isfinite(errors)
    return errors[valid]


def correspondence_metric_errors(
    gt_points1: torch.Tensor,
    gt_points2: torch.Tensor,
    pred_points1: torch.Tensor,
    pred_points2: torch.Tensor,
    correspondence1: torch.Tensor,
    correspondence2: torch.Tensor,
    valid_correspondence: torch.Tensor,
) -> torch.Tensor:
    """Return metric 3-D errors for matched pixels in both temporal views.

    Unlike ``correspondence_motion_errors``, this objective does not cancel a
    common scale or frame error shared by both predicted point maps. That is
    required because MASt3R-SLAM thresholds and keyframe topology consume the
    point maps before the downstream metric-scale estimator runs.
    """
    batch_size, match_count = correspondence1.shape[:2]
    batch_index = torch.arange(
        batch_size, device=pred_points1.device
    )[:, None].expand(batch_size, match_count)
    x1, y1 = correspondence1.long().unbind(-1)
    x2, y2 = correspondence2.long().unbind(-1)

    errors1 = torch.linalg.vector_norm(
        pred_points1[batch_index, y1, x1] - gt_points1[batch_index, y1, x1],
        dim=-1,
    )
    errors2 = torch.linalg.vector_norm(
        pred_points2[batch_index, y2, x2] - gt_points2[batch_index, y2, x2],
        dim=-1,
    )
    valid = (
        valid_correspondence.bool()
        & torch.isfinite(errors1)
        & torch.isfinite(errors2)
    )
    return torch.cat((errors1[valid], errors2[valid]))


class D405RelativeMotionLoss(MultiLoss):
    """Supervise cross-view rigid motion without scene-depth domination.

    MASt3R predicts both point maps in the first camera frame.  Comparing the
    displacement between ground-truth corresponding pixels cancels common
    scene geometry while retaining errors in temporal scale and pose.
    """

    def get_name(self):
        return type(self).__name__

    def compute_loss(self, gt1, gt2, pred1, pred2, **_kwargs):
        in_camera1 = inv(gt1["camera_pose"])
        gt_points1 = geotrf(in_camera1, gt1["pts3d"])
        gt_points2 = geotrf(in_camera1, gt2["pts3d"])
        batch_size = pred1["pts3d"].shape[0]
        sample_weights = temporal_loss_weights(
            gt1, batch_size, pred1["pts3d"]
        )
        error_batches = [
            correspondence_motion_errors(
                gt_points1[index : index + 1],
                gt_points2[index : index + 1],
                pred1["pts3d"][index : index + 1],
                pred2["pts3d_in_other_view"][index : index + 1],
                gt1["corres"][index : index + 1],
                gt2["corres"][index : index + 1],
                gt1["valid_corres"][index : index + 1],
            )
            for index in range(batch_size)
        ]
        loss, errors = weighted_batch_error_mean(
            error_batches, sample_weights, pred1["pts3d"]
        )
        if errors.numel() == 0:
            p95 = loss.detach()
            mean = loss.detach()
        else:
            mean = errors.detach().mean()
            p95 = torch.quantile(errors.detach(), 0.95)
        return loss, {
            "relative_motion_l21_m": float(mean),
            "relative_motion_weighted_l21_m": float(loss.detach()),
            "relative_motion_p95_m": float(p95),
            "relative_motion_matches": int(errors.numel()),
        }


class D405MetricCorrespondenceLoss(MultiLoss):
    """Supervise temporal point maps in the D405 metric camera frame."""

    def get_name(self):
        return type(self).__name__

    def compute_loss(self, gt1, gt2, pred1, pred2, **_kwargs):
        in_camera1 = inv(gt1["camera_pose"])
        gt_points1 = geotrf(in_camera1, gt1["pts3d"])
        gt_points2 = geotrf(in_camera1, gt2["pts3d"])
        batch_size = pred1["pts3d"].shape[0]
        sample_weights = temporal_loss_weights(
            gt1, batch_size, pred1["pts3d"]
        )
        error_batches = [
            correspondence_metric_errors(
                gt_points1[index : index + 1],
                gt_points2[index : index + 1],
                pred1["pts3d"][index : index + 1],
                pred2["pts3d_in_other_view"][index : index + 1],
                gt1["corres"][index : index + 1],
                gt2["corres"][index : index + 1],
                gt1["valid_corres"][index : index + 1],
            )
            for index in range(batch_size)
        ]
        loss, errors = weighted_batch_error_mean(
            error_batches, sample_weights, pred1["pts3d"]
        )
        if errors.numel() == 0:
            p95 = loss.detach()
            mean = loss.detach()
        else:
            mean = errors.detach().mean()
            p95 = torch.quantile(errors.detach(), 0.95)
        return loss, {
            "metric_correspondence_l21_m": float(mean),
            "metric_correspondence_weighted_l21_m": float(loss.detach()),
            "metric_correspondence_p95_m": float(p95),
            "metric_correspondence_points": int(errors.numel()),
        }
