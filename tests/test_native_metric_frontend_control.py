import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

control_spec = importlib.util.spec_from_file_location(
    "native_metric_frontend_control", ROOT / "scripts/run_native_metric_frontend_control.py"
)
control = importlib.util.module_from_spec(control_spec)
control_spec.loader.exec_module(control)

joint_spec = importlib.util.spec_from_file_location(
    "metric_joint_frontend_runner", ROOT / "scripts/run_experimental_metric_joint_frontend.py"
)
joint = importlib.util.module_from_spec(joint_spec)
joint_spec.loader.exec_module(joint)


def write_dataset(path: Path, raw: Path, stream: str = "infrared_left") -> Path:
    path.mkdir()
    manifest = {
        "stream": stream,
        "slam_supervision": False,
        "source_session": "same_session",
        "source_frames_csv": str(raw),
        "source_frames_csv_sha256": control.sha(raw),
        "every": 1,
        "start_index": 0,
        "stereo_depth_source": {"right_directory": "right"},
    }
    (path / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "calibration.yaml").write_text("camera: fixture\n", encoding="utf-8")
    with (path / "frames.csv").open("w", newline="", encoding="utf-8") as stream_file:
        writer = csv.DictWriter(stream_file, fieldnames=("input_index", "t_sec", "image"))
        writer.writeheader()
        writer.writerows(
            {"input_index": i, "t_sec": f"{i + 1:.1f}", "image": f"{i:06d}.png"}
            for i in range(3)
        )
    return path


def test_effective_config_differs_from_metric_runner_only_by_joint_flag(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "single_thread: true\n"
        "dataset: {subsample: 1}\n"
        "local_opt: {pin: 1}\n"
        "tracking: {imu_rotation_prior: true, imu_rotation_constraint_weight: 0.6}\n",
        encoding="utf-8",
    )
    metric_on = joint.freeze_config(path)
    metric_off = control.freeze_config(path)
    assert metric_on["tracking"]["metric_relative_joint"] is True
    assert metric_off["tracking"]["metric_relative_joint"] is False
    metric_on["tracking"]["metric_relative_joint"] = False
    assert metric_off == metric_on


def test_scrub_mast3r_env_removes_all_runtime_hooks():
    env = control.scrub_mast3r_env({
        "MAST3R_FOO": "1",
        "MAST3R_METRIC_RELATIVE_JOINT_ADAPTER": "bad",
        "PATH": "/usr/bin",
    })
    assert not any(key.startswith("MAST3R_") for key in env)
    assert env["PATH"] == "/usr/bin"
    assert env["OMP_NUM_THREADS"] == "1"


def test_control_rejects_an_already_metric_enabled_source_config(tmp_path):
    path = tmp_path / "candidate_config.yaml"
    path.write_text(
        "single_thread: true\n"
        "dataset: {subsample: 1}\n"
        "local_opt: {pin: 1}\n"
        "tracking: {metric_relative_joint: true}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source config must be native baseline"):
        control.freeze_config(path)


def test_main_runs_native_off_command_with_strict_converter_and_manifest(tmp_path, monkeypatch):
    raw = tmp_path / "raw_frames.csv"
    raw.write_text("raw fixture", encoding="utf-8")
    dataset = write_dataset(tmp_path / "dataset", raw)
    checkpoint = tmp_path / "checkpoint.pth"
    checkpoint.write_text("checkpoint", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "single_thread: true\n"
        "dataset: {subsample: 1}\n"
        "local_opt: {pin: 1}\n"
        "tracking: {imu_rotation_prior: true}\n",
        encoding="utf-8",
    )
    output = tmp_path / "native_off"
    monkeypatch.setattr(control, "complete_source", lambda *_args: (
        {"source_session": "same_session"},
        3,
    ))
    monkeypatch.setenv("MAST3R_STALE_HOOK", "must_not_leak")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "main.py" in command:
            assert kwargs["cwd"] == control.TOOL
            assert not any(key.startswith("MAST3R_") for key in kwargs["env"])
            (output / "mast3r_logs").mkdir()
            (output / "mast3r_logs/dataset_full.txt").write_text(
                "0.000000 0 0 0 0 0 0 1\n"
                "0.033333 1 0 0 0 0 0 1\n"
                "0.066667 2 0 0 0 0 0 1\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0)
        assert str(ROOT / "scripts/convert_mast3r_slam_trajectory.py") in command
        assert "--require-complete" in command
        out = Path(command[command.index("--output") + 1])
        dense = Path(command[command.index("--dense-output") + 1])
        out.write_text("t_sec,x,y,z,qw,qx,qy,qz\n1,0,0,0,1,0,0,0\n", encoding="utf-8")
        dense.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        out.with_suffix(".manifest.json").write_text('{"schema":"umi_mast3r_trajectory_v1"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(control.subprocess, "run", fake_run)
    argv = [
        "run_native_metric_frontend_control.py",
        "--dataset",
        str(dataset),
        "--paired-left-dataset",
        str(dataset),
        "--eye",
        "left",
        "--config",
        str(config),
        "--checkpoint",
        str(checkpoint),
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert control.main() == 0

    assert len(calls) == 2
    assert not (output / "solves.jsonl").exists()
    effective = (output / "effective_config.yaml").read_text(encoding="utf-8")
    assert "metric_relative_joint: false" in effective
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "umi_mast3r_run_v1"
    assert manifest["status"] == "FRONTEND_COMPLETE_NOT_SCORED"
    assert manifest["frontend_mode"] == "native_default_off_control"
    assert manifest["metric_relative_joint_enabled"] is False
    assert manifest["precision_accepted"] is False
    assert manifest["production_promoted"] is False
    assert manifest["context_sha256"] == control.sha(output / "context.json")
    assert manifest["trajectory_sha256"] == control.sha(output / "trajectory_frames.csv")
    assert manifest["command"][manifest["command"].index("--dataset") + 1] == str(output / "dataset")
    assert manifest["converter_command"][-1] == "--require-complete"


def test_existing_output_is_never_reused(tmp_path, monkeypatch):
    output = tmp_path / "already_there"
    output.mkdir()
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text("single_thread: true\n", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.pth"
    checkpoint.write_text("checkpoint", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_native_metric_frontend_control.py",
            "--dataset",
            str(dataset),
            "--paired-left-dataset",
            str(dataset),
            "--eye",
            "left",
            "--config",
            str(config),
            "--checkpoint",
            str(checkpoint),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(FileExistsError):
        control.main()
