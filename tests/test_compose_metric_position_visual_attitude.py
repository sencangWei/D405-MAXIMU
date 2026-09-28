import csv
import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928/compose_metric_position_visual_attitude.py"
SPEC = importlib.util.spec_from_file_location("compose_metric_position_visual_attitude", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_trajectory(path, times, positions, quaternions):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"))
        for time, position, quaternion in zip(times, positions, quaternions):
            writer.writerow((time, *position, *quaternion))


def test_compose_preserves_metric_displacement_and_attitude(tmp_path):
    metric, attitude = tmp_path / "metric.csv", tmp_path / "attitude.csv"
    output, report = tmp_path / "combined.csv", tmp_path / "report.json"
    write_trajectory(metric, [1, 2], [[1, 0, 0], [2, 0, 0]],
                     [[1, 0, 0, 0], [1, 0, 0, 0]])
    write_trajectory(attitude, [1, 2], [[0, 0, 0], [0, 1, 0]],
                     [[0, 0, 0, 1], [0, 0, 0, 1]])
    result = MODULE.compose(metric, attitude, output, report)
    with output.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert result["external_reference_used"] is False
    assert np.allclose([[float(row[key]) for key in ("x", "y", "z")] for row in rows],
                       [[0, 0, 0], [-1, 0, 0]], atol=1e-9)
    assert all(float(row["qz"]) == 1.0 for row in rows)


def test_compose_rejects_unmatched_timestamps(tmp_path):
    metric, attitude = tmp_path / "metric.csv", tmp_path / "attitude.csv"
    write_trajectory(metric, [1, 2], [[0, 0, 0], [1, 0, 0]], [[1, 0, 0, 0]] * 2)
    write_trajectory(attitude, [1, 3], [[0, 0, 0], [1, 0, 0]], [[1, 0, 0, 0]] * 2)
    with pytest.raises(ValueError, match="identical frame timestamps"):
        MODULE.compose(metric, attitude, tmp_path / "out.csv", tmp_path / "report.json")
    assert not (tmp_path / "out.csv").exists()
