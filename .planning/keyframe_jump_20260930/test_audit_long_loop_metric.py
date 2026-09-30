import json

from audit_long_loop_metric import long_pairs


def test_long_pairs_only_selects_distant_accepted_edges(tmp_path):
    events = tmp_path / "events.jsonl"
    entries = [
        {"event": "add_factors", "accepted": [
            {"source_frames": [10, 200], "valid_match_fraction_i": 0.5,
             "valid_match_fraction_j": 0.4},
            {"source_frames": [10, 50], "valid_match_fraction_i": 0.9,
             "valid_match_fraction_j": 0.9}]},
        {"event": "add_factors", "accepted": [
            {"source_frames": [10, 200], "valid_match_fraction_i": 0.6,
             "valid_match_fraction_j": 0.55},
            {"source_frames": [30, 400], "valid_match_fraction_i": 0.6,
             "valid_match_fraction_j": 0.3}]},
    ]
    events.write_text("\n".join(json.dumps(entry) for entry in entries))
    assert long_pairs(events, minimum_gap=100) == [
        ((10, 200), 0.55), ((30, 400), 0.3)
    ]
