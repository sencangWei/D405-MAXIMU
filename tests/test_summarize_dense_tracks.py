import importlib.util
from pathlib import Path

import numpy as np


PATH = Path(__file__).resolve().parents[1]/".planning/frontend_pivot_20260929/summarize_dense_tracks.py"
SPEC = importlib.util.spec_from_file_location("summarize_dense_tracks", PATH)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_group_tracks_are_scoped_to_same_anchor_and_entire_span():
    rows = [
        dict(frame_id=10, keyframe_id=8, captured=True, ids=np.array([0, 1, 2, 4])),
        dict(frame_id=11, keyframe_id=8, captured=True, ids=np.array([1, 2, 3, 4])),
        dict(frame_id=12, keyframe_id=8, captured=True, ids=np.array([2, 3, 4, 5])),
        dict(frame_id=13, keyframe_id=12, captured=True, ids=np.array([0, 2])),
    ]
    groups = mod.summarize_groups(rows)
    assert [g["keyframe_id"] for g in groups] == [8, 12]
    assert groups[0]["full_span_shared"] == 2
    assert groups[0]["adjacent_shared_min"] == 3
    assert groups[0]["frames"] == 3
    assert groups[1]["full_span_shared"] == 2
