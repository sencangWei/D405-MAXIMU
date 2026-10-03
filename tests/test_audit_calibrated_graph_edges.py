import importlib.util
from pathlib import Path

import pytest
import torch


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "audit_calibrated_graph_edges.py"
)
spec = importlib.util.spec_from_file_location("audit_calibrated_graph_edges", MODULE)
audit = importlib.util.module_from_spec(spec)
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


def _args(*, point_count=30, valid_count=1):
    poses = _identity_pose()
    Xs = _grid_points(point_count)
    Cs = torch.ones(2, point_count, 1, dtype=torch.float64)
    K = torch.eye(3, dtype=torch.float64)
    ii = torch.tensor([0], dtype=torch.long)
    jj = torch.tensor([1], dtype=torch.long)
    idx = torch.arange(point_count, dtype=torch.long).reshape(1, point_count)
    valid = torch.zeros(1, point_count, 1, dtype=torch.bool)
    valid[:, :valid_count, :] = True
    Q = torch.ones(1, point_count, 1, dtype=torch.float64)
    return (
        poses, Xs, Cs, K, ii, jj, idx, valid, Q,
        10, 10, 0, 1e-6, 2.0, 0.5, 0.2, 0.1, 5, 1e-4,
    )


def _clone_args(args):
    return tuple(value.clone() if torch.is_tensor(value) else value for value in args)


def test_identity_edge_has_zero_residual_and_whitening():
    args = list(_args(valid_count=1))
    args[6][0, 0] = 11
    args[1][1, 0] = args[1][0, 11]

    out = audit.calibrated_edge_residuals(tuple(args), 0)

    assert out["mask"].tolist()[:1] == [True]
    assert torch.allclose(out["residual"][0], torch.zeros(3, dtype=torch.float64))
    assert torch.allclose(out["whitened"][0], torch.zeros(3, dtype=torch.float64))


def test_known_translation_matches_native_relative_pose_direction():
    args = list(_args(valid_count=1))
    args[0][1, 0] = 1.0
    args[6][0, 0] = 11
    args[1][1, 0] = args[1][0, 11]

    out = audit.calibrated_edge_residuals(tuple(args), 0)

    assert out["mask"][0]
    assert torch.allclose(out["residual"][0], torch.tensor([1.0, 0.0, 0.0], dtype=torch.float64))
    assert torch.allclose(out["whitened"][0], torch.tensor([0.5, 0.0, 0.0], dtype=torch.float64))


def test_sim3_scale_changes_log_depth_without_changing_center_projection():
    args = list(_args(valid_count=1))
    args[0][1, 7] = 2.0
    args[6][0, 0] = 11
    args[1][1, 0] = torch.tensor([1.0, 1.0, 1.0], dtype=torch.float64)

    out = audit.calibrated_edge_residuals(tuple(args), 0)

    assert out["mask"][0]
    assert torch.allclose(out["residual"][0, :2], torch.zeros(2, dtype=torch.float64))
    assert torch.allclose(out["residual"][0, 2], torch.log(torch.tensor(2.0, dtype=torch.float64)))


def test_mapped_index_orientation_uses_edge_idx_target_not_point_order():
    args = list(_args(valid_count=2))
    args[6][0, 0] = 22
    args[6][0, 1] = 11
    args[1][1, 0] = args[1][0, 22]
    args[1][1, 1] = args[1][0, 11]

    out = audit.calibrated_edge_residuals(tuple(args), 0)

    assert out["target_uv"][:2].tolist() == [[2.0, 2.0], [1.0, 1.0]]
    assert torch.allclose(out["residual"][:2], torch.zeros(2, 3, dtype=torch.float64))


def test_strict_q_confidence_border_and_z_masks_zero_invalid_whitened():
    args = list(_args(valid_count=6))
    args[6][0, :6] = torch.tensor([11, 12, 13, 14, 15, 16])
    for k in range(6):
        args[1][1, k] = args[1][0, int(args[6][0, k])]
    args[8][0, 1, 0] = 0.01  # Q low
    args[2][0, 13, 0] = 0.01  # source C low for k=2
    args[2][1, 3, 0] = 0.01  # target C low for k=3
    args[1][1, 4] = torch.tensor([0.0, 1.0, 1.0], dtype=torch.float64)  # border
    args[1][1, 5, 2] = -1.0  # z invalid

    out = audit.calibrated_edge_residuals(tuple(args), 0)

    assert out["mask"][:6].tolist() == [True, False, False, False, False, False]
    assert torch.count_nonzero(out["whitened"][1:6]).item() == 0


def test_summary_uses_common_mask_and_reports_dynamic_counts():
    pre = list(_args(valid_count=2))
    post = list(_args(valid_count=2))
    pre[6][0, :2] = torch.tensor([11, 12])
    post[6][0, :2] = torch.tensor([11, 12])
    for args in (pre, post):
        args[1][1, 0] = args[1][0, 11]
        args[1][1, 1] = args[1][0, 12]
    post[1][1, 1, 2] = -1.0

    summary = audit.summarize_edge_update(tuple(pre), tuple(post), 0)

    assert summary["native_dynamic_mask_counts"] == {"pre": 2, "post": 1, "common": 1}
    assert summary["pre_common"]["count"] == 1
    assert summary["post_common"]["count"] == 1
    assert "analysis-only" in summary["note"]


def test_inputs_are_not_mutated():
    args = list(_args(valid_count=1))
    args[6][0, 0] = 11
    args[1][1, 0] = args[1][0, 11]
    frozen = _clone_args(tuple(args))

    audit.calibrated_edge_residuals(tuple(args), 0)

    for before, after in zip(frozen, args):
        if torch.is_tensor(after):
            assert torch.equal(before, after)
        else:
            assert before == after


def test_rejects_bad_pose_quaternion_and_matched_index():
    args = list(_args(valid_count=1))
    args[0][0, 6] = 2.0
    with pytest.raises(ValueError, match="quaternions"):
        audit.calibrated_edge_residuals(tuple(args), 0)

    args = list(_args(valid_count=1))
    args[6][0, 0] = 999
    with pytest.raises(ValueError, match="out of bounds"):
        audit.calibrated_edge_residuals(tuple(args), 0)
