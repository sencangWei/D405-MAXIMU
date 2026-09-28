import importlib.util
from pathlib import Path
import sys

import pytest


DIRECTORY = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928"
sys.path.insert(0, str(DIRECTORY))
SPEC = importlib.util.spec_from_file_location("summarize_keyframe_update", DIRECTORY/"summarize_keyframe_update.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
sys.path.pop(0)


def fixture(tmp_path):
    identity = dict(rows=1199, byte_identical=True, array_identical=True, max_component_delta=0)
    rows = [dict(frame_id=i, captured=True, sample_path=str(tmp_path/f"{i:04d}.npz")) for i in range(1000, 1121)]
    return dict(diagnostic_only=True, external_ground_truth_used=False, estimator_changed=False,
                errors=[], fixed_input_scope=[1000, 1120], rows=rows,
                source_input_sha256={str(DIRECTORY/"probe_keyframe_update.py"): check.digest(DIRECTORY/"probe_keyframe_update.py")},
                trajectory_identity={name: identity.copy() for name in ("trajectory_frames.csv", "trajectory_online_frames.csv")})


def test_complete_scope_with_full_online_identity_passes(tmp_path):
    check.validate_new_trace(fixture(tmp_path))


@pytest.mark.parametrize("fault", ["online_missing", "changed_pose", "missing_row", "duplicate_path", "capture_false", "errors", "estimator", "adapter_hash"])
def test_bad_capture_rejected(tmp_path, fault):
    sample = fixture(tmp_path)
    if fault == "online_missing": del sample["trajectory_identity"]["trajectory_online_frames.csv"]
    elif fault == "changed_pose": sample["trajectory_identity"]["trajectory_frames.csv"]["byte_identical"] = False
    elif fault == "missing_row": sample["rows"].pop()
    elif fault == "duplicate_path": sample["rows"][1]["sample_path"] = sample["rows"][0]["sample_path"]
    elif fault == "capture_false": sample["rows"][0]["captured"] = False
    elif fault == "errors": sample["errors"] = [dict(stage="raw_update")]
    elif fault == "adapter_hash": sample["source_input_sha256"][str(DIRECTORY/"probe_keyframe_update.py")] = "stale"
    else: sample["estimator_changed"] = True
    with pytest.raises(ValueError): check.validate_new_trace(sample)


def test_unknown_distribution_is_not_zero_and_nonfinite_fails():
    assert check.distribution([])["status"] == "UNKNOWN"
    with pytest.raises(ValueError): check.distribution([float("nan")])
