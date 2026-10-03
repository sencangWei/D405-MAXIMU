from __future__ import annotations

import importlib.util
from pathlib import Path

import torch
import json
import pytest


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "audit_native_objective_change.py"
)
spec = importlib.util.spec_from_file_location("audit_native_objective_change", MODULE)
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


def _args(valid_count=2):
    poses = _identity_pose()
    xs = _grid_points()
    cs = torch.ones(2, 30, 1, dtype=torch.float64)
    k = torch.eye(3, dtype=torch.float64)
    ii = torch.tensor([0], dtype=torch.long)
    jj = torch.tensor([1], dtype=torch.long)
    idx = torch.arange(30, dtype=torch.long).reshape(1, 30)
    valid = torch.zeros(1, 30, 1, dtype=torch.bool)
    valid[:, :valid_count, :] = True
    q = torch.ones(1, 30, 1, dtype=torch.float64)
    args = (
        poses, xs, cs, k, ii, jj, idx, valid, q,
        10, 10, 0, 1e-6, 2.0, 0.5, 0.2, 0.1, 5, 1e-4,
    )
    args = list(args)
    args[6][0, :2] = torch.tensor([11, 12])
    args[1][1, 0] = args[1][0, 11]
    args[1][1, 1] = args[1][0, 12]
    return tuple(args)


def test_unchanged_graph_has_zero_common_delta_and_no_mask_change():
    args = _args(valid_count=2)
    frame_ids = [100, 200]

    row = audit.summarize_edge_objective_change(args, args[0], args[0].clone(), 0, frame_ids)

    assert row["source_frame_id"] == 100
    assert row["target_frame_id"] == 200
    assert row["pre_dynamic_count"] == 2
    assert row["post_dynamic_count"] == 2
    assert row["common_count"] == 2
    assert row["gained_count"] == 0
    assert row["lost_count"] == 0
    assert row["common_cost_delta"] == 0.0


def test_count_loss_and_zero_common_support_are_reported():
    args = list(_args(valid_count=2))
    frame_ids = [10, 20]
    post_pose = args[0].clone()
    post_pose[1, 0] = 100.0  # projects outside image; post dynamic mask loses all points

    row = audit.summarize_edge_objective_change(tuple(args), args[0], post_pose, 0, frame_ids)

    assert row["pre_dynamic_count"] == 2
    assert row["post_dynamic_count"] == 0
    assert row["common_count"] == 0
    assert row["lost_count"] == 2
    assert row["gained_count"] == 0
    assert row["pre_common_cost"] == 0.0
    assert row["post_common_cost"] == 0.0


def test_compact_edge_uses_only_two_endpoint_frames():
    args = _args(valid_count=1)
    compact = audit.compact_edge_args(torch, args, 0, args[0])

    assert compact[0].shape == (2, 8)
    assert compact[1].shape == (2, 30, 3)
    assert compact[4].tolist() == [0]
    assert compact[5].tolist() == [1]
    assert compact[6].shape == (1, 30)


def test_missing_pose_hash_fails_before_loading_unbound_poses(tmp_path):
    job = tmp_path / "missing"
    job.mkdir()
    report = job / "report.json"
    report.write_text(json.dumps({"status": "DIAGNOSTIC_COMPLETE", "job": {"id": "missing"}}))
    with pytest.raises(ValueError, match="missing frozen poses_sha256"):
        audit._validate_frozen_job(tmp_path, {"id": "missing", "report_sha256": audit.sha(report)})


@pytest.mark.parametrize("flags", [
    {"schema": "wrong"},
    {"diagnostic_only": False},
    {"external_ground_truth_used": True},
    {"production_promoted": True},
])
def test_rejects_invalid_trial_provenance(tmp_path, flags):
    summary = {"schema": "native_depth_shape_GN_falsifier_v1", "diagnostic_only": True,
               "external_ground_truth_used": False, "production_promoted": False, "jobs": []}
    summary.update(flags)
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError):
        audit.audit_trial(tmp_path)
