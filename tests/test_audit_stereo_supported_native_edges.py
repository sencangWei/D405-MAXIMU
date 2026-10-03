import importlib.util
from pathlib import Path

import pytest
import torch


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "audit_stereo_supported_native_edges.py"
)
spec = importlib.util.spec_from_file_location("audit_stereo_supported_native_edges", MODULE)
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)


def _identity_pose():
    pose = torch.zeros(2, 8, dtype=torch.float64)
    pose[:, 6] = 1.0
    pose[:, 7] = 1.0
    return pose


def _grid_points(count=30, width=10):
    pts = torch.zeros(2, count, 3, dtype=torch.float64)
    for index in range(count):
        pts[:, index, 0] = float(index % width)
        pts[:, index, 1] = float(index // width)
        pts[:, index, 2] = 1.0
    return pts


def _args(*, point_count=30, valid_count=4):
    poses = _identity_pose()
    xs = _grid_points(point_count)
    cs = torch.ones(2, point_count, 1, dtype=torch.float64)
    k = torch.eye(3, dtype=torch.float64)
    ii = torch.tensor([0], dtype=torch.long)
    jj = torch.tensor([1], dtype=torch.long)
    idx = torch.arange(point_count, dtype=torch.long).reshape(1, point_count)
    valid = torch.zeros(1, point_count, 1, dtype=torch.bool)
    valid[:, :valid_count, :] = True
    q = torch.ones(1, point_count, 1, dtype=torch.float64)
    return (
        poses, xs, cs, k, ii, jj, idx, valid, q,
        10, 10, 0, 1e-6, 2.0, 0.5, 0.2, 0.1, 5, 1e-4,
    )


def _matched_args():
    args = list(_args(valid_count=4))
    args[6][0, :4] = torch.tensor([22, 11, 12, 13])
    for target_index in range(4):
        args[1][1, target_index] = args[1][0, int(args[6][0, target_index])]
    return tuple(args)


def _clone_args(args):
    return tuple(value.clone() if torch.is_tensor(value) else value for value in args)


def test_splits_by_source_indices_and_target_arange_not_target_order():
    pre = _matched_args()
    post = _clone_args(pre)
    post = list(post)
    post[0][1, 0] = 0.5
    post = tuple(post)
    stereo = torch.ones((2, 30), dtype=torch.bool)
    stereo[0, 22] = False  # source for target pixel 0 only
    stereo[1, 1] = False  # target pixel 1 only

    summary = audit.summarize_stereo_supported_edge(pre, post, 0, stereo)

    assert summary["native_common_count"] == 4
    assert summary["stereo_supported_count"] == 2
    assert summary["stereo_unsupported_count"] == 2
    assert summary["supported_mask"][:4].tolist() == [False, False, True, True]
    assert summary["unsupported_mask"][:4].tolist() == [True, True, False, False]


def test_complementary_split_counts_equal_common_and_report_stats():
    pre = _matched_args()
    post = list(_clone_args(pre))
    post[0][1, 0] = 1.0
    post = tuple(post)
    stereo = torch.ones((2, 30), dtype=torch.bool)
    stereo[1, 3] = False
    pre[8][0, :4, 0] = torch.tensor([0.25, 0.5, 0.75, 1.0], dtype=torch.float64)

    summary = audit.summarize_stereo_supported_edge(pre, post, 0, stereo)

    assert summary["stereo_supported_count"] + summary["stereo_unsupported_count"] == summary["native_common_count"]
    assert summary["supported"]["pre"]["count"] == 3
    assert summary["unsupported"]["pre"]["count"] == 1
    assert summary["supported"]["pre"]["q_median"] == 0.5
    assert summary["supported"]["post"]["pixel_p95"] is not None
    assert summary["supported"]["post"]["huber_rho_sum_analysis"] >= 0.0


def test_invalid_zero_and_out_of_range_masks_are_handled_failclosed():
    args = _matched_args()
    numeric_mask = torch.ones((2, 30), dtype=torch.float64)
    numeric_mask[0, 22] = 0.0
    summary = audit.summarize_stereo_supported_edge(args, args, 0, numeric_mask)
    assert summary["stereo_supported_count"] == 3
    assert summary["stereo_unsupported_count"] == 1

    bad_shape = torch.ones((2, 29), dtype=torch.bool)
    with pytest.raises(ValueError, match="shape"):
        audit.summarize_stereo_supported_edge(args, args, 0, bad_shape)

    bad_value = torch.ones((2, 30), dtype=torch.float64)
    bad_value[0, 0] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        audit.summarize_stereo_supported_edge(args, args, 0, bad_value)

    bad_args = list(_matched_args())
    bad_args[6][0, 0] = 999
    with pytest.raises(ValueError, match="out of bounds"):
        audit.summarize_stereo_supported_edge(tuple(bad_args), tuple(bad_args), 0, torch.ones((2, 30), dtype=torch.bool))


def test_unsupported_empty_reports_none_stats():
    args = _matched_args()
    summary = audit.summarize_stereo_supported_edge(args, args, 0, torch.ones((2, 30), dtype=torch.bool))

    assert summary["stereo_supported_count"] == summary["native_common_count"]
    assert summary["stereo_unsupported_count"] == 0
    assert summary["unsupported"]["pre"]["count"] == 0
    assert summary["unsupported"]["pre"]["q_median"] is None
    assert summary["unsupported"]["post"]["pixel_p95"] is None


def test_inputs_are_not_mutated():
    pre = _matched_args()
    post = _clone_args(pre)
    stereo = torch.ones((2, 30), dtype=torch.bool)
    frozen_pre = _clone_args(pre)
    frozen_post = _clone_args(post)
    frozen_stereo = stereo.clone()

    audit.summarize_stereo_supported_edge(pre, post, 0, stereo)

    for before, after in zip(frozen_pre, pre):
        if torch.is_tensor(after):
            assert torch.equal(before, after)
        else:
            assert before == after
    for before, after in zip(frozen_post, post):
        if torch.is_tensor(after):
            assert torch.equal(before, after)
        else:
            assert before == after
    assert torch.equal(frozen_stereo, stereo)
