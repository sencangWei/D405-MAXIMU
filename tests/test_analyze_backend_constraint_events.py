import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928"))
from analyze_backend_constraint_events import summarize_events


def test_accepted_edge_and_pose_change_are_observational():
    events = [
        {"event": "add_factors", "accepted": [{"source_frames": [1000, 1055],
          "keyframes": [1, 2], "valid_match_fraction_i": .5,
          "valid_match_fraction_j": .6}]},
        {"event": "solve", "factor_count": 1,
         "before": [[1, 1000, [0, 0, 0, 0, 0, 0, 1, 1]],
                    [2, 1055, [1, 0, 0, 0, 0, 0, 1, 1]]],
         "after": [[1, 1000, [0, 0, 0, 0, 0, 0, 1, 1]],
                   [2, 1055, [2, 0, 0, 0, 0, 0, 1, 1]]]},
    ]
    result = summarize_events(events)
    assert result["accepted_edges"] == 1
    assert result["accepted_edges_into_1000_1120"][0]["raw_frame_gap"] == 55
    assert result["solves"][0]["current_translation_change_native"] == pytest.approx(1)


def test_missing_solve_is_rejected():
    with pytest.raises(ValueError, match="coverage incomplete"):
        summarize_events([{"event": "add_factors", "accepted": []}])


def test_initial_empty_solve_is_not_treated_as_a_pose_change():
    events = [{"event": "add_factors", "accepted": []},
              {"event": "solve", "before": [], "after": [], "factor_count": 0}]
    result = summarize_events(events)
    assert result["solve_calls"] == 1
    assert result["solves"] == []
