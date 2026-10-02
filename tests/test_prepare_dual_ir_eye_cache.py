import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_dual_ir_eye_cache.py"
spec = importlib.util.spec_from_file_location("prepare_dual_ir_eye_cache", SCRIPT)
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def left_stereo_report(session: Path) -> dict:
    session.mkdir(parents=True, exist_ok=True)
    trajectory = session / "left_trajectory_frames.csv"
    trajectory.touch(exist_ok=True)
    return {
        "schema": "umi_mast3r_stereo_scale_v2",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "session": str(session),
        "trajectory": str(trajectory),
        "observation_frame": "infrared_left_camera_i",
        "db3": str(session / "d405_720p_rgb_stereo_ir.db3"),
        "factory_stereo_calibration": {"baseline_m": 0.018083254},
        "observations": [],
    }


def make_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    session = tmp_path / "session"
    left = tmp_path / "left" / "mast3r"
    output = tmp_path / "right"
    orientation = tmp_path / "vins.csv"
    session.mkdir()
    (session / "d405_frames.csv").write_text("dummy\n", encoding="utf-8")
    (orientation).write_text("timestamp,x,y,z,qw,qx,qy,qz\n", encoding="utf-8")
    for left_name, _ in prep.STEREO_REPORTS:
        write_json(left / left_name, left_stereo_report(session))
    write_json(
        left / "imu_scale_report.json",
        {
            "schema": "umi_mast3r_imu_scale_v1",
            "result": "PASS",
            "slam_supervision": False,
            "external_ground_truth_used": False,
            "session": str(session),
            "orientation_trajectory": str(orientation),
            "attitude": {"source": "onboard_orientation_trajectory"},
        },
    )
    return session, left, output, orientation


def fake_run_factory(monkeypatch):
    calls = []

    def fake_run(command, *, stage, output, timeout_s, env=None):
        calls.append((stage, command, env))
        output.mkdir(parents=True, exist_ok=True)
        if stage == "frontend":
            (output / "dataset").mkdir()
            write_json(
                output / "dataset" / "dataset_manifest.json",
                {
                    "schema": "umi_mast3r_dataset_v1",
                    "stream": "infrared_right",
                    "frames": 25,
                    "source_session": str(output.parent / "session"),
                },
            )
            write_json(
                output / "trajectory_frames.manifest.json",
                {"schema": "umi_mast3r_trajectory_v1", "source_poses": 22, "dense_frames": 25},
            )
            (output / "trajectory_frames.csv").write_text("t,x,y,z,qw,qx,qy,qz\n", encoding="utf-8")
            (output / "trajectory_all_frames_interpolated.csv").write_text("dense\n", encoding="utf-8")
            write_json(
                output / "run_manifest.json",
                {
                    "config": str(prep.OFFLINE_CONFIG),
                    "checkpoint": str(prep.CHECKPOINT),
                    "trajectory": str(output / "trajectory_frames.csv"),
                },
            )
        elif stage.startswith("stereo_scale"):
            report = Path(command[-1])
            write_json(
                report,
                {
                    "schema": "umi_mast3r_stereo_scale_v2",
                    "result": "PASS",
                    "slam_supervision": False,
                    "external_ground_truth_used": False,
                    "session": str(output.parent / "session"),
                    "observation_frame": "infrared_right_camera_i",
                    "trajectory": str(output / "trajectory_frames.csv"),
                    "factory_stereo_calibration": {
                        "right_rotation_from_left": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                        "right_translation_from_left_m": [0.018, 0, 0],
                    },
                },
            )
        elif stage == "imu_scale":
            write_json(
                Path(command[-1]),
                {
                    "schema": "umi_mast3r_imu_scale_v1",
                    "result": "PASS",
                    "slam_supervision": False,
                    "external_ground_truth_used": False,
                    "camera_stream": "infrared_right",
                },
            )
            Path(command[command.index("--output") + 1]).write_text("t,x,y,z,qw,qx,qy,qz\n", encoding="utf-8")

    monkeypatch.setattr(prep, "_run_command", fake_run)
    return calls


def test_prepare_builds_right_onboard_commands_and_manifest(tmp_path, monkeypatch):
    session, left, output, orientation = make_inputs(tmp_path)
    monkeypatch.setenv("MAST3R_REUSE_DATASET_DIR", "/tmp/must-not-leak")
    monkeypatch.setenv("MAST3R_STEREO_DESCRIPTOR_RECOVERY", "1")
    calls = fake_run_factory(monkeypatch)

    result = prep.prepare(session, left, output, frontend_timeout_s=1, stage_timeout_s=1)

    assert result == output.resolve()
    frontend = calls[0]
    assert frontend[0] == "frontend"
    assert frontend[1][-3:] == ["infrared_right", "0", "0"]
    assert frontend[2]["MAST3R_SLAM_CONFIG"] == str(prep.OFFLINE_CONFIG)
    assert frontend[2]["MAST3R_SLAM_CHECKPOINT"] == str(prep.CHECKPOINT)
    assert "MAST3R_REUSE_DATASET_DIR" not in frontend[2]
    assert frontend[2]["MAST3R_STEREO_DESCRIPTOR_RECOVERY"] == "0"
    assert frontend[2]["MAST3R_SPATIAL_POINTMAP_RECOVERY"] == "0"
    derive_commands = [command for stage, command, _ in calls if stage.startswith("stereo_scale")]
    assert len(derive_commands) == 4
    assert all("--right-trajectory" in command for command in derive_commands)
    assert all(str(output.resolve() / "trajectory_frames.csv") in command for command in derive_commands)
    imu_command = [command for stage, command, _ in calls if stage == "imu_scale"][0]
    assert "--stream" in imu_command and imu_command[imu_command.index("--stream") + 1] == "infrared_right"
    assert "--orientation-trajectory" in imu_command
    assert imu_command[imu_command.index("--orientation-trajectory") + 1] == str(orientation.resolve())
    assert imu_command[imu_command.index("--node-stride") + 1] == "10"
    assert imu_command[imu_command.index("--max-hop") + 1] == "1"

    manifest = json.loads((output / "dual_ir_eye_cache_manifest.json").read_text())
    coverage = json.loads((output / "frontend_coverage_report.json").read_text())
    assert manifest["external_ground_truth_used"] is False
    assert manifest["interpolated_dense_used_for_downstream"] is False
    assert coverage["compatible_original_camera_frames"] == 25
    assert coverage["raw_tracked_poses"] == 22
    assert coverage["partial_track_allowed"] is True


def test_prepare_rejects_tracker_gt_left_report(tmp_path):
    session, left, output, _ = make_inputs(tmp_path)
    bad = left_stereo_report(session)
    bad["source"] = "TrackerGT/debug.csv"
    write_json(left / prep.STEREO_REPORTS[0][0], bad)

    with pytest.raises(prep.PreparationError, match="TrackerGT"):
        prep.prepare(session, left, output)


def test_prepare_rejects_mismatched_left_report_session(tmp_path):
    session, left, output, _ = make_inputs(tmp_path)
    bad = left_stereo_report(tmp_path / "other_session")
    write_json(left / prep.STEREO_REPORTS[0][0], bad)

    with pytest.raises(prep.PreparationError, match="session does not match"):
        prep.prepare(session, left, output)


def test_prepare_rejects_missing_left_raw_trajectory_before_frontend(tmp_path, monkeypatch):
    session, left, output, _ = make_inputs(tmp_path)
    bad = left_stereo_report(session)
    missing = tmp_path / "missing_trajectory.csv"
    bad["trajectory"] = str(missing)
    write_json(left / prep.STEREO_REPORTS[0][0], bad)

    def forbidden_run(*args, **kwargs):
        raise AssertionError("frontend should not run after failed preflight")

    monkeypatch.setattr(prep, "_run_command", forbidden_run)
    with pytest.raises(prep.PreparationError, match="trajectory is missing"):
        prep.prepare(session, left, output)


def test_prepare_allows_mast3r_attitude_source_as_onboard(tmp_path, monkeypatch):
    session, left, output, _ = make_inputs(tmp_path)
    report = json.loads((left / "imu_scale_report.json").read_text())
    report["attitude"]["source"] = "mast3r"
    write_json(left / "imu_scale_report.json", report)
    fake_run_factory(monkeypatch)

    prep.prepare(session, left, output, frontend_timeout_s=1, stage_timeout_s=1)

    assert (output / "dual_ir_eye_cache_manifest.json").is_file()


def test_prepare_rejects_unknown_attitude_source_before_frontend(tmp_path, monkeypatch):
    session, left, output, _ = make_inputs(tmp_path)
    report = json.loads((left / "imu_scale_report.json").read_text())
    report["attitude"]["source"] = "TrackerGT"
    write_json(left / "imu_scale_report.json", report)

    def forbidden_run(*args, **kwargs):
        raise AssertionError("frontend should not run after failed preflight")

    monkeypatch.setattr(prep, "_run_command", forbidden_run)
    with pytest.raises(prep.PreparationError, match="onboard body orientation"):
        prep.prepare(session, left, output)


def test_prepare_refuses_existing_output(tmp_path):
    session, left, output, _ = make_inputs(tmp_path)
    output.mkdir()

    with pytest.raises(prep.PreparationError, match="overwrite"):
        prep.prepare(session, left, output)


def test_timeout_terminates_process_group_and_writes_marker(tmp_path, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    killed = []

    class FakeProc:
        pid = 12345
        returncode = None

        def __init__(self):
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired(["cmd"], timeout)
            self.returncode = -signal.SIGTERM
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(TimeoutError):
        prep._run_command(["cmd"], stage="frontend", output=output, timeout_s=1)

    assert killed == [(12345, signal.SIGTERM)]
    marker = json.loads((output / "frontend.failed.json").read_text())
    assert marker["reason"] == "timeout after 1s"


def test_run_command_accepts_allowed_rc3_without_failure_marker(tmp_path, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()

    class FakeProc:
        pid = 12345

        def wait(self, timeout=None):
            return 3

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProc())

    prep._run_command(["cmd"], stage="score", output=output, timeout_s=1, allowed_returncodes=(0, 3))

    assert not (output / "score.failed.json").exists()
    events = [
        json.loads(line)
        for line in (output / "command_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["returncode"] == 3


def test_run_command_rejects_disallowed_rc_and_writes_failure_marker(tmp_path, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()

    class FakeProc:
        pid = 12345

        def wait(self, timeout=None):
            return 4

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProc())

    with pytest.raises(subprocess.CalledProcessError):
        prep._run_command(["cmd"], stage="score", output=output, timeout_s=1, allowed_returncodes=(0, 3))

    marker = json.loads((output / "score.failed.json").read_text())
    assert marker["reason"] == "disallowed return code"
    assert marker["returncode"] == 4
