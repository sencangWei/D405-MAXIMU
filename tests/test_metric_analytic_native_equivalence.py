from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"


def _load_probe():
    path = BASE / "probe_metric_analytic_native_equivalence.py"
    spec = importlib.util.spec_from_file_location("probe_metric_analytic_native_equivalence_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def test_factor_callable_restored_on_exception():
    import metric_relative_pose_factor

    original = metric_relative_pose_factor.factor_linearization

    def replacement(*_args, **_kwargs):  # pragma: no cover - should not be called
        raise AssertionError("replacement was called")

    try:
        try:
            probe.with_factor_linearization(
                replacement,
                lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
            )
        except RuntimeError as exc:
            assert str(exc) == "synthetic failure"
        assert metric_relative_pose_factor.factor_linearization is original
    finally:
        metric_relative_pose_factor.factor_linearization = original


def test_pose_differences_labels_bitwise_exact_and_failure():
    reference = torch.tensor(
        [
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0],
            [1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0, 2.0],
        ],
        dtype=torch.float32,
    )
    exact = probe.pose_differences(reference, reference.clone())
    assert exact["bitwise_exact"] is True
    assert exact["translation_max"] == 0.0
    assert exact["rotation_rad_max"] == 0.0
    assert exact["logscale_max"] == 0.0

    candidate = reference.clone()
    candidate[1, 0] += 0.25
    candidate[1, 7] *= 1.1
    changed = probe.pose_differences(reference, candidate)
    assert changed["bitwise_exact"] is False
    assert changed["translation_max"] > 0.0
    assert changed["logscale_max"] > 0.0


def test_pose_differences_rejects_nonfinite_or_nonpositive_scale():
    valid = torch.tensor([[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0]], dtype=torch.float32)
    bad_scale = valid.clone()
    bad_scale[0, 7] = 0.0
    try:
        probe.pose_differences(valid, bad_scale)
    except ValueError as exc:
        assert "nonpositive scale" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("nonpositive scale was accepted")

    bad_finite = valid.clone()
    bad_finite[0, 0] = float("nan")
    try:
        probe.pose_differences(valid, bad_finite)
    except ValueError as exc:
        assert "nonfinite" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("nonfinite pose was accepted")


def test_memory_guard_rejects_low_free_gpu_bytes(tmp_path):
    graph = tmp_path / "graph.pt"
    graph.write_bytes(b"x" * 1024)
    guard = probe.memory_guard(graph, free_bytes=probe.MIN_FREE_BYTES - 1)
    assert guard["accepted"] is False
    assert guard["reason"] == "GPU_FREE_BELOW_GUARD_OR_UNKNOWN"


def test_gpu_free_parser_uses_device0_not_max():
    text = "0, 1024\n1, 99999\n"
    assert probe._parse_device0_free_bytes(text) == 1024 * 1024 * 1024


def test_equal_positive_iteration_counts_gate():
    assert probe.equal_positive_iteration_counts([{}], [{}], [{}]) is True
    assert probe.equal_positive_iteration_counts([{}], [{}, {}], [{}]) is False
    assert probe.equal_positive_iteration_counts([], [{}], [{}]) is False


def test_run_probe_rejects_empty_case_list(tmp_path):
    try:
        probe.run_probe([], tmp_path / "out")
    except ValueError as exc:
        assert "at least one case" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty case list was accepted")


def test_run_probe_continues_after_failure_but_stops_on_skip(tmp_path, monkeypatch):
    seen: list[str] = []

    def fake_run_case(case, output, *, artifact_name=None):
        seen.append(case)
        status = {
            "bad": "DIAGNOSTIC_FAILED",
            "good": "DIAGNOSTIC_COMPLETE",
            "skip": "DIAGNOSTIC_SKIPPED",
            "after_skip": "DIAGNOSTIC_COMPLETE",
        }[case]
        return {"id": case, "artifact_name": artifact_name or case, "status": status}

    monkeypatch.setattr(probe, "run_case", fake_run_case)
    summary = probe.run_probe(["bad", "good", "skip", "after_skip"], tmp_path / "out")
    assert seen == ["bad", "good", "skip"]
    assert [row["status"] for row in summary["jobs"]] == [
        "DIAGNOSTIC_FAILED",
        "DIAGNOSTIC_COMPLETE",
        "DIAGNOSTIC_SKIPPED",
    ]
    assert summary["status"] == "DIAGNOSTIC_INCOMPLETE_OR_FAILED"


@pytest.mark.parametrize("runner_hash", ["stale-probe-source", None])
def test_resume_rejects_stale_or_missing_runner_hash(tmp_path, monkeypatch, runner_hash):
    output = tmp_path / "out"
    output.mkdir()
    old_summary = {"schema": probe.SCHEMA, "jobs": [], "cases_requested": []}
    if runner_hash is not None:
        old_summary["runner_sha256"] = runner_hash
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(old_summary), encoding="utf-8")
    before = summary_path.read_bytes()
    monkeypatch.setattr(
        probe, "run_case", lambda case, *_args, **_kwargs:
        {"id": case, "status": "DIAGNOSTIC_COMPLETE"},
    )
    with pytest.raises(ValueError, match="probe source hash"):
        probe.run_probe(["synthetic"], output)
    assert summary_path.read_bytes() == before
