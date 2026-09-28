import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / ".planning" / "metric_window_bundle_20260928" / "run_full_seam_controls.py"
spec = importlib.util.spec_from_file_location("run_full_seam_controls", ADAPTER)
full_seam = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = full_seam
spec.loader.exec_module(full_seam)

CASES = ["dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh1", "fresh2", "fresh3", "fresh4"]


def write_fake_summaries(output: Path, counts=(81,), mutate=None):
    output.mkdir()
    cases = []
    for index, name in enumerate(CASES):
        count = counts[index] if index < len(counts) else counts[-1]
        pair_count = len(full_seam.pair_windows(count))
        pairs = []
        windows = []
        independent = []
        for pair in range(1, pair_count + 1):
            start = 40 * (pair - 1)
            indices = list(range(start, start + 41, 5))
            pairs.append({"pair": pair, "indices": indices, "raw_frame_indices": list(range(start, start + 41))})
            for part in range(2):
                row = {"window": 2 * (pair - 1) + part + 1, "indices": indices[:5] if part == 0 else indices[4:]}
                windows.append(row)
                independent.append(dict(row))
        case = {"case": name, "windows": windows, "independent_windows": independent, "pairs": pairs}
        if mutate is not None:
            mutate(case)
        cases.append(case)
    summary = {
        "cases": cases,
        "source_sha256": {},
        "external_reference_used": False,
        "production_modified": False,
        "correlated_factors_from_shared_window": True,
    }
    (output / "summary.json").write_text(json.dumps(summary) + "\n")
    independent_cases = [{**case, "windows": case["independent_windows"]} for case in cases]
    (output / "independent_summary.json").write_text(
        json.dumps({**summary, "cases": independent_cases, "correlated_factors_from_shared_window": False}) + "\n"
    )


def test_pair_windows_schedule_41_81_1200():
    with pytest.raises(ValueError, match="too short"):
        full_seam.pair_windows(40)
    one = full_seam.pair_windows(41)
    two = full_seam.pair_windows(81)
    many = full_seam.pair_windows(1200)

    assert len(one) == 1
    np.testing.assert_array_equal(one[0], [0, 5, 10, 15, 20, 25, 30, 35, 40])
    assert len(two) == 2
    np.testing.assert_array_equal(two[1], [40, 45, 50, 55, 60, 65, 70, 75, 80])
    assert len(many) == 29
    assert [int(window[0]) for window in many] == list(range(0, 1160, 40))
    assert [int(window[-1]) for window in many] == list(range(40, 1200, 40))[:29]
    assert all(left[-1] == right[0] for left, right in zip(many[:-1], many[1:]))
    assert all(not (set(left[:-1]) & set(right[1:])) for left, right in zip(many[:-1], many[1:]))


def test_main_patches_pair_windows_restores_argv_and_annotates_both_summaries(tmp_path, monkeypatch):
    output = tmp_path / "full_seam"
    old_argv = sys.argv[:]
    original = full_seam.base.previous.pair_windows
    seen = {}

    def fake_main():
        assert full_seam.base.previous.pair_windows is not original
        seen["argv"] = sys.argv[:]
        for count in [1200] + [81] * 9:
            full_seam.base.previous.pair_windows(count)
        write_fake_summaries(output, counts=[1200] + [81] * 9)
        return "ok"

    monkeypatch.setattr(full_seam.base, "main", fake_main)

    assert full_seam.main(["--output", str(output)]) == "ok"

    assert full_seam.base.previous.pair_windows is original
    assert sys.argv == old_argv
    assert seen["argv"] == [str(ADAPTER.resolve()), "--output", str(output)]
    for name, independent in (("summary.json", False), ("independent_summary.json", True)):
        data = json.loads((output / name).read_text())
        adapter = data["full_seam_adapter"]
        assert data["source_sha256"][str(ADAPTER.resolve())] == adapter["source_sha256"]
        assert adapter["raw_interval_frames"] == 40
        assert adapter["ba_node_offsets"] == [0, 5, 10, 15, 20, 25, 30, 35, 40]
        assert adapter["pair_counts"]["dev1"] == 29
        assert adapter["pair_counts"]["dev2"] == 2
        assert adapter["case_counts"][0]["recording_raw_frame_count"] == 1200
        assert adapter["case_counts"][0]["raw_count_loaded_pairs_prefix"] == 1161
        assert adapter["case_counts"][0]["uncovered_tail_frames_after_last_endpoint"] == 39
        assert adapter["case_counts"][1]["recording_raw_frame_count"] == 81
        assert adapter["case_counts"][1]["uncovered_tail_frames_after_last_endpoint"] == 0
        assert adapter["correlated_paired_endpoints"] is True
        assert adapter["calibrated_covariance"] is False
        assert adapter["external_ground_truth_used"] is False
        assert adapter["production_selection"] is False
        assert adapter["independent_summary"] is independent
        assert adapter["leftover_tail_policy"] == "explicitly_uncovered_no_duplicate_partial_edge_no_tail_fallback"


def test_main_restores_binding_and_argv_on_base_exception(tmp_path, monkeypatch):
    old_argv = sys.argv[:]
    original = full_seam.base.previous.pair_windows

    def fail():
        assert full_seam.base.previous.pair_windows is not original
        raise RuntimeError("boom")

    monkeypatch.setattr(full_seam.base, "main", fail)

    with pytest.raises(RuntimeError, match="boom"):
        full_seam.main(["--output", str(tmp_path / "out")])

    assert full_seam.base.previous.pair_windows is original
    assert sys.argv == old_argv


def test_adapter_source_hash_guard_runs_before_annotation(tmp_path, monkeypatch):
    output = tmp_path / "full_seam"
    calls = {"count": 0}

    def fake_main():
        write_fake_summaries(output, counts=[41] * 10)

    def fake_sha(path):
        calls["count"] += 1
        return "a" * 64 if calls["count"] == 1 else "b" * 64

    monkeypatch.setattr(full_seam.base, "main", fake_main)
    monkeypatch.setattr(full_seam, "_sha", fake_sha)

    with pytest.raises(ValueError, match="adapter source changed"):
        full_seam.main(["--output", str(output)])

    data = json.loads((output / "summary.json").read_text())
    assert "full_seam_adapter" not in data


def test_annotation_refuses_wrong_case_set(tmp_path, monkeypatch):
    output = tmp_path / "full_seam"

    def fake_main():
        for _ in CASES:
            full_seam.base.previous.pair_windows(41)
        write_fake_summaries(output, counts=[41] * 10)
        data = json.loads((output / "summary.json").read_text())
        data["cases"][0]["case"] = "surprise"
        (output / "summary.json").write_text(json.dumps(data) + "\n")

    monkeypatch.setattr(full_seam.base, "main", fake_main)

    with pytest.raises(ValueError, match="exact ten"):
        full_seam.main(["--output", str(output)])


def test_annotation_refuses_missing_recording_counts(tmp_path):
    output = tmp_path / "full_seam"
    write_fake_summaries(output, counts=[41] * 10)

    with pytest.raises(ValueError, match="recording frame counts"):
        full_seam._annotate(output / "summary.json", "a" * 64, ADAPTER.resolve(), independent=False, recording_counts=[])


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda case: case["pairs"][0].update({"raw_frame_indices": list(range(1, 42))}), "raw frame indices"),
        (lambda case: case["pairs"].pop(), "pair count"),
        (lambda case: case["windows"].pop(), "joint window count"),
        (lambda case: case["independent_windows"][0].update({"indices": [1, 2, 3, 4, 5]}), "independent window indices"),
        (lambda case: case["pairs"][0].update({"indices": [0, 5, 10, 15, 20, 25, 30, 35, 41]}), "pair indices"),
    ],
)
def test_annotation_refuses_bad_pair_contracts(tmp_path, mutate, match):
    output = tmp_path / "full_seam"
    write_fake_summaries(output, counts=[81] * 10, mutate=mutate)

    with pytest.raises(ValueError, match=match):
        full_seam._annotate(
            output / "summary.json", "a" * 64, ADAPTER.resolve(),
            independent=False, recording_counts=[81] * 10,
        )
