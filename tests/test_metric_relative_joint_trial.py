import importlib.util
from pathlib import Path

import pytest
import torch


BASE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
spec = importlib.util.spec_from_file_location("probe_metric_relative_joint", BASE / "probe_metric_relative_joint.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def rows():
    return [{"id": name, "status": "DIAGNOSTIC_COMPLETE", "support_preserved": True,
             "native_objective": {"common_cost_ratio": 1.01},
             "rotation_disagreement_deg": {"control": {"max": 1.2}, "filtered": {"max": 1.0}}}
            for name in sorted(probe.REQUIRED)]


def test_unmet_geometry_proxy_does_not_discard_partial_progress_or_claim_ate():
    values = rows()
    next(v for v in values if v["id"] == "right587")["rotation_disagreement_deg"] = {
        "control": {"max": 9.2}, "filtered": {"max": 7.5}}
    result = probe.verdict(values)
    assert result["source_geometry_criterion_met"] is False
    assert result["candidate_status"] == "SOURCE_TRIAL_COMPLETE_REQUIRES_FIXED10_ATE"
    assert result["discard_on_geometry_criterion_alone"] is False
    assert result["formal_result"] == "NOT_EVALUATED_ATE"
    assert result["ate_claim"] is False


def test_technical_execution_failure_is_not_a_scientific_ate_rejection():
    values = rows()
    values[0] = {"id": values[0]["id"], "status": "DIAGNOSTIC_FAILED", "error": "repeat/pin"}
    result = probe.verdict(values)
    assert result["candidate_status"] == "TECHNICAL_EXECUTION_FAILED"
    assert result["discard_on_geometry_criterion_alone"] is False
    assert result["formal_result"] == "NOT_EVALUATED_ATE"


def test_passing_dense_objective_regression_remains_visible():
    values = rows()
    next(v for v in values if v["id"] == "passing_held2_752")["native_objective"]["common_cost_ratio"] = 2.1
    result = probe.verdict(values)
    assert result["source_geometry_criterion_met"] is False
    assert "passing_held2_752:passing_dense_objective_exploded" in result["source_geometry_criterion_failures"]
    assert result["ate_claim"] is False


def test_repeat_and_pin_failures_are_separate_exact_checks():
    initial = torch.zeros((3, 8))
    variant = initial.clone()
    repeat = variant.clone()
    repeat[1, 0] = 1e-7
    result = probe.replay_invariants(initial, variant, repeat)
    assert result["repeat_exact"] is False
    assert result["pin_exact"] is True
    assert result["repeat_max_abs"] > 0
    assert result["pin_max_abs"] == 0
    assert result["variant_pose_sha256"] != result["repeat_pose_sha256"]
    variant[0, 0] = .001
    result = probe.replay_invariants(initial, variant, variant.clone())
    assert result["repeat_exact"] is True
    assert result["pin_exact"] is False
    assert result["pin_max_abs"] > 0


def test_invalid_replay_poses_do_not_receive_successful_invariants():
    initial = torch.zeros((3, 8))
    with pytest.raises(ValueError, match="dimensions"):
        probe.replay_invariants(initial, initial[:-1], initial)
    variant = initial.clone()
    variant[1, 0] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        probe.replay_invariants(initial, variant, initial)
