"""CPU helper for fixed-scale GN target scales from stereo depth shape ratios."""

from __future__ import annotations

import math
from typing import Any

import torch


SCHEMA = "mast3r_fixed_scale_stereo_targets_v1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _ratio_by_frame(condition_report: dict[str, Any], frame_count: int) -> list[float]:
    frames = condition_report.get("frames")
    _require(isinstance(frames, list), "condition report must contain frames list")
    ratios: list[float | None] = [None] * frame_count
    for row in frames:
        _require(isinstance(row, dict), "condition report frame rows must be dicts")
        frame_index = row.get("frame_index")
        _require(isinstance(frame_index, int), "frame_index must be an integer")
        _require(0 <= frame_index < frame_count, "frame_index out of range")
        _require(ratios[frame_index] is None, f"duplicate ratio for frame {frame_index}")
        support_count = row.get("support_count")
        _require(isinstance(support_count, int) and support_count > 0, f"frame {frame_index} has no stereo support")
        ratio = row.get("median_metric_over_native_ratio")
        _require(isinstance(ratio, (float, int)) and math.isfinite(float(ratio)) and float(ratio) > 0,
                 f"frame {frame_index} has invalid stereo ratio")
        ratios[frame_index] = float(ratio)
    missing = [index for index, ratio in enumerate(ratios) if ratio is None]
    _require(not missing, f"missing stereo ratios for frames {missing}")
    return [float(ratio) for ratio in ratios]


def stereo_scale_targets_from_condition_report(
    poses: torch.Tensor,
    condition_report: dict[str, Any],
) -> tuple[torch.Tensor, dict[str, Any]]:
    """Compute fixed Sim3 target scales from per-frame stereo/native ratios.

    Target scales preserve frame 0 exactly and only use relative stereo shape
    ratios: ``target_s_i = s0 * r_i / r0``.  Multiplying every stereo metric
    depth by a constant leaves all targets unchanged.
    """

    _require(torch.is_tensor(poses), "poses must be a tensor")
    _require(poses.ndim == 2 and poses.shape[1] == 8, "poses must have shape [N,8]")
    _require(torch.is_floating_point(poses), "poses must be floating point")
    _require(torch.isfinite(poses).all().item(), "poses must be finite")
    scales = poses[:, 7]
    _require((scales > 0).all().item(), "pose scales must be positive")

    frame_count = int(poses.shape[0])
    _require(frame_count > 0, "poses must contain at least one frame")
    ratios = _ratio_by_frame(condition_report, frame_count)
    r0 = ratios[0]
    s0 = scales[0]
    ratio_tensor = torch.as_tensor(ratios, dtype=poses.dtype, device=poses.device)
    targets = (s0 * ratio_tensor / torch.as_tensor(r0, dtype=poses.dtype, device=poses.device)).clone()
    targets[0] = s0

    report = {
        "schema": SCHEMA,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "frame_count": frame_count,
        "reference_frame_index": 0,
        "reference_scale": float(s0.detach().cpu().item()),
        "reference_ratio": float(r0),
        "min_target_scale": float(torch.min(targets).detach().cpu().item()),
        "max_target_scale": float(torch.max(targets).detach().cpu().item()),
    }
    return targets, report
