from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import torch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / ".planning"
    / "dual_ir_regression_25_20261002"
    / "sep29_frontier_local_pairs_20261003"
    / "condition_native_pointmap_depth_shape.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("condition_native_pointmap_depth_shape", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


conditioner = _load_module()


def _args(dtype=torch.float64):
    poses = torch.tensor(
        [
            [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            [1.0, 0.0, 0.0, 0.0, 0.1, 0.0, 0.0, 1.0],
        ],
        dtype=dtype,
    )
    xs = torch.tensor(
        [
            [[1.0, 0.0, 1.0], [0.0, 2.0, 2.0], [3.0, 0.0, 3.0], [4.0, 4.0, 4.0]],
            [[0.5, 0.0, 1.0], [0.0, 1.0, 2.0], [1.5, 0.0, 3.0], [2.0, 2.0, 4.0]],
        ],
        dtype=dtype,
    )
    cs = torch.ones((2, 4), dtype=dtype)
    qi = torch.ones((3,), dtype=dtype)
    return (
        poses,
        xs,
        cs,
        qi,
        torch.tensor([0, 1], dtype=torch.long),
        torch.tensor([1, 0], dtype=torch.long),
        torch.tensor([0, 1, 2], dtype=torch.long),
        torch.tensor([1, 2, 3], dtype=torch.long),
        torch.tensor([True, True, True]),
        torch.tensor(0.25, dtype=dtype),
        torch.tensor(0.75, dtype=dtype),
        torch.tensor(1.0, dtype=dtype),
        torch.tensor(2.0, dtype=dtype),
        torch.tensor(3.0, dtype=dtype),
        torch.tensor(4.0, dtype=dtype),
        torch.tensor(5.0, dtype=dtype),
        torch.tensor(6.0, dtype=dtype),
        torch.tensor(7.0, dtype=dtype),
        torch.tensor(8.0, dtype=dtype),
    )


def test_preserves_ray_direction_and_per_frame_median_gauge():
    args = _args()
    metric = torch.tensor(
        [
            [2.0, 8.0, 18.0, 123.0],  # metric/native ratios on support: 2,4,6 -> median 4
            [5.0, 20.0, 45.0, 321.0],  # ratios: 5,10,15 -> median 10
        ],
        dtype=torch.float64,
    )
    valid = torch.tensor([[True, True, True, False], [True, True, True, False]])

    cloned, report = conditioner.condition_depth_shape(args, metric, valid)

    assert report["frames"][0]["median_metric_over_native_ratio"] == 4.0
    assert report["frames"][1]["median_metric_over_native_ratio"] == 10.0
    assert torch.allclose(cloned[1][0, 0], args[1][0, 0] * 0.5)
    assert torch.allclose(cloned[1][0, 1], args[1][0, 1] * 1.0)
    assert torch.allclose(cloned[1][0, 2], args[1][0, 2] * 1.5)
    assert torch.allclose(cloned[1][1, 0], args[1][1, 0] * 0.5)
    assert torch.allclose(cloned[1][1, 1], args[1][1, 1] * 1.0)
    assert torch.allclose(cloned[1][1, 2], args[1][1, 2] * 1.5)
    before_xy_over_z = args[1][..., :2] / args[1][..., 2:3]
    after_xy_over_z = cloned[1][..., :2] / cloned[1][..., 2:3]
    assert torch.allclose(after_xy_over_z, before_xy_over_z)


def test_only_xs_values_change_and_inputs_are_not_mutated():
    args = _args()
    original = tuple(value.clone() if torch.is_tensor(value) else value for value in args)
    metric = torch.full(args[1].shape[:2], 3.0, dtype=torch.float64)
    valid = torch.ones(args[1].shape[:2], dtype=torch.bool)

    cloned, report = conditioner.condition_depth_shape(args, metric, valid)

    assert report["changed_arg_indices"] == [1]
    assert not torch.equal(cloned[1], args[1])
    for index, (before, after) in enumerate(zip(args, cloned)):
        if index == 1:
            continue
        if torch.is_tensor(before):
            assert torch.equal(before, after), index
        else:
            assert before == after
    for before, after in zip(original, args):
        if torch.is_tensor(before):
            assert torch.equal(before, after)
        else:
            assert before == after


def test_invalid_stereo_pixels_and_invalid_metric_outside_mask_are_untouched():
    args = _args()
    metric = torch.tensor(
        [[float("nan"), 8.0, 18.0, 123.0], [5.0, float("nan"), 45.0, 321.0]],
        dtype=torch.float64,
    )
    valid = torch.tensor([[False, True, True, False], [True, True, True, False]])

    cloned, report = conditioner.condition_depth_shape(args, metric, valid)

    assert torch.equal(cloned[1][0, 0], args[1][0, 0])
    assert torch.equal(cloned[1][0, 3], args[1][0, 3])
    assert torch.equal(cloned[1][1, 1], args[1][1, 1])
    assert torch.equal(cloned[1][1, 3], args[1][1, 3])
    assert report["frames"][0]["support_count"] == 2
    assert report["frames"][1]["support_count"] == 2


def test_no_support_frame_is_unchanged_and_reports_zero():
    args = _args()
    metric = torch.full(args[1].shape[:2], float("nan"), dtype=torch.float64)
    valid = torch.zeros(args[1].shape[:2], dtype=torch.bool)

    cloned, report = conditioner.condition_depth_shape(args, metric, valid)

    assert torch.equal(cloned[1], args[1])
    assert report["changed_arg_indices"] == []
    assert report["total_changed_points"] == 0
    assert all(frame["support_count"] == 0 and frame["unchanged"] for frame in report["frames"])


def test_rejects_bad_metric_or_valid_shapes():
    args = _args()
    with pytest.raises(ValueError, match="metric_depths shape"):
        conditioner.condition_depth_shape(args, torch.ones((2, 3)), torch.ones((2, 4), dtype=torch.bool))
    with pytest.raises(ValueError, match="stereo_valid shape"):
        conditioner.condition_depth_shape(args, torch.ones((2, 4)), torch.ones((2, 3), dtype=torch.bool))


def test_rejects_nonfinite_xyz_and_nonpositive_pose_scales():
    args = list(_args())
    args[1] = args[1].clone()
    args[1][0, 0, 0] = float("nan")
    with pytest.raises(ValueError, match="Xs contain non-finite"):
        conditioner.condition_depth_shape(tuple(args), torch.ones((2, 4)), torch.ones((2, 4), dtype=torch.bool))

    args = list(_args())
    args[0] = args[0].clone()
    args[0][0, 7] = 0.0
    with pytest.raises(ValueError, match="pose scales"):
        conditioner.condition_depth_shape(tuple(args), torch.ones((2, 4)), torch.ones((2, 4), dtype=torch.bool))


def test_nonpositive_native_z_pixel_is_excluded_and_unchanged():
    args = list(_args())
    args[1] = args[1].clone()
    args[1][0, 0, 2] = 0.0
    metric = torch.tensor([[2.0, 8.0, 18.0, 1.0], [5.0, 20.0, 45.0, 1.0]], dtype=torch.float64)
    valid = torch.tensor([[True, True, True, False], [True, True, True, False]])

    cloned, report = conditioner.condition_depth_shape(tuple(args), metric, valid)

    assert torch.equal(cloned[1][0, 0], args[1][0, 0])
    assert report["frames"][0]["support_count"] == 2
    assert report["frames"][0]["median_metric_over_native_ratio"] == 4.0


def test_rejects_nonfinite_original_non_xs_args():
    args = list(_args())
    args[2] = args[2].clone()
    args[2][0, 0] = float("inf")
    with pytest.raises(ValueError, match=r"args\[2\]"):
        conditioner.condition_depth_shape(tuple(args), torch.ones((2, 4)), torch.ones((2, 4), dtype=torch.bool))


def test_preserves_dtype_and_device():
    args = _args(dtype=torch.float32)
    metric = torch.tensor([[2.0, 8.0, 18.0, 1.0], [5.0, 20.0, 45.0, 1.0]], dtype=torch.float64)
    valid = torch.tensor([[True, True, True, False], [True, True, True, False]])

    cloned, _ = conditioner.condition_depth_shape(args, metric, valid)

    assert cloned[1].dtype == args[1].dtype
    assert cloned[1].device == args[1].device
