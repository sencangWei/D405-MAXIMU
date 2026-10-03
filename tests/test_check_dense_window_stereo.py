import importlib.util
from pathlib import Path

import numpy as np
import pytest


PATH = (Path(__file__).resolve().parents[1]
        / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
        / "check_dense_window_stereo.py")
spec = importlib.util.spec_from_file_location("check_dense_window_stereo", PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_reads_xyzw_native_sim3_without_scaling_position():
    row = [1, 2, 3, 0, 0, 0, 1, 0.4]
    report = {"pose_states": {"808": {"variant_after": row}}}
    np.testing.assert_array_equal(probe.state(report, 808, "variant_after"), row)


@pytest.mark.parametrize("row", [None, [0] * 7, [0] * 8,
                               [0, 0, 0, 0, 0, 0, 1, -1],
                               [float("nan"), 0, 0, 0, 0, 0, 1, 1]])
def test_rejects_missing_nonfinite_or_invalid_sim3(row):
    report = {"pose_states": {"808": {"variant_after": row}}}
    with pytest.raises(ValueError, match="Sim3|quaternion"):
        probe.state(report, 808, "variant_after")


def test_never_scores_unsolved_window(tmp_path):
    path = tmp_path / "unsolved.json"
    path.write_text('{"status":"REJECTED_DENSE_EDGES"}')
    with pytest.raises(ValueError, match="Only solved"):
        probe.main(["--probe", str(path), "--dataset", str(tmp_path),
                    "--output", str(tmp_path / "out.json")])
    assert not (tmp_path / "out.json").exists()
