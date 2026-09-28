import importlib.util
from pathlib import Path

import numpy as np
import pytest


PATH = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928/summarize_map_and_right.py"
SPEC = importlib.util.spec_from_file_location("summarize_map_and_right", PATH)
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def test_empty_is_unknown_not_zero_error():
    result = summary.summarize([])
    assert result["frame_count"] == 0
    assert result["fb_closure_norm_px_stats"]["status"] == "UNKNOWN"
    assert result["absolute_z_delta_learned_units"]["status"] == "UNKNOWN"


def test_failed_lk_counts_as_zero_support_but_unknown_error():
    row = dict(right_temporal=dict(native_bounds_count=100, status="UNKNOWN"),
               map_update=dict(status="UNKNOWN"), continuity=dict(status="UNKNOWN"))
    result = summary.summarize([row])
    assert result["forward_support_fraction"]["median"] == 0
    assert result["fb_closure_norm_px_stats"]["unknown_frames"] == 1
    assert result["fb_closure_norm_px_stats"]["status"] == "UNKNOWN"


def test_summary_is_framewise_not_pooled_point_counts():
    rows = [dict(right_temporal=dict(native_bounds_count=100, usable_expected_comparison_count=count,
                                    fb_closure_norm_px_stats=dict(status="OK", count=count, median=value)),
                 map_update=dict(status="UNKNOWN"), continuity=dict(status="UNKNOWN"))
            for count, value in ((99, 1), (1, 9))]
    result = summary.summarize(rows)
    assert result["fb_closure_norm_px_stats"]["median"] == 5
    assert result["forward_support_fraction"]["median"] == .5


def test_nonfinite_summary_rejected():
    with pytest.raises(ValueError):
        summary.distribution([np.nan])


def test_wrong_scope_or_estimator_semantics_fail_closed():
    with pytest.raises(ValueError):
        summary.validate(dict(diagnostic_only=False))
    with pytest.raises(ValueError):
        summary.validate(dict(diagnostic_only=True, external_ground_truth_used=False,
                              estimator_changed=False, gpu_replay_used=False,
                              fixed_input_scope=[1000, 1120], cases={"fresh4": {}}))
