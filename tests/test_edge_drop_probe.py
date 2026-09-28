"""The causal edge probe never selects a pair from external GT fields."""
import importlib.util
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928/edge_drop_probe/sitecustomize.py"
spec = importlib.util.spec_from_file_location("edge_drop_probe", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_only_onboard_geometry_rejections_are_selected():
    report = {"schema": "accepted_backend_edge_stereo_check_v1",
              "results": [{"first_raw": 1, "second_raw": 2, "accepted": True},
                          {"first_raw": 2, "second_raw": 3, "accepted": False}]}
    assert module.rejected_pairs(report) == {(2, 3)}


def test_unknown_report_schema_is_rejected():
    with pytest.raises(ValueError, match="schema"):
        module.rejected_pairs({"schema": "external_ground_truth", "results": []})


def test_explicit_onboard_only_selected_pairs():
    report = {"schema": "selected_backend_edge_causal_probe_v1",
              "external_reference_used": False,
              "selected_pairs": [{"first_raw": 909, "second_raw": 1085}]}
    assert module.rejected_pairs(report) == {(909, 1085)}
    report["external_reference_used"] = True
    with pytest.raises(ValueError, match="external reference"):
        module.rejected_pairs(report)
