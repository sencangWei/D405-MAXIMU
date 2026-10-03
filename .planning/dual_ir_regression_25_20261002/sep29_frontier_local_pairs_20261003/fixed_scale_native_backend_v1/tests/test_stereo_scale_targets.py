from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import torch


MODULE = Path(__file__).resolve().parents[1] / "stereo_scale_targets.py"
spec = importlib.util.spec_from_file_location("stereo_scale_targets", MODULE)
targets_mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(targets_mod)


def _poses(dtype=torch.float64):
    poses = torch.zeros((3, 8), dtype=dtype)
    poses[:, 6] = 1.0
    poses[:, 7] = torch.tensor([2.0, 3.0, 4.0], dtype=dtype)
    return poses


def _report(ratios=(10.0, 20.0, 5.0), support=(3, 4, 5)):
    return {
        "schema": "condition_native_pointmap_depth_shape_v1",
        "frames": [
            {
                "frame_index": index,
                "support_count": support[index],
                "median_metric_over_native_ratio": ratios[index],
            }
            for index in range(len(ratios))
        ],
    }


def test_targets_preserve_frame0_and_use_relative_ratios():
    poses = _poses()

    targets, report = targets_mod.stereo_scale_targets_from_condition_report(poses, _report())

    assert torch.allclose(targets, torch.tensor([2.0, 4.0, 1.0], dtype=torch.float64))
    assert targets[0].item() == poses[0, 7].item()
    assert report["schema"] == "mast3r_fixed_scale_stereo_targets_v1"
    assert report["external_ground_truth_used"] is False


def test_global_metric_ratio_multiplier_leaves_targets_unchanged():
    poses = _poses()

    base, _ = targets_mod.stereo_scale_targets_from_condition_report(poses, _report((10.0, 20.0, 5.0)))
    scaled, _ = targets_mod.stereo_scale_targets_from_condition_report(poses, _report((30.0, 60.0, 15.0)))

    assert torch.allclose(base, scaled)


def test_no_pose_mutation_and_preserves_dtype_device():
    poses = _poses(dtype=torch.float32)
    before = poses.clone()

    targets, _ = targets_mod.stereo_scale_targets_from_condition_report(poses, _report())

    assert torch.equal(poses, before)
    assert targets.dtype == poses.dtype
    assert targets.device == poses.device


def test_rejects_pose_shape_nonfinite_and_nonpositive_scales():
    with pytest.raises(ValueError, match="shape"):
        targets_mod.stereo_scale_targets_from_condition_report(torch.zeros((3, 7)), _report())

    poses = _poses()
    poses[0, 0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        targets_mod.stereo_scale_targets_from_condition_report(poses, _report())

    poses = _poses()
    poses[1, 7] = 0.0
    with pytest.raises(ValueError, match="positive"):
        targets_mod.stereo_scale_targets_from_condition_report(poses, _report())


def test_rejects_missing_duplicate_and_out_of_range_ratios():
    poses = _poses()
    missing = _report()
    missing["frames"] = missing["frames"][:2]
    with pytest.raises(ValueError, match="missing"):
        targets_mod.stereo_scale_targets_from_condition_report(poses, missing)

    duplicate = _report()
    duplicate["frames"].append(dict(duplicate["frames"][1]))
    with pytest.raises(ValueError, match="duplicate"):
        targets_mod.stereo_scale_targets_from_condition_report(poses, duplicate)

    out_of_range = _report()
    out_of_range["frames"][2]["frame_index"] = 3
    with pytest.raises(ValueError, match="out of range"):
        targets_mod.stereo_scale_targets_from_condition_report(poses, out_of_range)


def test_rejects_no_support_and_invalid_ratios():
    poses = _poses()
    no_support = _report(support=(3, 0, 5))
    with pytest.raises(ValueError, match="no stereo support"):
        targets_mod.stereo_scale_targets_from_condition_report(poses, no_support)

    nonpositive = _report(ratios=(10.0, 0.0, 5.0))
    with pytest.raises(ValueError, match="invalid stereo ratio"):
        targets_mod.stereo_scale_targets_from_condition_report(poses, nonpositive)

    nonfinite = _report(ratios=(10.0, float("inf"), 5.0))
    with pytest.raises(ValueError, match="invalid stereo ratio"):
        targets_mod.stereo_scale_targets_from_condition_report(poses, nonfinite)
