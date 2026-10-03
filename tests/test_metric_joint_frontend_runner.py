import csv
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("metric_joint_frontend_runner", ROOT / "scripts/run_experimental_metric_joint_frontend.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def dataset_fixture(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    raw = tmp_path / "raw_frames.csv"
    raw.write_text("authoritative fixture")
    manifest = {
        "stream": "infrared_left", "slam_supervision": False, "source_session": "same_session",
        "source_frames_csv": str(raw), "source_frames_csv_sha256": runner.sha(raw), "every": 1, "start_index": 0,
    }
    (dataset / "dataset_manifest.json").write_text(json.dumps(manifest))
    with (dataset / "frames.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("input_index", "t_sec"))
        writer.writeheader()
        writer.writerows([{"input_index": i, "t_sec": i + 1.} for i in range(3)])
    monkeypatch.setattr(runner, "load_authoritative_frames", lambda *args: [{"t_sec": i + 1.} for i in range(3)])
    return dataset


def test_full_source_accepts_exact_authoritative_clock(tmp_path, monkeypatch):
    dataset = dataset_fixture(tmp_path, monkeypatch)
    _, count = runner.complete_source(dataset, dataset, "left")
    assert count == 3


def test_source_prefix_is_not_counted_as_full_replay(tmp_path, monkeypatch):
    dataset = dataset_fixture(tmp_path, monkeypatch)
    rows = (dataset / "frames.csv").read_text().splitlines()
    (dataset / "frames.csv").write_text("\n".join(rows[:-1]) + "\n")
    with pytest.raises(ValueError, match="prefix/subset"):
        runner.complete_source(dataset, dataset, "left")


def test_source_wrong_eye_is_not_depth_relabeling(tmp_path, monkeypatch):
    dataset = dataset_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="eye mismatch"):
        runner.complete_source(dataset, dataset, "right")


def test_right_source_validates_right_raw_clock_not_left_clock(tmp_path, monkeypatch):
    paired = dataset_fixture(tmp_path, monkeypatch)
    native = tmp_path / "right_dataset"
    native.mkdir()
    manifest = json.loads((paired / "dataset_manifest.json").read_text())
    manifest["stream"] = "infrared_right"
    (native / "dataset_manifest.json").write_text(json.dumps(manifest))
    (native / "frames.csv").write_bytes((paired / "frames.csv").read_bytes())
    calls = []

    def raw_clock(path, stream):
        calls.append(stream)
        shift = .01 if stream == "infrared_right" else 0.
        return [{"t_sec": i + 1. + shift} for i in range(3)]

    monkeypatch.setattr(runner, "load_authoritative_frames", raw_clock)
    with pytest.raises(ValueError, match="authoritative full clock"):
        runner.complete_source(native, paired, "right")
    assert calls == ["infrared_right"]


def test_empty_or_baseline_only_solve_log_does_not_pass(tmp_path):
    path = tmp_path / "solves.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="no graph solves"):
        runner.validate_solves(path, {"code_sha256": {}})
    path.write_text(json.dumps({"mode": "NATIVE_NO_ACCEPTED_METRIC_PAIR"}) + "\n")
    with pytest.raises(ValueError, match="metric contributions"):
        runner.validate_solves(path, {"code_sha256": {}})


def test_bound_actual_joint_solves_are_not_ate_acceptance(tmp_path):
    path = tmp_path / "solves.jsonl"
    record = {"mode": "JOINT_METRIC_NATIVE", "external_ground_truth_used": False,
              "factor_count": 2, "accepted_pair_count": 1, "code_sha256": {"factor": "sha"}, "precision_pass": False}
    path.write_text(json.dumps(record) + "\n")
    assert runner.validate_solves(path, {"code_sha256": {"factor": "sha"}}) == 1
    record["code_sha256"]["factor"] = "changed"
    path.write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="metric contributions"):
        runner.validate_solves(path, {"code_sha256": {"factor": "sha"}})


def test_effective_config_freeze_preserves_baseline_and_only_enables_joint(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("single_thread: true\ndataset: {subsample: 1}\nlocal_opt: {pin: 1}\ntracking: {imu_rotation_prior: true, imu_rotation_constraint_weight: 0.6}\n")
    actual = runner.freeze_config(path)
    assert actual == {
        "single_thread": True, "dataset": {"subsample": 1}, "local_opt": {"pin": 1},
        "tracking": {"imu_rotation_prior": True, "imu_rotation_constraint_weight": .6, "metric_relative_joint": True},
    }


def test_effective_config_rejects_subsample_or_multiple_threads(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("single_thread: false\ndataset: {subsample: 1}\n")
    with pytest.raises(ValueError, match="single-thread full-frame"):
        runner.freeze_config(path)
