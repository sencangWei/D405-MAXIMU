from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import numpy as np
import torch


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "pnp_supported_outlier_mask.py"
)
spec = importlib.util.spec_from_file_location("pnp_supported_outlier_mask", MODULE)
masker = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(masker)


def _args(edge_count: int = 2):
    poses = torch.zeros(2, 8, dtype=torch.float64)
    poses[:, 6] = 1.0
    poses[:, 7] = 1.0
    xs = torch.zeros(2, 4, 3, dtype=torch.float64)
    cs = torch.ones(2, 4, 1, dtype=torch.float64)
    k = torch.eye(3, dtype=torch.float64)
    ii = torch.tensor([0, 1] if edge_count == 2 else [0, 1, 1, 2])
    jj = torch.tensor([1, 0] if edge_count == 2 else [1, 0, 2, 1])
    idx = torch.arange(4).repeat(edge_count, 1)
    valid = torch.ones(edge_count, 4, 1, dtype=torch.bool)
    q = torch.ones(edge_count, 4, 1, dtype=torch.float64)
    return (
        poses, xs, cs, k, ii, jj, idx, valid, q,
        2, 2, 0, 1e-6, 2.0, 0.5, 0.2, 0.1, 5, 1e-4,
    )


def _graph(edge_count: int = 2):
    return {"frame_ids": [10, 11, 12], "args": _args(edge_count)}


def _depths(frame_count: int = 3):
    return torch.ones(frame_count, 4, dtype=torch.float64)


def _set_transform(monkeypatch, tx: float):
    transform = np.eye(4)
    transform[0, 3] = tx

    def fake_pnp(graph, depths, edge_index):
        return transform.copy(), {"accepted": True, "edge_index": int(edge_index)}

    monkeypatch.setattr(masker.audit, "_factorgraph_pnp_transform", fake_pnp)


def test_filter_removes_only_supported_inconsistent_and_preserves_unsupported(monkeypatch):
    graph = _graph()
    depths = _depths(2)
    depths[1, 2] = float("nan")  # target pixel 2 is unsupported and must remain valid.
    monkeypatch.setattr(masker, "_metric_loop_gate", lambda graph, depths, fwd, rev: (True, {"accepted": True}))
    _set_transform(monkeypatch, 3.0)
    original_args = tuple(v.clone() if torch.is_tensor(v) else v for v in graph["args"])

    variant_args, report = masker.filter_supported_outliers(graph, depths)

    assert report["accepted_pair_count"] == 1
    assert report["total_removed_count"] == 6
    directions = report["pairs"][0]["directions"]
    assert directions[0]["supported_count"] == 3
    assert directions[0]["removed_count"] == 3
    assert directions[0]["retained_supported_count"] == 0
    assert directions[0]["removed_fraction_of_supported"] == 1.0
    assert directions[0]["original_valid_count"] == 4
    assert directions[0]["remaining_valid_count"] == 1
    assert variant_args[7][0, :, 0].tolist() == [False, False, True, False]
    assert torch.equal(graph["args"][7], original_args[7])
    assert variant_args[7].data_ptr() != graph["args"][7].data_ptr()
    for index, (before, after) in enumerate(zip(original_args, variant_args)):
        if index == 7 or not torch.is_tensor(after):
            continue
        assert torch.equal(before, after)


def test_exact_two_pixel_boundary_removes_nothing(monkeypatch):
    graph = _graph()
    depths = _depths(2)
    monkeypatch.setattr(masker, "_metric_loop_gate", lambda graph, depths, fwd, rev: (True, {"accepted": True}))
    _set_transform(monkeypatch, 2.0)

    variant_args, report = masker.filter_supported_outliers(graph, depths)

    assert report["total_removed_count"] == 0
    assert torch.equal(variant_args[7], graph["args"][7])


def test_rejected_pair_leaves_masks_unchanged(monkeypatch):
    graph = _graph()
    depths = _depths(2)
    monkeypatch.setattr(masker, "_metric_loop_gate", lambda graph, depths, fwd, rev: (False, {"accepted": False, "reason": "cycle"}))

    variant_args, report = masker.filter_supported_outliers(graph, depths)

    assert report["accepted_pair_count"] == 0
    assert report["rejected_pair_count"] == 1
    assert report["pairs"][0]["status"] == "PAIR_REJECTED"
    assert [d["status"] for d in report["pairs"][0]["directions"]] == [
        "PAIR_REJECTED_UNCHANGED",
        "PAIR_REJECTED_UNCHANGED",
    ]
    assert all(d["removed_count"] == 0 and d["remaining_valid_count"] == d["original_valid_count"] for d in report["pairs"][0]["directions"])
    assert torch.equal(variant_args[7], graph["args"][7])


def test_metric_loop_gate_uses_empty_cfg_holder_lambda_and_cloned_valid(monkeypatch):
    graph = _graph()
    depths = _depths(2)
    captured = {}

    class FakeFactorGraph:
        @staticmethod
        def _metric_pnp_direction(holder, source_frame, target_frame, target_to_source_index, valid_target):
            valid_target[:] = False
            captured["holder_cfg"] = dict(holder.cfg)
            captured["has_lambda"] = callable(holder._metric_pnp_direction)
            return np.eye(4), {"accepted": True}

        @staticmethod
        def metric_loop_gate(holder, frame_i, frame_j, index_j_to_i, valid_j, index_i_to_j, valid_i):
            holder._metric_pnp_direction(frame_i, frame_j, index_j_to_i, valid_j)
            holder._metric_pnp_direction(frame_j, frame_i, index_i_to_j, valid_i)
            return True, {"accepted": True}

    module = types.SimpleNamespace(FactorGraph=FakeFactorGraph)
    monkeypatch.setitem(sys.modules, "mast3r_slam.global_opt", module)

    accepted, report = masker._metric_loop_gate(graph, depths, 0, 1)

    assert accepted is True
    assert report == {"accepted": True}
    assert captured == {"holder_cfg": {}, "has_lambda": True}
    assert graph["args"][7].all()  # production FactorGraph mutates valid inputs; originals must be clones.


def test_accepted_gate_but_direction_reject_fails_closed(monkeypatch):
    graph = _graph()
    depths = _depths(2)
    monkeypatch.setattr(masker, "_metric_loop_gate", lambda graph, depths, fwd, rev: (True, {"accepted": True}))
    monkeypatch.setattr(
        masker.audit,
        "_factorgraph_pnp_transform",
        lambda graph, depths, edge_index: (None, {"accepted": False, "reason": "direction_failed"}),
    )

    try:
        masker.filter_supported_outliers(graph, depths)
    except ValueError as exc:
        assert "accepted pair has rejected direction" in str(exc)
    else:
        raise AssertionError("accepted gate with rejected direction was not failclosed")


def test_all_unordered_pairs_are_processed(monkeypatch):
    graph = _graph(edge_count=4)
    depths = _depths(3)
    gate_calls = []

    def fake_gate(graph, depths, fwd, rev):
        gate_calls.append((fwd, rev))
        return (len(gate_calls) == 1), {"accepted": len(gate_calls) == 1}

    monkeypatch.setattr(masker, "_metric_loop_gate", fake_gate)
    _set_transform(monkeypatch, 3.0)

    variant_args, report = masker.filter_supported_outliers(graph, depths)

    assert gate_calls == [(0, 1), (2, 3)]
    assert report["accepted_pair_count"] == 1
    assert report["rejected_pair_count"] == 1
    assert report["pairs"][0]["accepted"] is True
    assert report["pairs"][1]["accepted"] is False
    assert not torch.equal(variant_args[7][0], graph["args"][7][0])
    assert torch.equal(variant_args[7][2:], graph["args"][7][2:])


def test_missing_reverse_and_duplicate_edges_are_malformed():
    graph = _graph()
    args = list(graph["args"])
    args[4] = torch.tensor([0])
    args[5] = torch.tensor([1])
    args[6] = args[6][:1]
    args[7] = args[7][:1]
    args[8] = args[8][:1]
    graph["args"] = tuple(args)
    try:
        masker.filter_supported_outliers(graph, _depths(2))
    except ValueError as exc:
        assert "missing reverse edge" in str(exc)
    else:
        raise AssertionError("missing reverse edge was accepted")

    graph = _graph()
    args = list(graph["args"])
    args[4] = torch.tensor([0, 0])
    args[5] = torch.tensor([1, 1])
    graph["args"] = tuple(args)
    try:
        masker.filter_supported_outliers(graph, _depths(2))
    except ValueError as exc:
        assert "duplicate directed edge" in str(exc)
    else:
        raise AssertionError("duplicate directed edge was accepted")
