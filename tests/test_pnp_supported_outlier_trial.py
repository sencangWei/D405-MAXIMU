import importlib.util
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
spec = importlib.util.spec_from_file_location("probe_pnp_supported_outlier_mask", BASE / "probe_pnp_supported_outlier_mask.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def rows():
    return [{"id": name, "status": "DIAGNOSTIC_COMPLETE", "support_preserved": True,
             "rotation_disagreement_deg": {"control": {"max": 1.2}, "filtered": {"max": 1.0}}}
            for name in sorted(probe.REQUIRED)]


def test_geometry_candidate_never_claims_ate():
    verdict = probe.evaluate_candidate(rows())
    assert verdict["candidate_status"] == "GEOMETRY_CANDIDATE_ONLY"
    assert verdict["formal_result"] == "NOT_EVALUATED_ATE"
    assert verdict["ate_claim"] is False


@pytest.mark.parametrize("value", [2.0, float("nan"), float("inf")])
def test_right_failure_boundary(value):
    values = rows()
    next(v for v in values if v["id"] == "right587")["rotation_disagreement_deg"]["filtered"]["max"] = value
    assert "right587:right_max_not_below_2deg" in probe.evaluate_candidate(values)["criterion_failures"]


def test_control_support_and_missing_execution_cannot_pass():
    values = rows()
    next(v for v in values if v["id"] == "passing_held2_752")["rotation_disagreement_deg"]["filtered"]["max"] = 1.3
    values[0]["support_preserved"] = False
    verdict = probe.evaluate_candidate(values)
    assert "passing_held2_752:passing_control_regression" in verdict["criterion_failures"]
    assert any("depleted" in item for item in verdict["criterion_failures"])
    assert "technical_execution_incomplete" in probe.evaluate_candidate(values[:-1])["criterion_failures"]
