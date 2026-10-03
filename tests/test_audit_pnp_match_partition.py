from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import torch


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "audit_pnp_match_partition.py"
)
spec = importlib.util.spec_from_file_location("audit_pnp_match_partition", MODULE)
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)


def _args():
    poses = torch.zeros(2, 8, dtype=torch.float64)
    poses[:, 6] = 1.0
    poses[:, 7] = 1.0
    xs = torch.zeros(2, 16, 3, dtype=torch.float64)
    for idx in range(16):
        xs[:, idx, 0] = float(idx % 4)
        xs[:, idx, 1] = float(idx // 4)
        xs[:, idx, 2] = 1.0
    cs = torch.ones(2, 16, 1, dtype=torch.float64)
    k = torch.eye(3, dtype=torch.float64)
    ii = torch.tensor([0])
    jj = torch.tensor([1])
    idx = torch.arange(16).reshape(1, 16)
    valid = torch.zeros(1, 16, 1, dtype=torch.bool)
    valid[:, :4, :] = True
    q = torch.ones(1, 16, 1, dtype=torch.float64)
    return (
        poses, xs, cs, k, ii, jj, idx, valid, q,
        4, 4, 0, 1e-6, 2.0, 0.5, 0.2, 0.1, 5, 1e-4,
    )


def test_classifier_orientation_threshold_and_depth_unsupported():
    source_depth = torch.ones(16)
    target_depth = torch.ones(16)
    target_depth[2] = float("nan")
    idx = torch.arange(16)
    valid = torch.zeros(16, dtype=torch.bool)
    valid[:4] = True
    transform = np.eye(4)
    transform[0, 3] = 3.0  # pixels 0,1,3 become >2px inconsistent; pixel2 lacks target depth

    out = audit.classify_pnp_reprojection(
        K=torch.eye(3),
        source_depth_flat=source_depth,
        target_depth_flat=target_depth,
        target_to_source_index=idx,
        valid_target=valid,
        transform_source_to_target=transform,
        width=4,
    )

    assert out["inconsistent"][:4].tolist() == [True, True, False, True]
    assert out["depth_unsupported"][:4].tolist() == [False, False, True, False]
    assert out["consistent"][:4].tolist() == [False, False, False, False]


def test_classifier_keeps_two_pixel_boundary_consistent():
    source_depth = torch.ones(16)
    target_depth = torch.ones(16)
    valid = torch.zeros(16, dtype=torch.bool)
    valid[0] = True
    transform = np.eye(4)
    transform[0, 3] = 2.0

    out = audit.classify_pnp_reprojection(
        K=torch.eye(3),
        source_depth_flat=source_depth,
        target_depth_flat=target_depth,
        target_to_source_index=torch.arange(16),
        valid_target=valid,
        transform_source_to_target=transform,
        width=4,
    )

    assert out["consistent"][0]
    assert not out["inconsistent"][0]


def test_classifier_does_not_mutate_valid_mask_with_invalid_indices():
    valid = torch.tensor([True, True, True, False])
    original = valid.clone()
    out = audit.classify_pnp_reprojection(
        K=torch.eye(3),
        source_depth_flat=torch.ones(4),
        target_depth_flat=torch.ones(4),
        target_to_source_index=torch.tensor([0, -1, 99, 1]),
        valid_target=valid,
        transform_source_to_target=np.eye(4),
        width=2,
    )

    assert torch.equal(valid, original)
    assert out["both_depth"].tolist() == [True, False, False, False]


def test_partition_masks_are_complementary_and_inputs_not_mutated():
    args = list(_args())
    args[6][0, :4] = torch.tensor([5, 6, 7, 8])
    args[11] = -1
    for target in range(4):
        args[1][1, target] = args[1][0, int(args[6][0, target])]
    args = tuple(args)
    frozen = tuple(v.clone() if torch.is_tensor(v) else v for v in args)
    classes = {
        "consistent": torch.tensor([True, False, False, False] + [False] * 12),
        "inconsistent": torch.tensor([False, True, False, False] + [False] * 12),
        "depth_unsupported": torch.tensor([False, False, True, False] + [False] * 12),
    }

    row = audit.partition_edge(args, args[0], args[0].clone(), 0, classes)

    assert row["common_count"] == 4
    assert row["classes"]["consistent"]["pre"]["count"] == 1
    assert row["classes"]["inconsistent"]["pre"]["count"] == 1
    assert row["classes"]["depth_unsupported"]["pre"]["count"] == 1
    assert row["classes"]["pnp_unclassified"]["pre"]["count"] == 1
    for before, after in zip(frozen, args):
        if torch.is_tensor(after):
            assert torch.equal(before, after)
        else:
            assert before == after


def test_rejected_pnp_is_preserved_as_failure_and_job_with_failures(monkeypatch, tmp_path):
    report = {
        "job": {"id": "case", "graph": str(tmp_path / "graph.pt")},
        "graph_sha256": "graphsha",
        "images": [],
        "stereo_source": {},
        "chain_pnp": [
            {
                "accepted": True,
                "first_frame_id": 1,
                "second_frame_id": 2,
                "rotation_disagreement_deg": {"control": 9.0},
            }
        ],
    }
    graph = {
        "frame_ids": [1, 2],
        "args": list(_args()),
    }
    monkeypatch.setattr(audit, "_validate_frozen_job", lambda trial, row: (report, {"poses": {"pre": graph["args"][0], "control": graph["args"][0]}}))
    monkeypatch.setattr(audit.depth_probe, "validate_job_bindings", lambda job: None)
    monkeypatch.setattr(torch, "load", lambda *a, **k: graph)
    monkeypatch.setattr(audit.depth_probe, "load_depths", lambda job, graph: (torch.ones(2, 16), [], {}))
    monkeypatch.setattr(audit, "_accepted_pnp_transform", lambda graph, depths, edge_index: (None, {"accepted": False, "reason": "pnp_failed"}))
    monkeypatch.setattr(audit, "sha", lambda path: "sha")

    row = audit.audit_job(tmp_path, {"id": "case", "report_sha256": "unused"})

    assert row["pairs"][0]["status"] == "PNP_REJECTED"
    assert row["pairs"][0]["pnp_report"]["reason"] == "pnp_failed"
    assert row["pairs"][1]["status"] == "PNP_PAIR_MISSING_EDGE"
    assert row["status"] == "WITH_FAILURES"


def test_audit_job_audits_both_directed_edges(monkeypatch, tmp_path):
    report = {
        "job": {"id": "case", "graph": str(tmp_path / "graph.pt")},
        "graph_sha256": "graphsha",
        "images": [],
        "stereo_source": {},
        "chain_pnp": [
            {
                "accepted": True,
                "first_frame_id": 1,
                "second_frame_id": 2,
                "rotation_disagreement_deg": {"control": 9.0},
            }
        ],
    }
    args = list(_args())
    args[4] = torch.tensor([0, 1])
    args[5] = torch.tensor([1, 0])
    args[6] = torch.arange(16).repeat(2, 1)
    args[7] = torch.ones(2, 16, 1, dtype=torch.bool)
    args[8] = torch.ones(2, 16, 1, dtype=torch.float64)
    graph = {"frame_ids": [1, 2], "args": tuple(args)}
    poses = {"poses": {"pre": args[0], "control": args[0].clone()}}
    monkeypatch.setattr(audit, "_validate_frozen_job", lambda trial, row: (report, poses))
    monkeypatch.setattr(audit.depth_probe, "validate_job_bindings", lambda job: None)
    monkeypatch.setattr(torch, "load", lambda *a, **k: graph)
    monkeypatch.setattr(audit.depth_probe, "load_depths", lambda job, graph: (torch.ones(2, 16), [], {}))
    monkeypatch.setattr(audit, "_accepted_pnp_transform", lambda graph, depths, edge_index: (np.eye(4), {"accepted": True}))
    monkeypatch.setattr(audit, "sha", lambda path: "sha")

    row = audit.audit_job(tmp_path, {"id": "case", "report_sha256": "unused"})

    assert row["status"] == "AUDIT_COMPLETE"
    assert [(p["direction"], p["edge_index"]) for p in row["pairs"]] == [("forward", 0), ("reverse", 1)]
    assert all(p["status"] == "PARTITION_COMPLETE" for p in row["pairs"])


def test_source_trial_flags_are_required():
    good = {
        "schema": "native_depth_shape_GN_falsifier_v1",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "precision_pass": False,
    }
    audit._validate_source_trial(good)
    for key, value in (
        ("schema", "wrong"),
        ("diagnostic_only", False),
        ("external_ground_truth_used", True),
        ("production_promoted", True),
        ("precision_pass", True),
    ):
        bad = dict(good)
        bad[key] = value
        try:
            audit._validate_source_trial(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted bad {key}")


def test_depth_image_and_metadata_binding_mismatch_fails(monkeypatch, tmp_path):
    report = {
        "job": {"id": "case", "graph": str(tmp_path / "graph.pt")},
        "images": [{"frame_id": 1}],
        "stereo_source": {"eye": "left"},
        "chain_pnp": [],
    }
    graph = {"frame_ids": [1, 2], "args": tuple(_args())}
    monkeypatch.setattr(audit, "_validate_frozen_job", lambda trial, row: (report, {"poses": {"pre": graph["args"][0], "control": graph["args"][0]}}))
    monkeypatch.setattr(audit.depth_probe, "validate_job_bindings", lambda job: None)
    monkeypatch.setattr(torch, "load", lambda *a, **k: graph)
    monkeypatch.setattr(audit.depth_probe, "load_depths", lambda job, graph: (torch.ones(2, 16), [{"frame_id": 99}], {"eye": "left"}))

    try:
        audit.audit_job(tmp_path, {"id": "case", "report_sha256": "unused"})
    except ValueError as exc:
        assert "image bindings differ" in str(exc)
    else:
        raise AssertionError("mismatched image binding was accepted")
