import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / ".planning" / "metric_window_bundle_20260928" / "run_full_shape_controls.py"
SPEC = importlib.util.spec_from_file_location("run_full_shape_controls", ADAPTER)
full_shape = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = full_shape
SPEC.loader.exec_module(full_shape)


CASES = ["dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh1", "fresh2", "fresh3", "fresh4"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows_for_case(name: str, root: Path, *, independent_joint_pair="missing"):
    count = full_shape.EXPECTED_RAW_COUNTS[name]
    pairs = []
    windows = []
    for pair, indices in enumerate(full_shape.full_seam.pair_windows(count), 1):
        values = [int(v) for v in indices]
        pairs.append(
            {
                "pair": pair,
                "indices": values,
                "raw_frame_indices": list(range(values[0], values[-1] + 1)),
                "accepted": True,
                "reason": "ok",
            }
        )
        for part, half in enumerate((values[:5], values[4:])):
            windows.append(
                {
                    "window": 2 * (pair - 1) + part + 1,
                    "joint_pair": pair,
                    "indices": half,
                    "elapsed_s": [float(v) for v in half],
                    "accepted": True,
                    "reason": "ok",
                    "diagnostics": {
                        "stereo_window_shape_factor": {
                            "available": True,
                            "solver_accepted": True,
                            "not_admissible_for_graph": True,
                            "conditional_rank": 24,
                            "rank_deficient": False,
                        }
                    },
                }
            )
    independent = [dict(row) for row in windows]
    if independent_joint_pair == "missing":
        for row in independent:
            row.pop("joint_pair", None)
    elif independent_joint_pair == "bad":
        independent[0]["joint_pair"] = 99
    input_path = root / f"{name}.input"
    input_path.write_text(f"input {name}")
    return {
        "case": name,
        "windows": windows,
        "independent_windows": independent,
        "pairs": pairs,
        "input_sha256": {str(input_path): sha(input_path)},
        "decoded_grayscale_frame_sha256": {f"left:{name}:0": "d" * 64},
        "external_reference_used": False,
    }


def write_summaries(output: Path, *, independent_joint_pair="missing", mutate=None):
    output.mkdir(parents=True, exist_ok=True)
    cases = [rows_for_case(name, output, independent_joint_pair=independent_joint_pair) for name in CASES]
    if mutate is not None:
        mutate(cases)
    joint = {
        "cases": cases,
        "source_sha256": {},
        "external_reference_used": False,
        "production_modified": False,
        "correlated_factors_from_shared_window": True,
    }
    independent = [{**case, "windows": case["independent_windows"]} for case in cases]
    (output / "summary.json").write_text(json.dumps(joint) + "\n")
    (output / "independent_summary.json").write_text(
        json.dumps({**joint, "cases": independent, "correlated_factors_from_shared_window": False}) + "\n"
    )


def patch_preflight(monkeypatch, hashes=None):
    frozen = {"input": "a" * 64} if hashes is None else hashes
    monkeypatch.setattr(full_shape, "preflight_inputs", lambda: dict(frozen))
    monkeypatch.setattr(full_shape, "verify_input_hashes", lambda actual: None)


def test_main_patches_full_schedule_and_shape_solver_then_restores(tmp_path, monkeypatch):
    output = tmp_path / "full_shape"
    original_pair_windows = full_shape.seam.previous.pair_windows
    original_solve = full_shape.seam.previous.solve
    original_argv = sys.argv[:]
    seen = {}
    patch_preflight(monkeypatch)

    def fake_main():
        seen["pair_windows_patched"] = full_shape.seam.previous.pair_windows is full_shape.full_seam.pair_windows
        seen["solve_patched"] = full_shape.seam.previous.solve is full_shape.shape_adapter.solve_with_shape_factor
        seen["argv"] = sys.argv[:]
        write_summaries(output)
        return "ok"

    monkeypatch.setattr(full_shape.seam, "main", fake_main)

    assert full_shape.main(["--output", str(output)]) == "ok"

    assert full_shape.seam.previous.pair_windows is original_pair_windows
    assert full_shape.seam.previous.solve is original_solve
    assert sys.argv == original_argv
    assert seen == {
        "pair_windows_patched": True,
        "solve_patched": True,
        "argv": [str(Path(full_shape.seam.__file__).resolve()), "--output", str(output)],
    }
    joint = json.loads((output / "summary.json").read_text())
    independent = json.loads((output / "independent_summary.json").read_text())
    for summary, flag in ((joint, False), (independent, True)):
        meta = summary["full_shape_window_adapter"]
        assert meta["diagnostic_only"] is True
        assert meta["external_ground_truth_used"] is False
        assert meta["used_for_graph_or_selection"] is False
        assert meta["available_for_graph"] is False
        assert meta["calibrated_covariance"] is False
        assert meta["statistical_independence_claimed"] is False
        assert meta["independent_summary"] is flag
        assert meta["joint_pairs_per_case"] == 29
        assert meta["endpoint_rows_per_case"] == 58
        assert meta["pair_group_counts"]["available"] == 290
        assert meta["endpoint_diagnostic_counts"]["available"] == 580
        assert meta["case_counts"]["dev1"]["uncovered_tail_frames_after_last_endpoint"] == 38
        assert meta["case_counts"]["dev2"]["uncovered_tail_frames_after_last_endpoint"] == 39
        assert str(ADAPTER.resolve()) in summary["source_sha256"]
        assert str(Path(full_shape.full_seam.__file__).resolve()) in summary["source_sha256"]
        assert str(Path(full_shape.shape_adapter.__file__).resolve()) in summary["source_sha256"]
    assert sum(len(case["pairs"]) for case in joint["cases"]) == 290
    assert sum(len(case["windows"]) for case in joint["cases"]) == 580


def test_refuses_existing_output_unknown_cli_and_restores_on_failure(tmp_path, monkeypatch):
    existing = tmp_path / "exists"
    existing.mkdir()
    original_pair_windows = full_shape.seam.previous.pair_windows
    original_solve = full_shape.seam.previous.solve
    monkeypatch.setattr(full_shape.seam, "main", lambda: pytest.fail("base main must not run"))
    patch_preflight(monkeypatch)
    with pytest.raises(ValueError, match="overwrite"):
        full_shape.main(["--output", str(existing)])
    assert full_shape.seam.previous.pair_windows is original_pair_windows
    assert full_shape.seam.previous.solve is original_solve

    with pytest.raises(SystemExit):
        full_shape.main(["--output", str(tmp_path / "new"), "--extra"])
    assert not (tmp_path / "new").exists()
    assert full_shape.seam.previous.pair_windows is original_pair_windows
    assert full_shape.seam.previous.solve is original_solve

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(full_shape.seam, "main", boom)
    with pytest.raises(RuntimeError, match="boom"):
        full_shape.main(["--output", str(tmp_path / "boom")])
    assert full_shape.seam.previous.pair_windows is original_pair_windows
    assert full_shape.seam.previous.solve is original_solve


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda cases: cases.append({**cases[0], "case": "extra"}), "exact ten"),
        (lambda cases: cases.__setitem__(0, {**cases[0], "case": "wrong"}), "known cases"),
        (lambda cases: cases[0]["pairs"].pop(), "29 seam pairs"),
        (lambda cases: cases[0]["pairs"][0].update({"indices": [1, 5, 10, 15, 20, 25, 30, 35, 40]}), "pair schedule"),
        (lambda cases: cases[0]["pairs"][0].update({"raw_frame_indices": list(range(1, 42))}), "raw frame indices"),
        (lambda cases: cases[0]["windows"][0].update({"indices": [1, 2, 3, 4, 5]}), "endpoint indices"),
        (lambda cases: cases[0]["windows"][0].update({"window": 9}), "window ids"),
    ],
)
def test_full_schedule_validation_rejects_malformed_rows(tmp_path, mutate, match):
    output = tmp_path / "bad"
    write_summaries(output, mutate=mutate)
    with pytest.raises(ValueError, match=match):
        full_shape.annotate(output, full_shape.hash_sources())


def test_validation_allows_missing_independent_joint_pair_but_rejects_bad_present_value(tmp_path):
    ok = tmp_path / "ok"
    write_summaries(ok, independent_joint_pair="missing")
    full_shape._validate_pair_summaries(
        json.loads((ok / "summary.json").read_text()),
        json.loads((ok / "independent_summary.json").read_text()),
    )

    bad = tmp_path / "bad"
    write_summaries(bad, independent_joint_pair="bad")
    with pytest.raises(ValueError, match="independent joint_pair"):
        full_shape._validate_pair_summaries(
            json.loads((bad / "summary.json").read_text()),
            json.loads((bad / "independent_summary.json").read_text()),
        )


def test_real_shape_pair_success_schema_optional_independent_joint_pair():
    raw = ROOT / "reports" / "metric_window_bundle_20260928" / "shape_pairs_ten_v1"
    if not (raw / "summary.json").exists():
        pytest.skip("real raw shape-pairs summary not present")
    full_shape.shape_adapter._validate_pair_summaries(
        json.loads((raw / "summary.json").read_text()),
        json.loads((raw / "independent_summary.json").read_text()),
    )


def test_source_conflict_and_deleted_inputs_fail_before_either_summary_written(tmp_path):
    output = tmp_path / "guard"
    write_summaries(output)
    source_hashes = full_shape.hash_sources()
    joint = json.loads((output / "summary.json").read_text())
    independent = json.loads((output / "independent_summary.json").read_text())
    joint["source_sha256"][str(ADAPTER.resolve())] = "0" * 64
    (output / "summary.json").write_text(json.dumps(joint) + "\n")
    before_independent = (output / "independent_summary.json").read_text()
    with pytest.raises(ValueError, match="source hash conflict"):
        full_shape.annotate(output, source_hashes)
    assert (output / "independent_summary.json").read_text() == before_independent

    deleted = tmp_path / "deleted"
    write_summaries(deleted)
    data = json.loads((deleted / "summary.json").read_text())
    input_path = Path(next(iter(data["cases"][0]["input_sha256"])))
    input_path.unlink()
    with pytest.raises(ValueError, match="input missing"):
        full_shape.annotate(deleted, source_hashes)


def test_preflight_rejects_wrong_actual_counts_before_seam_main(tmp_path, monkeypatch):
    trajectory = tmp_path / "traj.csv"
    trajectory.write_text("trajectory")
    report = tmp_path / "stereo_report.json"
    graph = tmp_path / "graph.json"
    imu_cal = tmp_path / "imu.yaml"
    vins_cal = tmp_path / "vins.yaml"
    frames = tmp_path / "d405_frames.csv"
    imu = tmp_path / "external_imu" / "imu.bin"
    dataset_frames = tmp_path / "dataset" / "frames.csv"
    imu.parent.mkdir()
    dataset_frames.parent.mkdir()
    for path in [imu_cal, vins_cal, frames, imu, dataset_frames]:
        path.write_text("x")
    report.write_text(json.dumps({"trajectory": str(trajectory)}) + "\n")
    graph.write_text(
        json.dumps(
            {
                "inputs": {
                    "stereo_report": str(report),
                    "session": str(tmp_path),
                    "imu_calibration": str(imu_cal),
                    "vins_spatiotemporal_calibration": str(vins_cal),
                }
            }
        )
        + "\n"
    )
    monkeypatch.setattr(full_shape, "case_graphs", lambda: [("dev1", graph)])
    monkeypatch.setattr(full_shape.seam.previous.base.stereo, "load_trajectory", lambda path: (list(range(1185)), None))
    monkeypatch.setattr(full_shape.seam, "main", lambda: pytest.fail("seam main must not run"))

    with pytest.raises(ValueError, match="dev1 recording raw frame count 1185 != expected 1199"):
        full_shape.main(["--output", str(tmp_path / "out")])

    monkeypatch.setattr(full_shape.seam.previous.base.stereo, "load_trajectory", lambda path: (list(range(1200)), None))
    with pytest.raises(ValueError, match="dev1 recording raw frame count 1200 != expected 1199"):
        full_shape.main(["--output", str(tmp_path / "out2")])


def test_preflight_accepts_all_ten_counts_and_detects_input_change(tmp_path, monkeypatch):
    graphs = []
    trajectories = {}
    for name in CASES:
        root = tmp_path / name
        root.mkdir()
        trajectory = root / "traj.csv"
        trajectory.write_text(f"trajectory {name}")
        report = root / "stereo_report.json"
        graph = root / "graph.json"
        imu_cal = root / "imu.yaml"
        vins_cal = root / "vins.yaml"
        frames = root / "d405_frames.csv"
        imu = root / "external_imu" / "imu.bin"
        dataset_frames = root / "dataset" / "frames.csv"
        imu.parent.mkdir()
        dataset_frames.parent.mkdir()
        for path in [imu_cal, vins_cal, frames, imu, dataset_frames]:
            path.write_text(f"x {name}")
        report.write_text(json.dumps({"trajectory": str(trajectory)}) + "\n")
        graph.write_text(
            json.dumps(
                {
                    "inputs": {
                        "stereo_report": str(report),
                        "session": str(root),
                        "imu_calibration": str(imu_cal),
                        "vins_spatiotemporal_calibration": str(vins_cal),
                    }
                }
            )
            + "\n"
        )
        graphs.append((name, graph))
        trajectories[str(trajectory)] = full_shape.EXPECTED_RAW_COUNTS[name]

    monkeypatch.setattr(full_shape, "case_graphs", lambda: graphs)
    monkeypatch.setattr(
        full_shape.seam.previous.base.stereo,
        "load_trajectory",
        lambda path: (list(range(trajectories[str(path)])), None),
    )

    hashes = full_shape.preflight_inputs()
    assert len(hashes) == 80
    full_shape.verify_input_hashes(hashes)
    changed = Path(next(iter(hashes)))
    changed.write_text("changed")
    with pytest.raises(ValueError, match="input changed"):
        full_shape.verify_input_hashes(hashes)


def test_preflight_rejects_shifted_29_pair_schedule_before_seam_main(tmp_path, monkeypatch):
    trajectory = tmp_path / "traj.csv"
    trajectory.write_text("trajectory")
    report = tmp_path / "stereo_report.json"
    graph = tmp_path / "graph.json"
    imu_cal = tmp_path / "imu.yaml"
    vins_cal = tmp_path / "vins.yaml"
    frames = tmp_path / "d405_frames.csv"
    imu = tmp_path / "external_imu" / "imu.bin"
    dataset_frames = tmp_path / "dataset" / "frames.csv"
    imu.parent.mkdir()
    dataset_frames.parent.mkdir()
    for path in [imu_cal, vins_cal, frames, imu, dataset_frames]:
        path.write_text("x")
    report.write_text(json.dumps({"trajectory": str(trajectory)}) + "\n")
    graph.write_text(
        json.dumps(
            {
                "inputs": {
                    "stereo_report": str(report),
                    "session": str(tmp_path),
                    "imu_calibration": str(imu_cal),
                    "vins_spatiotemporal_calibration": str(vins_cal),
                }
            }
        )
        + "\n"
    )
    monkeypatch.setattr(full_shape, "case_graphs", lambda: [("dev1", graph)])
    monkeypatch.setattr(full_shape.seam.previous.base.stereo, "load_trajectory", lambda path: (list(range(1199)), None))
    monkeypatch.setattr(
        full_shape.full_seam,
        "pair_windows",
        lambda count: [list(range(start + 1, start + 42, 5)) for start in range(0, 1160, 40)],
    )
    monkeypatch.setattr(full_shape.seam, "main", lambda: pytest.fail("seam main must not run"))

    with pytest.raises(ValueError, match="literal 0..1160"):
        full_shape.main(["--output", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


def test_source_hash_verified_before_seam_main(tmp_path, monkeypatch):
    output = tmp_path / "out"
    calls = []
    patch_preflight(monkeypatch)
    monkeypatch.setattr(full_shape, "hash_sources", lambda: {"source": "a" * 64})

    def verify(hashes):
        calls.append("verify")
        raise ValueError("source changed before run")

    monkeypatch.setattr(full_shape, "verify_hashes", verify)
    monkeypatch.setattr(full_shape.seam, "main", lambda: pytest.fail("seam main must not run"))
    with pytest.raises(ValueError, match="source changed before run"):
        full_shape.main(["--output", str(output)])
    assert calls == ["verify"]
    assert not output.exists()


def test_real_full_seam_summary_schema_validates_full_contract():
    raw = ROOT / "reports" / "metric_window_bundle_20260928" / "seam_full_ten_v1"
    if not (raw / "summary.json").exists():
        pytest.skip("real full seam summary not present")
    counts = full_shape._validate_pair_summaries(
        json.loads((raw / "summary.json").read_text()),
        json.loads((raw / "independent_summary.json").read_text()),
    )
    assert counts["dev1"]["recording_raw_frame_count"] == 1199
    assert counts["dev2"]["recording_raw_frame_count"] == 1200
    assert all(row["pair_count"] == 29 for row in counts.values())
