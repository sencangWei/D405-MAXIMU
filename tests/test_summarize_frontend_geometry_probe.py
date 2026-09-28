import importlib.util
import copy
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928/summarize_frontend_geometry_probe.py"
SPEC = importlib.util.spec_from_file_location("summarize_frontend_geometry_probe", PATH)
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def test_distribution_is_per_frame_and_empty_is_unknown():
    assert summary.distribution([2, 4, 9]) == dict(status="OK", count=3, min=2., median=4., max=9.)
    assert summary.distribution([]) == dict(status="UNKNOWN", count=0)


def test_nonfinite_is_not_silently_dropped():
    with pytest.raises(ValueError, match="nonfinite"):
        summary.distribution([1, float("nan")])


def trace_fixture():
    identity = dict(rows=1199, byte_identical=True, array_identical=True, max_component_delta=0)
    return dict(diagnostic_only=True, external_ground_truth_used=False, estimator_changed=False,
                errors=[], fixed_input_scope=[1000, 1120],
                rows=[dict(frame_id=i, sample_path=f"/fixed/samples/{i:04d}.npz") for i in range(1000, 1121)],
                trajectory_identity={name: copy.deepcopy(identity) for name in
                                     ("trajectory_frames.csv", "trajectory_online_frames.csv")})


def test_successful_control_and_explicit_recovery_are_distinguished():
    trace = trace_fixture()
    summary.validate_trace("fresh1", trace)
    with pytest.raises(ValueError, match="recovery semantics"):
        summary.validate_trace("fresh4", trace)
    trace.update(gpu_or_pose_replay_rerun=False, recovered_pure_analysis=True,
                 original_capture_status="FAILED_DIAGNOSTIC_MATH")
    summary.validate_trace("fresh4", trace)
    with pytest.raises(ValueError, match="normal successful"):
        summary.validate_trace("heldout1", trace)


@pytest.mark.parametrize("fault", ["duplicate", "stem", "coverage", "scope", "online", "identity", "gt"])
def test_incomplete_or_unbound_trace_is_rejected(fault):
    trace = trace_fixture()
    if fault == "duplicate":
        trace["rows"][1]["sample_path"] = trace["rows"][0]["sample_path"]
    elif fault == "stem":
        trace["rows"][0]["sample_path"] = "/fixed/samples/9999.npz"
    elif fault == "coverage":
        trace["rows"].pop()
    elif fault == "scope":
        trace["fixed_input_scope"] = [1001, 1120]
    elif fault == "online":
        del trace["trajectory_identity"]["trajectory_online_frames.csv"]
    elif fault == "identity":
        trace["trajectory_identity"]["trajectory_frames.csv"]["byte_identical"] = False
    elif fault == "gt":
        trace["external_ground_truth_used"] = True
    with pytest.raises(ValueError):
        summary.validate_trace("fresh1", trace)
