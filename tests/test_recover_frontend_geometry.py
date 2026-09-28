import copy
import importlib.util
from pathlib import Path
import sys

import pytest


DIRECTORY = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928"
sys.path.insert(0, str(DIRECTORY))
SPEC = importlib.util.spec_from_file_location("recover_frontend_geometry", DIRECTORY/"recover_frontend_geometry.py")
recover = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recover)
sys.path.pop(0)


def origin():
    identity = dict(rows=1199, byte_identical=True, array_identical=True, max_component_delta=0)
    return dict(diagnostic_only=True, external_ground_truth_used=False, estimator_changed=False,
                fixed_input_scope=[1000, 1120], rows=[],
                trajectory_identity={name: copy.deepcopy(identity) for name in
                                     ("trajectory_frames.csv", "trajectory_online_frames.csv")},
                errors=[dict(frame_id=i, stage="finish", error="ValueError('pixel_border invalid')")
                        for i in range(1000, 1121)])


def test_only_exact_known_failed_diagnostic_origin_is_recoverable():
    recover.check_origin(origin())


@pytest.mark.parametrize("fault", ["gt", "estimator", "scope", "online", "identity", "coverage", "error", "stage", "rows"])
def test_recovery_rejects_missing_or_changed_provenance(fault):
    trace = origin()
    if fault == "gt":
        trace["external_ground_truth_used"] = True
    elif fault == "estimator":
        trace["estimator_changed"] = True
    elif fault == "scope":
        trace["fixed_input_scope"] = [1001, 1120]
    elif fault == "online":
        del trace["trajectory_identity"]["trajectory_online_frames.csv"]
    elif fault == "identity":
        trace["trajectory_identity"]["trajectory_frames.csv"]["max_component_delta"] = 1e-12
    elif fault == "coverage":
        trace["errors"].pop()
    elif fault == "error":
        trace["errors"][0]["error"] = "ValueError('unexpected diagnostic error')"
    elif fault == "stage":
        trace["errors"][0]["stage"] = "prepare"
    elif fault == "rows":
        trace["rows"] = [dict(frame_id=1000)]
    with pytest.raises(ValueError):
        recover.check_origin(trace)


def test_recovery_refuses_to_overwrite_artifact_before_reanalysis(tmp_path):
    source, output = tmp_path/"original.json", tmp_path/"existing.json"
    source.write_text("{}")
    output.write_text("preserve me")
    with pytest.raises(FileExistsError):
        recover.recover(source, output)
    assert output.read_text() == "preserve me"
