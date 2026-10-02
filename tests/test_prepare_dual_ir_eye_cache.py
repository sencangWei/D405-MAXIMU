import importlib.util
import hashlib
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


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def make_frontend_cache(tmp_path: Path, session: Path) -> Path:
    cache = tmp_path / "frontend_cache"
    (cache / "dataset").mkdir(parents=True)
    raw = cache / "trajectory_frames.csv"
    raw.write_text("t,x,y,z,qw,qx,qy,qz\n", encoding="utf-8")
    dense = cache / "trajectory_all_frames_interpolated.csv"
    dense.write_text("dense\n", encoding="utf-8")
    write_json(
        cache / "trajectory_frames.manifest.json",
        {"schema": "umi_mast3r_trajectory_v1", "source_poses": 22, "dense_frames": 25},
    )
    write_json(
        cache / "dataset/dataset_manifest.json",
        {
            "schema": "umi_mast3r_dataset_v1",
            "slam_supervision": False,
            "stream": "infrared_right",
            "source_session": str(session),
            "source_frames_csv_sha256": file_sha(session / "d405_frames.csv"),
            "frames": 25,
            "image_preprocessing": {"crop_bottom_px": 0, "mask_fixed_self_occlusion": False},
        },
    )
    write_json(
        cache / "run_manifest.json",
        {
            "schema": "umi_mast3r_run_v1",
            "slam_supervision": False,
            "config": str(prep.OFFLINE_CONFIG),
            "config_sha256": file_sha(prep.OFFLINE_CONFIG),
            "checkpoint": str(prep.CHECKPOINT),
            "checkpoint_sha256": file_sha(prep.CHECKPOINT),
            "trajectory": str(raw),
            "all_frames_interpolated_trajectory": str(dense),
            "stereo_descriptor_recovery": False,
            "spatial_pointmap_recovery": False,
        },
    )
    (cache / "000000.png").write_text("large image should not be copied", encoding="utf-8")
    return cache


def fake_run_factory(monkeypatch, optional_failures: set[str] | None = None):
    calls = []
    optional_failures = optional_failures or set()

    def fake_run(command, *, stage, output, timeout_s, env=None, allowed_returncodes=(0,)):
        calls.append((stage, command, env, allowed_returncodes))
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
            failed = report.name in optional_failures
            write_json(
                report,
                {
                    "schema": "umi_mast3r_stereo_scale_v2",
                    "result": "FAIL" if failed else "PASS",
                    "failures": ["right_stereo_scale_unobservable"] if failed else [],
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
            if failed:
                if 2 not in allowed_returncodes:
                    raise subprocess.CalledProcessError(2, command)
                return 2
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
        return 0

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
    derive_commands = [command for stage, command, _, _ in calls if stage.startswith("stereo_scale")]
    assert len(derive_commands) == 4
    assert all("--right-trajectory" in command for command in derive_commands)
    assert all(str(output.resolve() / "trajectory_frames.csv") in command for command in derive_commands)
    imu_command = [command for stage, command, _, _ in calls if stage == "imu_scale"][0]
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


def test_reject_window_keeps_valid_optional_rc2_as_diagnostic(tmp_path, monkeypatch):
    session, left, output, _ = make_inputs(tmp_path)
    calls = fake_run_factory(monkeypatch, {"stereo_scale_dense10hz_right_report.json"})

    prep.prepare(
        session,
        left,
        output,
        frontend_timeout_s=1,
        stage_timeout_s=1,
        optional_stereo_policy="reject_window",
    )

    manifest = json.loads((output / "dual_ir_eye_cache_manifest.json").read_text())
    rejected = manifest["rejected_optional_stereo_reports"]
    assert len(rejected) == 1
    assert rejected[0]["result"] == "FAIL"
    assert rejected[0]["returncode"] == 2
    assert "stereo_scale_dense10hz_right_report.json" in rejected[0]["report"]
    assert all("dense10hz" not in path for path in manifest["stereo_reports"])
    optional_call = [
        call for call in calls
        if call[0] == "stereo_scale_dense10hz_right_report"
    ][0]
    assert optional_call[3] == prep.OPTIONAL_DERIVE_STEREO_RETURNCODES


def test_strict_policy_rejects_optional_rc2(tmp_path, monkeypatch):
    session, left, output, _ = make_inputs(tmp_path)
    fake_run_factory(monkeypatch, {"stereo_scale_dense10hz_right_report.json"})

    with pytest.raises(subprocess.CalledProcessError):
        prep.prepare(session, left, output, frontend_timeout_s=1, stage_timeout_s=1)


def test_optional_rc2_with_wrong_identity_still_fails(tmp_path, monkeypatch):
    session, left, output, _ = make_inputs(tmp_path)

    def fake_run(command, *, stage, output, timeout_s, env=None, allowed_returncodes=(0,)):
        output.mkdir(parents=True, exist_ok=True)
        if stage == "frontend":
            (output / "dataset").mkdir()
            write_json(output / "dataset/dataset_manifest.json", {
                "schema": "umi_mast3r_dataset_v1", "stream": "infrared_right",
                "frames": 25, "source_session": str(session),
            })
            write_json(output / "trajectory_frames.manifest.json", {
                "schema": "umi_mast3r_trajectory_v1", "source_poses": 22, "dense_frames": 25,
            })
            (output / "trajectory_frames.csv").write_text("raw\n")
            (output / "trajectory_all_frames_interpolated.csv").write_text("dense\n")
            write_json(output / "run_manifest.json", {
                "config": str(prep.OFFLINE_CONFIG), "checkpoint": str(prep.CHECKPOINT),
            })
            return 0
        if stage.startswith("stereo_scale"):
            report = Path(command[-1])
            write_json(report, {
                "schema": "umi_mast3r_stereo_scale_v2",
                "result": "FAIL" if "dense10hz" in report.name else "PASS",
                "failures": ["right_stereo_scale_unobservable"] if "dense10hz" in report.name else [],
                "slam_supervision": False,
                "external_ground_truth_used": False,
                "session": str(session),
                "observation_frame": "infrared_left_camera_i" if "dense10hz" in report.name else "infrared_right_camera_i",
                "trajectory": str(output / "trajectory_frames.csv"),
            })
            return 2 if "dense10hz" in report.name else 0
        raise AssertionError(stage)

    monkeypatch.setattr(prep, "_run_command", fake_run)
    with pytest.raises(prep.PreparationError, match="right-IR framed"):
        prep.prepare(
            session,
            left,
            output,
            frontend_timeout_s=1,
            stage_timeout_s=1,
            optional_stereo_policy="reject_window",
        )


def test_coverage_allows_partial_dense_within_dataset(tmp_path):
    output = tmp_path / "out"
    (output / "dataset").mkdir(parents=True)
    write_json(output / "dataset/dataset_manifest.json", {
        "schema": "umi_mast3r_dataset_v1", "stream": "infrared_right",
        "frames": 1199, "source_session": "/session",
    })
    write_json(output / "trajectory_frames.manifest.json", {
        "schema": "umi_mast3r_trajectory_v1", "source_poses": 588, "dense_frames": 588,
    })

    report = prep._coverage_report(output)

    assert report["raw_tracked_poses"] == 588
    assert report["dense_frames"] == 588
    assert report["compatible_original_camera_frames"] == 1199
    assert report["partial_track_allowed"] is True


@pytest.mark.parametrize(
    "source_poses,dense_frames,dataset_frames,message",
    [
        (0, 10, 10, "zero poses"),
        (11, 10, 12, "exceeds dense"),
        (10, 13, 12, "exceed compatible"),
    ],
)
def test_coverage_rejects_invalid_ranges(tmp_path, source_poses, dense_frames, dataset_frames, message):
    output = tmp_path / "out"
    (output / "dataset").mkdir(parents=True)
    write_json(output / "dataset/dataset_manifest.json", {
        "schema": "umi_mast3r_dataset_v1", "stream": "infrared_right",
        "frames": dataset_frames, "source_session": "/session",
    })
    write_json(output / "trajectory_frames.manifest.json", {
        "schema": "umi_mast3r_trajectory_v1", "source_poses": source_poses, "dense_frames": dense_frames,
    })

    with pytest.raises(prep.PreparationError, match=message):
        prep._coverage_report(output)


def test_frontend_cache_reuse_copies_small_files_and_rebinds_paths(tmp_path, monkeypatch):
    session, left, output, orientation = make_inputs(tmp_path)
    cache = make_frontend_cache(tmp_path, session)
    calls = fake_run_factory(monkeypatch)

    prep.prepare(
        session,
        left,
        output,
        frontend_cache=cache,
        frontend_timeout_s=1,
        stage_timeout_s=1,
    )

    assert all(stage != "frontend" for stage, *_ in calls)
    assert (output / "trajectory_frames.csv").is_file()
    assert (output / "trajectory_frames.manifest.json").is_file()
    assert (output / "trajectory_all_frames_interpolated.csv").is_file()
    assert (output / "dataset/dataset_manifest.json").is_file()
    assert not (output / "000000.png").exists()
    run = json.loads((output / "run_manifest.json").read_text())
    assert run["trajectory"] == str((output / "trajectory_frames.csv").resolve())
    assert run["all_frames_interpolated_trajectory"] == str(
        (output / "trajectory_all_frames_interpolated.csv").resolve()
    )
    derive_commands = [command for stage, command, _, _ in calls if stage.startswith("stereo_scale")]
    assert derive_commands
    assert all(str(output.resolve() / "trajectory_frames.csv") in command for command in derive_commands)
    provenance = json.loads((output / "frontend_cache_reuse_provenance.json").read_text())
    assert provenance["frontend_reused_without_gpu"] is True
    assert provenance["source_cache"] == str(cache.resolve())
    manifest = json.loads((output / "dual_ir_eye_cache_manifest.json").read_text())
    assert manifest["frontend_cache_reuse"]["frontend_reused_without_gpu"] is True
    imu_command = [command for stage, command, _, _ in calls if stage == "imu_scale"][0]
    assert imu_command[imu_command.index("--orientation-trajectory") + 1] == str(orientation.resolve())


@pytest.mark.parametrize(
    "mutator,message",
    [
        (lambda run, dataset, session: run.update(config_sha256="bad"), "config hash"),
        (lambda run, dataset, session: run.update(stereo_descriptor_recovery=True), "recovery"),
        (lambda run, dataset, session: dataset.update(source_session="/other"), "session"),
        (lambda run, dataset, session: dataset.update(source_frames_csv_sha256="bad"), "frame hash"),
        (lambda run, dataset, session: dataset["image_preprocessing"].update(crop_bottom_px=2), "preprocessing"),
    ],
)
def test_frontend_cache_reuse_rejects_bad_provenance(tmp_path, mutator, message):
    session, left, output, _ = make_inputs(tmp_path)
    cache = make_frontend_cache(tmp_path, session)
    run_path = cache / "run_manifest.json"
    dataset_path = cache / "dataset/dataset_manifest.json"
    run = json.loads(run_path.read_text())
    dataset = json.loads(dataset_path.read_text())
    mutator(run, dataset, session)
    write_json(run_path, run)
    write_json(dataset_path, dataset)

    with pytest.raises(prep.PreparationError, match=message):
        prep.prepare(session, left, output, frontend_cache=cache, frontend_timeout_s=1, stage_timeout_s=1)


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
