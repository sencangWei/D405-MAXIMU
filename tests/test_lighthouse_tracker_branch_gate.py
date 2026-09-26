import csv
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/lighthouse_tracker_branch_gate.py"


@pytest.mark.parametrize("has_step,expected_rc,expected_result", [(True, 3, "REJECT"), (False, 0, "PASS")])
def test_explicit_tracker_path_and_camera_window(tmp_path, has_step, expected_rc, expected_result):
    # No default tracker.csv: checking the wrong file must fail this test.
    tracker = tmp_path / "explicit_reference.csv"
    with tracker.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["host_monotonic_ns", "px_m", "py_m", "pz_m"])
        for index in range(101):
            step = .005 if has_step and index >= 50 else 0.
            writer.writerow([10_000_000_000 + index * 10_000_000, index * .00001 + step, 0., 0.])
    with (tmp_path / "d405_frames.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["infrared_left_mono"])
        writer.writerows([[10 + index / 30] for index in range(31)])
    output = tmp_path / "evidence" / "branch.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), "--tracker-csv", str(tracker),
         "--d405-session", str(tmp_path), "--json", str(output)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == expected_rc, result.stderr + result.stdout
    report = json.loads(output.read_text())
    assert report["result"] == expected_result
    assert report["tracker_csv"] == str(tracker)
    assert (len(report["switches"]) > 0) == has_step
    assert list(output.parent.iterdir()) == [output]
