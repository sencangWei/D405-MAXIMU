import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / ".planning" / "metric_window_bundle_20260928" / "run_full_coverage_controls.py"
spec = importlib.util.spec_from_file_location("run_full_coverage_controls", ADAPTER)
coverage = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = coverage
spec.loader.exec_module(coverage)


def test_windows_rejects_recordings_shorter_than_one_interval():
    with pytest.raises(ValueError, match="too short"):
        coverage.windows(20)


def test_windows_cover_1199_frames_with_nonoverlapping_20_frame_intervals():
    windows = coverage.windows(1199)

    assert len(windows) == 59
    np.testing.assert_array_equal(windows[0], [0, 5, 10, 15, 20])
    np.testing.assert_array_equal(windows[-1], [1160, 1165, 1170, 1175, 1180])
    starts = [int(window[0]) for window in windows]
    ends = [int(window[-1]) for window in windows]
    assert starts == list(range(0, 1180, 20))
    assert ends == list(range(20, 1200, 20))[:59]
    assert all(a == b for a, b in zip(ends[:-1], starts[1:]))


def test_main_delegates_base_harness_and_records_adapter_source_hash(tmp_path, monkeypatch):
    output = tmp_path / "controls"
    seen = {}
    old_argv = sys.argv[:]
    original_windows = coverage.base.windows

    def fake_base_main():
        assert coverage.base.windows is coverage.windows
        seen["argv"] = sys.argv[:]
        output.mkdir()
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "cases": [
                        {"case": "dev1", "windows": [{"window": 1}, {"window": 2}]}
                    ],
                    "external_reference_used": False,
                    "source_sha256": {},
                }
            )
            + "\n"
        )
        return None

    monkeypatch.setattr(coverage.base, "main", fake_base_main)

    assert coverage.main(["--output", str(output)]) is None

    summary = json.loads((output / "summary.json").read_text())
    adapter = summary["full_coverage_adapter"]
    assert seen["argv"] == [str(ADAPTER.resolve()), "--output", str(output)]
    assert adapter["source"] == str(ADAPTER.resolve())
    assert len(adapter["source_sha256"]) == 64
    assert adapter["interval_frames"] == 20
    assert adapter["ba_node_offsets"] == [0, 5, 10, 15, 20]
    assert adapter["windows_per_case"] == [2]
    assert adapter["leftover_tail_policy"] == "explicitly_uncovered_no_correlated_duplicate_edge"
    assert adapter["external_ground_truth_used"] is False
    assert adapter["production_selection"] is False
    assert coverage.base.windows is original_windows
    assert sys.argv == old_argv
