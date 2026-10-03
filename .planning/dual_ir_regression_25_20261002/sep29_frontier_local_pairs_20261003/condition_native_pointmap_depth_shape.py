"""Depth-shape conditioning for native MASt3R point maps.

Diagnostic-only helper: clone a native 19-argument GN input tuple and modify
only ``args[1]`` (Xs) by per-frame stereo metric depth shape.  It preserves each
point ray and the native per-frame median depth gauge; it does not alter poses,
confidences, edges, thresholds, or residual selection.
"""

from __future__ import annotations

import math
from typing import Any

import torch


SCHEMA = "condition_native_pointmap_depth_shape_v1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _clone_value(value: Any) -> Any:
    if torch.is_tensor(value):
        return value.clone()
    return value


def _check_finite_original_arg(index: int, value: Any) -> None:
    if torch.is_tensor(value):
        if value.is_floating_point() or value.is_complex():
            _require(torch.isfinite(value).all().item(), f"args[{index}] contains non-finite values")
        return
    if isinstance(value, (float, int)):
        _require(math.isfinite(float(value)), f"args[{index}] is non-finite")


def _validate_args19(args19: tuple[Any, ...]) -> tuple[torch.Tensor, torch.Tensor]:
    _require(isinstance(args19, tuple), "args19 must be a tuple")
    _require(len(args19) == 19, f"args19 must contain exactly 19 entries, got {len(args19)}")
    for index, value in enumerate(args19):
        if index in (0, 1):
            continue
        _check_finite_original_arg(index, value)

    poses = args19[0]
    xs = args19[1]
    _require(torch.is_tensor(poses), "args[0] poses must be a tensor")
    _require(torch.is_tensor(xs), "args[1] Xs must be a tensor")
    _require(poses.ndim == 2 and poses.shape[1] == 8, "args[0] poses must have shape (N, 8)")
    _require(xs.ndim == 3 and xs.shape[2] == 3, "args[1] Xs must have shape (N, P, 3)")
    _require(poses.shape[0] == xs.shape[0], "pose/Xs frame counts differ")
    _require(torch.is_floating_point(xs), "args[1] Xs must be floating point")
    _require(torch.isfinite(poses).all().item(), "args[0] poses contain non-finite values")
    _require(torch.isfinite(xs).all().item(), "args[1] Xs contain non-finite values")
    _require((poses[:, 7] > 0).all().item(), "args[0] pose scales must be positive")
    return poses, xs


def condition_depth_shape(
    args19: tuple[Any, ...],
    metric_depths: torch.Tensor,
    stereo_valid: torch.Tensor,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Return cloned GN args with per-frame depth-shape conditioned Xs.

    Valid conditioning pixels are the intersection of the raw stereo validity
    mask, finite positive metric depth, and finite positive native Z.  Frames
    with no support remain unchanged.
    """

    _, xs = _validate_args19(args19)
    _require(torch.is_tensor(metric_depths), "metric_depths must be a tensor")
    _require(torch.is_tensor(stereo_valid), "stereo_valid must be a tensor")
    _require(tuple(metric_depths.shape) == tuple(xs.shape[:2]), "metric_depths shape must match Xs[:2]")
    _require(tuple(stereo_valid.shape) == tuple(xs.shape[:2]), "stereo_valid shape must match Xs[:2]")

    metric = metric_depths.to(device=xs.device, dtype=xs.dtype)
    valid = stereo_valid.to(device=xs.device, dtype=torch.bool)
    native_z = xs[..., 2]
    support = valid & torch.isfinite(metric) & (metric > 0) & torch.isfinite(native_z) & (native_z > 0)

    cloned = tuple(_clone_value(value) for value in args19)
    corrected_xs = cloned[1]
    frame_reports: list[dict[str, Any]] = []
    total_changed = 0

    for frame_index in range(xs.shape[0]):
        frame_support = support[frame_index]
        support_count = int(frame_support.sum().item())
        if support_count == 0:
            frame_reports.append(
                {
                    "frame_index": frame_index,
                    "support_count": 0,
                    "changed_count": 0,
                    "median_metric_over_native_ratio": None,
                    "unchanged": True,
                }
            )
            continue

        ratios = metric[frame_index, frame_support] / native_z[frame_index, frame_support]
        median_ratio = torch.median(ratios)
        _require(torch.isfinite(median_ratio).item() and median_ratio.item() > 0, "invalid frame median ratio")
        scale = metric[frame_index, frame_support] / (median_ratio * native_z[frame_index, frame_support])
        corrected_xs[frame_index, frame_support, :] = xs[frame_index, frame_support, :] * scale[:, None]
        total_changed += support_count
        frame_reports.append(
            {
                "frame_index": frame_index,
                "support_count": support_count,
                "changed_count": support_count,
                "median_metric_over_native_ratio": float(median_ratio.detach().cpu().item()),
                "unchanged": False,
            }
        )

    report = {
        "schema": SCHEMA,
        "external_ground_truth_used": False,
        "tracker_reference_used": False,
        "production_promoted": False,
        "changed_arg_indices": [1] if total_changed else [],
        "frame_count": int(xs.shape[0]),
        "point_count": int(xs.shape[1]),
        "total_changed_points": total_changed,
        "frames": frame_reports,
    }
    return cloned, report
