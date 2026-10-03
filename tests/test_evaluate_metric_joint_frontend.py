import csv
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_metric_joint_frontend as fresh  # noqa: E402


def _has_script(command: list[str], name: str) -> bool:
    return any(Path(part).name == name for part in command)


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "tx", "ty", "tz", "qx", "qy", "qz", "qw"])
        for index in range(rows):
            writer.writerow([f"{index / 30:.9f}", 0, 0, 0, 0, 0, 0, 1])
    return path


def _frontend(
    path: Path,
    eye: str,
    rows: int = 4,
    *,
    session: Path,
    status: str = fresh.FRONTEND_READY_STATUS,
    metric_joint: bool = True,
) -> Path:
    _write_csv(path / "dataset" / "frames.csv", rows)
    _write_json(path / "dataset" / "dataset_manifest.json", {
        "stream": f"infrared_{eye}",
        "source_session": str(session),
        "slam_supervision": False,
    })
    (path / "dataset" / "calibration.yaml").write_text("camera_model: pinhole\n", encoding="utf-8")
    trajectory = _write_csv(path / "trajectory_frames.csv", rows)
    config = path / "effective_config.yaml"
    config.write_text(f"tracking:\n  metric_relative_joint: {str(metric_joint).lower()}\n", encoding="utf-8")
    checkpoint = path / "checkpoint.pth"
    checkpoint.write_bytes(b"checkpoint")
    context = fresh.context_for_source(path / "dataset", path / "dataset", eye)
    _write_json(path / "context.json", context)
    manifest = {
        "schema": "umi_mast3r_run_v1",
        "status": status,
        "eye": eye,
        "source_session": str(session),
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "input_frame_count": rows,
        "trajectory_frame_count": rows,
        "native_pose_count": rows,
        "trajectory_frames_sha256": fresh.file_hash(trajectory),
        "config": str(config),
        "config_sha256": fresh.file_hash(config),
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": fresh.file_hash(checkpoint),
        "context": str(path / "context.json"),
        "context_sha256": fresh.file_hash(path / "context.json"),
    }
    _write_json(path / "run_manifest.json", manifest)
    return path


def _record_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    session = tmp_path / "session"
    capture = tmp_path / "capture"
    vins = tmp_path / "vins"
    ref = tmp_path / "reference_manifest.json"
    (session / "external_imu").mkdir(parents=True)
    vins.mkdir(parents=True)
    (session / "d405_frames.csv").write_text("frame\n0\n", encoding="utf-8")
    (session / "external_imu" / "imu.bin").write_bytes(b"imu")
    (capture / "tracker.csv").parent.mkdir(parents=True, exist_ok=True)
    (capture / "tracker.csv").write_text("t_sec,x,y,z,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n", encoding="utf-8")
    _write_json(capture / "capture_manifest.json", {"status": "PASS_CAPTURE_ONLY_NOT_CALIBRATED", "d405_session": str(session)})
    (vins / "vio_corrected_stream.csv").write_text("t_sec,tx,ty,tz,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n", encoding="utf-8")
    _write_json(vins / "run_acceptance.json", {"result": "PASS"})
    calibration = tmp_path / "reference_calibration.json"
    _write_json(calibration, {"schema": "calib"})
    _write_json(ref, {"schema": "ref", "artifacts": {"calibration": {"path": str(calibration)}}})
    manifest = {
        "schema": "fixture_full25",
        "records": [{
            "id": "rid",
            "session": str(session),
            "capture_dir": str(capture),
            "capture_manifest": str(capture / "capture_manifest.json"),
            "vins_dir": str(vins),
            "reference_manifest": str(ref),
            "left_dir": "/old/left",
            "right_dir": "/old/right",
        }],
    }
    return _write_json(tmp_path / "manifest.json", manifest), session, vins


def _stereo_report(path: Path, *, trajectory: str, eye: str = "left", right_derived: str | None = None) -> None:
    report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "session": "/unused",
        "trajectory": trajectory,
        "observation_frame": f"infrared_{eye}_camera_i",
        "scale_m_per_mast3r_unit": 1.0,
        "factory_stereo_calibration": {"baseline_m": 0.018083254},
        "observations": [],
    }
    if right_derived:
        report["derived_from_left_stereo_report"] = right_derived
    _write_json(path, report)


def _metric_outputs(command: list[str]) -> None:
    if "align_mast3r_scale_with_stereo.py" in " ".join(command):
        text = command[-1]
        # Last shell token is the report path; output path follows --output.
        parts = text.split()
        report = Path(parts[parts.index("--report") + 1].strip("'\""))
        output = Path(parts[parts.index("--output") + 1].strip("'\""))
        trajectory = parts[parts.index("--trajectory") + 1].strip("'\"")
        output.write_text("t_sec,tx,ty,tz,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n", encoding="utf-8")
        _stereo_report(report, trajectory=trajectory, eye="left")
        return
    if _has_script(command, "derive_right_ir_stereo_scale.py"):
        out = Path(command[command.index("--output") + 1])
        left = command[command.index("--left-stereo-report") + 1]
        traj = command[command.index("--right-trajectory") + 1]
        _stereo_report(out, trajectory=traj, eye="right", right_derived=left)
        return
    if _has_script(command, "align_mast3r_scale_with_imu.py"):
        out = Path(command[command.index("--output") + 1])
        rep = Path(command[command.index("--report") + 1])
        traj = command[command.index("--trajectory") + 1]
        out.write_text("t_sec,tx,ty,tz,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n", encoding="utf-8")
        _write_json(rep, {"schema": "umi_mast3r_imu_scale_v1", "result": "PASS", "slam_supervision": False, "external_ground_truth_used": False, "trajectory": traj, "scale": 1.0})
        return


def _score_outputs(command: list[str], *, result: str = "FAIL", maximum_m: float = 0.011) -> None:
    score_dir = Path(command[command.index("--output") + 1])
    estimate = Path(command[command.index("--estimate") + 1])
    reference = Path(command[command.index("--reference-manifest") + 1])
    _write_json(score_dir / "workflow_manifest.json", {
        "result": "SCORING_COMPLETED",
        "estimate": str(estimate),
        "estimate_sha256": fresh.file_hash(estimate),
        "estimate_unchanged": True,
        "reference_manifest": str(reference),
        "reference_manifest_sha256": fresh.file_hash(reference),
    })
    _write_json(score_dir / "precision.json", {"result": result, "samples": 1, "ate_translation_max_m": maximum_m})


def test_orchestrates_existing_clis_and_truthful_outputs(tmp_path: Path) -> None:
    manifest, _session, _vins = _record_fixture(tmp_path)
    left = _frontend(tmp_path / "left", "left", session=_session)
    right = _frontend(tmp_path / "right", "right", session=_session)
    calls: list[list[str]] = []

    def fake_runner(command, cwd=None, **_kwargs):
        calls.append(command)
        _metric_outputs(command)
        if _has_script(command, "fuse_mast3r_dual_ir_symmetric.py"):
            out = Path(command[command.index("--output-dir") + 1])
            _write_json(out / "candidate_manifest.json", {"schema": "umi_dual_ir_symmetric_experiment_v1", "external_ground_truth_used": False, "slam_supervision": False, "policy_arguments": {"optional_stereo_policy": "reject_window"}})
            _write_json(out / "graph_report.json", {"schema": "umi_dual_ir_symmetric_graph_diagnostic_v1", "external_ground_truth_used": False, "slam_supervision": False})
            _write_json(out / "local_motion_factors.json", [])
            _write_json(out / "shared_stereo_observations.json", [])
            (out / "body_trajectory_fused.csv").write_text("t_sec,tx,ty,tz,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n", encoding="utf-8")
        if _has_script(command, "score_steamvr_slam.py"):
            _score_outputs(command)
        if _has_script(command, "run_constant_ir_gauge_probe.py") or _has_script(command, "run_physical_stereo_lever_probe.py"):
            out = Path(command[command.index("--output") + 1])
            _write_json(out / "summary.json", {"status": "COMPLETED", "results": [{"id": "rid", "status": "COMPLETED"}]})
        return SimpleNamespace(returncode=0)

    report = fresh.run_pipeline(
        manifest_path=manifest,
        record_id="rid",
        left_frontend=left,
        right_frontend=right,
        output=tmp_path / "out",
        command_runner=fake_runner,
    )

    assert report["status"] == "COMPLETED"
    assert report["right_geometry_source"] == "left_derived_right_geometry_from_derive_right_ir_stereo_scale"
    assert report["frontend_mode"] == "metric_relative_joint"
    assert report["not_full10"] is True and report["not_full25"] is True
    assert len([cmd for cmd in calls if "align_mast3r_scale_with_stereo.py" in " ".join(cmd)]) == 4
    assert all("--prepared-dataset" not in " ".join(cmd) for cmd in calls)
    assert any(_has_script(cmd, "run_constant_ir_gauge_probe.py") for cmd in calls)
    assert any(_has_script(cmd, "run_physical_stereo_lever_probe.py") for cmd in calls)
    baseline_summary = json.loads((tmp_path / "out" / "fresh_symmetric_baseline" / "summary.json").read_text())
    assert baseline_summary["results"][0]["variants"]["both"]["score"]["ate_translation_max_m"] == 0.011
    inventory = json.loads((tmp_path / "out" / "fresh_full25_inventory.json").read_text())
    row = inventory["records"][0]
    assert row["fresh_metric_joint_frontend_eval"]["right_geometry_source"] == report["right_geometry_source"]


@pytest.mark.parametrize("failed_eye", ["left", "right"])
def test_optional_quality_failure_is_retained_without_deriving_from_failed_left(tmp_path: Path, failed_eye: str) -> None:
    manifest, session, _vins = _record_fixture(tmp_path)
    left = _frontend(tmp_path / "left", "left", session=session)
    right = _frontend(tmp_path / "right", "right", session=session)

    def fake_runner(command, cwd=None, **_kwargs):
        if _has_script(command, "derive_right_ir_stereo_scale.py"):
            source = Path(command[command.index("--left-stereo-report") + 1])
            assert json.loads(source.read_text())["result"] == "PASS"
        _metric_outputs(command)
        if _has_script(command, "fuse_mast3r_dual_ir_symmetric.py"):
            out = Path(command[command.index("--output-dir") + 1])
            _write_json(out / "candidate_manifest.json", {"schema": "umi_dual_ir_symmetric_experiment_v1"})
            _write_json(out / "graph_report.json", {"schema": "umi_dual_ir_symmetric_graph_diagnostic_v1"})
            _write_json(out / "local_motion_factors.json", [])
            _write_json(out / "shared_stereo_observations.json", [])
            (out / "body_trajectory_fused.csv").write_text("t_sec,tx,ty,tz,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n", encoding="utf-8")
        if _has_script(command, "score_steamvr_slam.py"):
            _score_outputs(command, result="PASS", maximum_m=0.001)
        if _has_script(command, "run_constant_ir_gauge_probe.py") or _has_script(command, "run_physical_stereo_lever_probe.py"):
            out = Path(command[command.index("--output") + 1])
            _write_json(out / "summary.json", {"status": "COMPLETED", "results": [{"id": "rid", "status": "COMPLETED"}]})
        rc = 0
        if "long_hops" in " ".join(command):
            if failed_eye == "left" and "align_mast3r_scale_with_stereo.py" in " ".join(command):
                rc = 3
                parts = command[-1].split()
                path = Path(parts[parts.index("--report") + 1].strip("'\""))
            elif failed_eye == "right" and _has_script(command, "derive_right_ir_stereo_scale.py"):
                rc = 2
                path = Path(command[command.index("--output") + 1])
            if rc:
                failed = json.loads(path.read_text())
                failed.update(result="FAIL", failures=["synthetic optional quality failure"])
                _write_json(path, failed)
        return SimpleNamespace(returncode=rc)

    report = fresh.run_pipeline(
        manifest_path=manifest,
        record_id="rid",
        left_frontend=left,
        right_frontend=right,
        output=tmp_path / "out",
        command_runner=fake_runner,
    )
    retained = [stage for stage in report["stages"] if stage.get("retained_for_reject_window_policy")]
    assert retained and retained[0]["primary"] is False
    assert retained[0]["left_returncode"] == (3 if failed_eye == "left" else 0)
    assert retained[0]["right_returncode"] == (None if failed_eye == "left" else 2)
    if failed_eye == "left":
        failed_right = json.loads(Path(retained[0]["right_report"]).read_text())
        assert failed_right["result"] == "FAIL"
        assert failed_right["derivation_status"] == "SKIPPED_LEFT_QUALITY_FAILED"
        assert failed_right["observations"] == []
        assert failed_right["scale_m_per_mast3r_unit"] is None
        import fuse_mast3r_stereo_imu as fusion
        primary = json.loads((tmp_path / "out/right_cache/stereo_scale_right_report.json").read_text())
        merged = fusion.merge_stereo_reports(primary, [failed_right], optional_policy="reject_window")
        assert merged["observations"] == primary["observations"]
        assert merged["optional_report_rejections"][0]["reason"] == "optional_report_failed"


@pytest.mark.parametrize("eye,returncode,expected_calls", [("left", 3, 1), ("right", 2, 2)])
def test_primary_stereo_quality_failure_stays_strict(tmp_path: Path, eye: str, returncode: int, expected_calls: int) -> None:
    manifest, session, _vins = _record_fixture(tmp_path)
    left = _frontend(tmp_path / "left", "left", session=session)
    right = _frontend(tmp_path / "right", "right", session=session)
    calls = []

    def fake_runner(command, cwd=None, **_kwargs):
        calls.append(command)
        _metric_outputs(command)
        is_left = "align_mast3r_scale_with_stereo.py" in " ".join(command)
        is_right = _has_script(command, "derive_right_ir_stereo_scale.py")
        rc = returncode if (eye == "left" and is_left) or (eye == "right" and is_right) else 0
        return SimpleNamespace(returncode=rc)

    with pytest.raises(subprocess.CalledProcessError) as error:
        fresh.run_pipeline(
            manifest_path=manifest, record_id="rid", left_frontend=left,
            right_frontend=right, output=tmp_path / "out", command_runner=fake_runner,
        )
    assert error.value.returncode == returncode
    assert len(calls) == expected_calls
    assert not any(_has_script(command, "score_steamvr_slam.py") for command in calls)


def test_rejects_incomplete_frontend_before_commands(tmp_path: Path) -> None:
    manifest, _session, _vins = _record_fixture(tmp_path)
    left = _frontend(tmp_path / "left", "left", session=_session, status="RUNNING")
    right = _frontend(tmp_path / "right", "right", session=_session)
    calls = []
    with pytest.raises(ValueError, match="FRONTEND_COMPLETE_NOT_SCORED"):
        fresh.run_pipeline(
            manifest_path=manifest,
            record_id="rid",
            left_frontend=left,
            right_frontend=right,
            output=tmp_path / "out",
            command_runner=lambda command, cwd=None: calls.append(command) or SimpleNamespace(returncode=0),
        )
    assert calls == []


def test_rejects_frontend_missing_context_sha_or_exact_time_binding(tmp_path: Path) -> None:
    _manifest, session, _vins = _record_fixture(tmp_path)
    frontend = _frontend(tmp_path / "left", "left", session=session)
    (frontend / "context.json").unlink()
    with pytest.raises(ValueError, match="missing required outputs"):
        fresh.validate_frontend(frontend, "left", expected_session=session)

    frontend = _frontend(tmp_path / "left2", "left", session=session)
    run = json.loads((frontend / "run_manifest.json").read_text())
    run.pop("trajectory_frames_sha256")
    _write_json(frontend / "run_manifest.json", run)
    with pytest.raises(ValueError, match="missing trajectory sha256"):
        fresh.validate_frontend(frontend, "left", expected_session=session)

    frontend = _frontend(tmp_path / "left2b", "left", session=session)
    run = json.loads((frontend / "run_manifest.json").read_text())
    run["trajectory_frames_sha256"] = "0" * 64
    _write_json(frontend / "run_manifest.json", run)
    with pytest.raises(ValueError, match="trajectory sha mismatch"):
        fresh.validate_frontend(frontend, "left", expected_session=session)

    frontend = _frontend(tmp_path / "left3", "left", session=session)
    trajectory = frontend / "trajectory_frames.csv"
    rows = trajectory.read_text(encoding="utf-8").splitlines()
    rows[-1] = rows[-1].replace("0.100000000", "0.123456789")
    trajectory.write_text("\n".join(rows) + "\n", encoding="utf-8")
    run = json.loads((frontend / "run_manifest.json").read_text())
    run["trajectory_frames_sha256"] = fresh.file_hash(trajectory)
    _write_json(frontend / "run_manifest.json", run)
    with pytest.raises(ValueError, match="timestamps do not match"):
        fresh.validate_frontend(frontend, "left", expected_session=session)


def test_rejects_stale_actual_context_source_hash(tmp_path: Path) -> None:
    _manifest, session, _vins = _record_fixture(tmp_path)
    frontend = _frontend(tmp_path / "left", "left", session=session)
    frames = frontend / "dataset" / "frames.csv"
    with frames.open("a", encoding="utf-8") as stream:
        stream.write("9.000000000,0,0,0,0,0,0,1\n")
    with pytest.raises(ValueError, match="context source/code binding mismatch"):
        fresh.validate_frontend(frontend, "left", expected_session=session)


def test_rejects_mixed_metric_joint_modes(tmp_path: Path) -> None:
    manifest, session, _vins = _record_fixture(tmp_path)
    left = _frontend(tmp_path / "left", "left", session=session, metric_joint=True)
    right = _frontend(tmp_path / "right", "right", session=session, metric_joint=False)
    with pytest.raises(ValueError, match="mode mismatch"):
        fresh.run_pipeline(
            manifest_path=manifest,
            record_id="rid",
            left_frontend=left,
            right_frontend=right,
            output=tmp_path / "out",
            command_runner=lambda command, cwd=None, **_kwargs: SimpleNamespace(returncode=0),
        )


def test_rejects_changed_fresh_source_before_scoring(tmp_path: Path) -> None:
    manifest, _session, _vins = _record_fixture(tmp_path)
    left = _frontend(tmp_path / "left", "left", session=_session)
    right = _frontend(tmp_path / "right", "right", session=_session)

    def mutating_runner(command, cwd=None, **_kwargs):
        _metric_outputs(command)
        if _has_script(command, "fuse_mast3r_dual_ir_symmetric.py"):
            out = Path(command[command.index("--output-dir") + 1])
            _write_json(out / "candidate_manifest.json", {"schema": "umi_dual_ir_symmetric_experiment_v1"})
            _write_json(out / "graph_report.json", {"schema": "umi_dual_ir_symmetric_graph_diagnostic_v1"})
            _write_json(out / "local_motion_factors.json", [])
            _write_json(out / "shared_stereo_observations.json", [])
            (out / "body_trajectory_fused.csv").write_text("t_sec,tx,ty,tz,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n", encoding="utf-8")
            (right / "run_manifest.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    with pytest.raises(RuntimeError, match="consumed input changed"):
        fresh.run_pipeline(
            manifest_path=manifest,
            record_id="rid",
            left_frontend=left,
            right_frontend=right,
            output=tmp_path / "out",
            command_runner=mutating_runner,
        )


def test_rejects_right_report_without_left_derived_provenance(tmp_path: Path) -> None:
    manifest, session, _vins = _record_fixture(tmp_path)
    left = _frontend(tmp_path / "left", "left", session=session)
    right = _frontend(tmp_path / "right", "right", session=session)

    def bad_runner(command, cwd=None, **_kwargs):
        _metric_outputs(command)
        if _has_script(command, "derive_right_ir_stereo_scale.py"):
            out = Path(command[command.index("--output") + 1])
            traj = command[command.index("--right-trajectory") + 1]
            _stereo_report(out, trajectory=traj, eye="right", right_derived=None)
        return SimpleNamespace(returncode=0)

    with pytest.raises(ValueError, match="not explicitly left-derived"):
        fresh.run_pipeline(
            manifest_path=manifest,
            record_id="rid",
            left_frontend=left,
            right_frontend=right,
            output=tmp_path / "out",
            command_runner=bad_runner,
        )


def test_rejects_scorer_estimate_sha_mismatch(tmp_path: Path) -> None:
    manifest, session, _vins = _record_fixture(tmp_path)
    left = _frontend(tmp_path / "left", "left", session=session)
    right = _frontend(tmp_path / "right", "right", session=session)

    def bad_score_runner(command, cwd=None, **_kwargs):
        _metric_outputs(command)
        if _has_script(command, "fuse_mast3r_dual_ir_symmetric.py"):
            out = Path(command[command.index("--output-dir") + 1])
            _write_json(out / "candidate_manifest.json", {"schema": "umi_dual_ir_symmetric_experiment_v1"})
            _write_json(out / "graph_report.json", {"schema": "umi_dual_ir_symmetric_graph_diagnostic_v1"})
            _write_json(out / "local_motion_factors.json", [])
            _write_json(out / "shared_stereo_observations.json", [])
            (out / "body_trajectory_fused.csv").write_text("t_sec,tx,ty,tz,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n", encoding="utf-8")
        if _has_script(command, "score_steamvr_slam.py"):
            score_dir = Path(command[command.index("--output") + 1])
            estimate = Path(command[command.index("--estimate") + 1])
            reference = Path(command[command.index("--reference-manifest") + 1])
            _write_json(score_dir / "workflow_manifest.json", {
                "result": "SCORING_COMPLETED",
                "estimate": str(estimate),
                "estimate_sha256": "0" * 64,
                "estimate_unchanged": True,
                "reference_manifest": str(reference),
                "reference_manifest_sha256": fresh.file_hash(reference),
            })
            _write_json(score_dir / "precision.json", {"result": "PASS"})
        return SimpleNamespace(returncode=0)

    with pytest.raises(ValueError, match="scorer estimate sha mismatch"):
        fresh.run_pipeline(
            manifest_path=manifest,
            record_id="rid",
            left_frontend=left,
            right_frontend=right,
            output=tmp_path / "out",
            command_runner=bad_score_runner,
        )


def test_main_marks_existing_summary_failed_on_exception(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"

    def boom(**kwargs):
        out.mkdir()
        _write_json(out / "summary.json", {"schema": fresh.SCHEMA, "status": "RUNNING"})
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(fresh, "run_pipeline", boom)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        fresh.main([
            "--manifest", str(tmp_path / "manifest.json"),
            "--record-id", "rid",
            "--left-frontend-dir", str(tmp_path / "left"),
            "--right-frontend-dir", str(tmp_path / "right"),
            "--output", str(out),
        ])
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "EVALUATION_FAILED"
    assert summary["error_type"] == "RuntimeError"
