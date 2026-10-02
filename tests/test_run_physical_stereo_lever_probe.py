import importlib.util
import csv
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "physical_stereo_lever_probe",
    ROOT / "scripts/run_physical_stereo_lever_probe.py",
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


BASELINE_FACTOR = {
    "first_index": 0,
    "second_index": 1,
    "eye": "left",
    "metric_displacement_world_m": [1.0, 0.0, 0.0],
    "confidence": 0.75,
    "own_confidence": 0.75,
    "own_observation_confidence": 0.9,
    "own_stereo_residual_m": 0.001,
}
CONSTANT_FACTOR = {**BASELINE_FACTOR, "metric_displacement_world_m": [0.5, 0.0, 0.0]}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return probe.file_hash(path)


def observation(times, *, confidence=0.8, raw_indices=False):
    return {
        "accepted": True,
        "first_index": 7 if raw_indices else 0,
        "second_index": 8 if raw_indices else 1,
        "first_t_sec": float(times[0] + (0.001 if raw_indices else 0.0)),
        "second_t_sec": float(times[1] + (0.001 if raw_indices else 0.0)),
        "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
        "metric_displacement_frame": "infrared_left_camera_i",
        "scale": 1.0,
        "pnp_inlier_ratio": confidence,
        "rotation_error_deg": 0.0,
    }


def shared_row(times, *, confidence=0.8):
    row = observation(times, confidence=confidence)
    row["metric_displacement_frame"] = "body_i"
    return row


def write_eye_trajectory(directory: Path, eye: str, times: np.ndarray) -> Path:
    path = directory / probe.eye_trajectory_name(eye)
    path.parent.mkdir(parents=True, exist_ok=True)
    trajectory_times = [
        times[0] - 0.20,
        times[0] - 0.16,
        times[0] - 0.12,
        times[0] - 0.08,
        times[0] - 0.04,
        times[0] - 0.02,
        times[0] - 0.01,
        times[0] + 0.001,
        times[1] + 0.001,
        times[2] + 0.001,
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        writer.writeheader()
        for timestamp in trajectory_times:
            writer.writerow({"t_sec": f"{timestamp:.9f}", "x": 0, "y": 0, "z": 0, "qw": 1, "qx": 0, "qy": 0, "qz": 0})
    return path


def write_stereo_reports(directory: Path, eye: str, session: Path, times: np.ndarray) -> tuple[list[Path], Path]:
    directory.mkdir(parents=True, exist_ok=True)
    trajectory = write_eye_trajectory(directory, eye, times)
    paths = []
    for index, name in enumerate(probe.eye_report_names(eye)):
        obs = observation(times, raw_indices=True)
        obs["metric_displacement_frame"] = f"infrared_{eye}_camera_i"
        report = {
            "schema": "umi_mast3r_stereo_scale_v2",
            "result": "PASS",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "session": str(session.resolve()),
            "trajectory": str(trajectory.resolve()),
            "observation_frame": f"infrared_{eye}_camera_i",
            "scale_m_per_mast3r_unit": 1.0,
            "factory_stereo_calibration": {"baseline_m": 0.018083254},
            "observations": [obs] if index == 0 else [],
        }
        path = directory / name
        write_json(path, report)
        paths.append(path)
    return paths, trajectory


def make_fixture(monkeypatch, tmp_path: Path, *, second_status: str | None = None):
    config = tmp_path / "config" / "vins.yaml"
    imu_config = tmp_path / "config" / "imu.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("vins\n", encoding="utf-8")
    imu_config.write_text("imu\n", encoding="utf-8")
    monkeypatch.setattr(probe.base.symmetric, "VINS_CONFIG", config)
    monkeypatch.setattr(probe.base.symmetric, "IMU_CONFIG", imu_config)
    records = []
    baseline = tmp_path / "baseline"
    times = np.asarray([1.0, 1.02, 1.04])
    for rid, status in (("case_01", "COMPLETED"), ("case_02", second_status)):
        if status is None:
            continue
        session = tmp_path / rid / "session"
        vins = tmp_path / rid / "vins"
        actual_left = tmp_path / rid / "actual_left"
        actual_right = tmp_path / rid / "actual_right"
        stale_left = tmp_path / rid / "stale_left"
        stale_right = tmp_path / rid / "stale_right"
        for directory in (session / "external_imu", vins):
            directory.mkdir(parents=True, exist_ok=True)
        (session / "d405_frames.csv").write_text("frames\n", encoding="utf-8")
        (session / "external_imu/imu.bin").write_bytes(b"imu")
        (vins / "vio_corrected_stream.csv").write_text("vins\n", encoding="utf-8")
        (vins / "run_acceptance.json").write_text("{}\n", encoding="utf-8")
        left_reports, left_trajectory = write_stereo_reports(actual_left, "left", session, times)
        right_reports, right_trajectory = write_stereo_reports(actual_right, "right", session, times)
        records.append({
            "id": rid,
            "session": str(session),
            "capture_dir": str(tmp_path / rid / "capture"),
            "vins_dir": str(vins),
            "left_dir": str(stale_left),
            "right_dir": str(stale_right),
            "reference_manifest": str(tmp_path / rid / "reference.json"),
        })
        write_json(
            baseline / "summary.json",
            {
                "schema": "umi_dual_ir_development_regression_v1",
                "status": "COMPLETED_WITH_FAILURES" if second_status else "COMPLETED",
                "results": [
                    {
                        "id": item["id"],
                        "status": (
                            "PREPARATION_OR_INPUT_FAILED"
                            if item["id"] == "case_02"
                            else "COMPLETED"
                        ),
                        "variants": {"both": {}},
                    }
                    for item in records
                ],
            },
        )
        if status != "COMPLETED":
            continue
        artifact = baseline / rid / "both"
        artifact.mkdir(parents=True)
        inputs = [
            vins / "vio_corrected_stream.csv",
            vins / "run_acceptance.json",
            session / "d405_frames.csv",
            session / "external_imu/imu.bin",
            config,
            imu_config,
            left_trajectory,
            right_trajectory,
            *left_reports,
            *right_reports,
        ]
        input_sha = {str(path.resolve()): sha(path) for path in inputs}
        eye_metadata = {
            eye: {
                "effective_body_T_camera": np.eye(4).tolist(),
                "factory_stereo_calibration": {"baseline_m": 0.018083254},
            }
            for eye in ("left", "right")
        }
        write_json(
            artifact / "candidate_manifest.json",
            {
                "schema": "umi_dual_ir_symmetric_experiment_v1",
                "external_ground_truth_used": False,
                "slam_supervision": False,
                "session": str(session.resolve()),
                "input_sha256": input_sha,
                "eye_reports": eye_metadata,
                "shared_gauge": "VINS_body_world_first_node_only",
                "primary_eye": None,
                "shared_scale_state": False,
                "body_t_camera_in_solver": np.eye(4).tolist(),
                "policy_arguments": dict(probe.base.BASELINE_POLICY_ARGUMENTS),
            },
        )
        write_json(
            artifact / "graph_report.json",
            {
                "schema": "umi_dual_ir_symmetric_graph_diagnostic_v1",
                "external_ground_truth_used": False,
                "slam_supervision": False,
                "output_frame": "body_imu_origin",
                "output_samples": 3,
                "td_s": -0.009109323,
                "policy_arguments": dict(probe.base.BASELINE_POLICY_ARGUMENTS),
                "joint_position_solver": {},
            },
        )
        write_json(artifact / "local_motion_factors.json", [BASELINE_FACTOR])
        row = shared_row(times)
        row["first_t_sec"] = float(times[0] + 0.001)
        row["second_t_sec"] = float(times[1] + 0.001)
        write_json(artifact / "shared_stereo_observations.json", [row])
        (artifact / "body_trajectory_fused.csv").write_text("baseline\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"records": records})
    return manifest, baseline, records


def write_constant_artifact(root: Path, record: dict, *, missing: bool = False) -> None:
    if missing:
        return
    artifact = root / record["id"] / "selected"
    artifact.mkdir(parents=True)
    source = artifact / "source.txt"
    source.write_text("constant source\n", encoding="utf-8")
    estimate = artifact / "body_trajectory_fused.csv"
    estimate.write_text("constant estimate\n", encoding="utf-8")
    write_json(artifact / "local_motion_factors.json", [CONSTANT_FACTOR])
    motion_hash = sha(artifact / "local_motion_factors.json")
    write_json(
        artifact / "candidate_manifest.json",
        {
            "schema": "umi_constant_ir_gauge_candidate_v1",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "session": str(Path(record["session"]).resolve()),
            "input_sha256": {str(source.resolve()): sha(source)},
            "output_estimate_sha256": sha(estimate),
            "output_motion_factors_sha256": motion_hash,
        },
    )
    write_json(
        artifact / "graph_report.json",
        {
            "schema": "umi_constant_ir_gauge_graph_diagnostic_v1",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "output_frame": "body_imu_origin",
        },
    )


def patch_runtime(monkeypatch, tmp_path, *, changed_after_score=False, mutate_transform=None, state_times=None):
    code = tmp_path / "code.py"
    code.write_text("code\n", encoding="utf-8")
    monkeypatch.setattr(probe, "frozen_code_paths", lambda: [code])
    times = np.asarray([1.0, 1.02, 1.04] if state_times is None else state_times)
    positions = np.asarray([[0.0, 0, 0], [0.1, 0, 0], [0.2, 0, 0]])
    rotations = Rotation.identity(3)
    rows = [
        {"t_sec": f"{t:.9f}", "x": "0", "y": "0", "z": "0", "qw": "1", "qx": "0", "qy": "0", "qz": "0"}
        for t in times
    ]
    state = SimpleNamespace(
        config={"td_s": -0.009109323},
        imu_times=times,
        gyro=np.zeros((3, 3)),
        accel=np.zeros((3, 3)),
        imu_info={},
        times=times,
        positions=positions,
        rotations=rotations,
        rows=rows,
        mono=times - 1,
        binding={},
        source_quality={},
    )
    monkeypatch.setattr(probe.base, "load_bound_reference", lambda record: state)
    monkeypatch.setattr(probe.base, "validate_baseline_trajectory_identity", lambda *args: None)

    def fake_transform(reference_times, reference_rotations, eye_candidates, original_rows):
        assert len(eye_candidates) == 2
        assert {candidate["eye"] for candidate in eye_candidates} == {"left", "right"}
        assert all(candidate["observation_confidence"] == pytest.approx(0.8) for candidate in eye_candidates)
        assert all(candidate["first_index"] == 7 for candidate in eye_candidates)
        assert all(candidate["reference_first_index"] == 0 for candidate in eye_candidates)
        assert original_rows[0]["first_t_sec"] == pytest.approx(reference_times[0] + 0.001)
        assert original_rows[0]["second_t_sec"] == pytest.approx(reference_times[1] + 0.001)
        row = {**original_rows[0], "metric_displacement_camera_i_m": [0.02, 0.0, 0.0]}
        if mutate_transform is not None:
            mutate_transform(row)
        return [row], {
            "schema": "physical_stereo_lever_diagnostic_v1",
            "accepted": False,
            "input_candidate_count": len(eye_candidates),
            "output_row_count": 1,
        }

    monkeypatch.setattr(probe, "transform_shared_stereo_body_lever", fake_transform)
    captured = {"refine_calls": []}

    def fake_refine(*_args, **kwargs):
        captured["refine_calls"].append(kwargs)
        return positions + [0.01, 0, 0], {"secondary_visual_motion": {"edges": 1}}

    monkeypatch.setattr(probe.fusion, "refine_positions_visual_inertial", fake_refine)
    monkeypatch.setattr(probe.fusion, "write_trajectory", lambda path, rows, refined, rotations: path.write_text("estimate\n", encoding="utf-8"))
    calls = {"code_changed": 0}

    def fake_code_changed(_hashes):
        calls["code_changed"] += 1
        return changed_after_score and calls["code_changed"] >= 3

    monkeypatch.setattr(probe, "code_changed", fake_code_changed)
    monkeypatch.setattr(probe.base, "code_changed", fake_code_changed)
    monkeypatch.setattr(probe.corpus, "score_frozen", lambda *args: {"result": "PASS", "samples": 3, "ate_translation_max_m": 0.001})
    return captured


def test_physical_variant_uses_baseline_bound_raw_reports_not_manifest_and_scores(tmp_path, monkeypatch):
    manifest, baseline, records = make_fixture(monkeypatch, tmp_path)
    captured = patch_runtime(monkeypatch, tmp_path)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 0

    variant = tmp_path / "out" / "case_01" / "physical_stereo_only"
    candidate = json.loads((variant / "candidate_manifest.json").read_text())
    assert candidate["external_ground_truth_used"] is False
    assert candidate["policy_arguments"]["physical_stereo_transform"] == "body_lever_from_raw_merged_stereo_v1"
    assert candidate["stereo_transform"]["row_count"] == 1
    actual_left_primary = Path(records[0]["session"]).parent / "actual_left" / "stereo_scale_bidirectional_report.json"
    assert str(actual_left_primary.resolve()) in candidate["input_sha256"]
    assert not Path(records[0]["left_dir"]).exists()
    assert captured["refine_calls"][0]["secondary_visual_factors"] == [BASELINE_FACTOR]
    assert captured["refine_calls"][0]["stereo_factor_confidences"].tolist() == [0.8]
    transformed = json.loads((variant / "shared_stereo_observations.json").read_text())[0]
    assert transformed["first_t_sec"] == pytest.approx(1.001)
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["status"] == "COMPLETED"


def test_real_transform_wrapper_maps_raw_stereo_times_to_reference_contract():
    if not probe.TRANSFORM_MODULE.exists():
        pytest.skip("physical_stereo_lever module not published in this checkout yet")
    times = np.asarray([1.0, 1.02, 1.04])
    state = SimpleNamespace(times=times, rotations=Rotation.identity(3))
    raw = observation(times, raw_indices=True)
    trajectory_times = np.asarray([0.8, 0.84, 0.88, 0.92, 0.96, 0.98, 0.99, 1.001, 1.021])
    candidate, skip_reason = probe.reference_bound_eye_candidate(
        times,
        trajectory_times,
        "left",
        {**raw, "metric_displacement_frame": "infrared_left_camera_i"},
        0.8,
        np.eye(4).tolist(),
    )
    assert skip_reason is None
    original = [shared_row(times)]
    original[0]["first_t_sec"] = raw["first_t_sec"]
    original[0]["second_t_sec"] = raw["second_t_sec"]

    transformed, report = probe.transform_shared_rows(state, [candidate], original)

    assert report["row_count"] == 1
    assert transformed[0]["first_t_sec"] == pytest.approx(raw["first_t_sec"])
    assert transformed[0]["second_t_sec"] == pytest.approx(raw["second_t_sec"])


def test_shared_row_identity_guard_rejects_confidence_change_before_solver(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(monkeypatch, tmp_path)
    captured = patch_runtime(
        monkeypatch,
        tmp_path,
        mutate_transform=lambda row: row.__setitem__("pnp_inlier_ratio", 0.1),
    )

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["physical_stereo_only"]
    assert variant["error_code"] == "VARIANT_FAILED"
    assert "pnp_inlier_ratio" in variant["error"]
    assert captured["refine_calls"] == []


def test_same_eye_duplicate_raw_candidates_fail_without_exact_dedup_metadata(tmp_path, monkeypatch):
    manifest, baseline, records = make_fixture(monkeypatch, tmp_path)
    patch_runtime(monkeypatch, tmp_path)
    duplicate = Path(records[0]["session"]).parent / "actual_left" / "stereo_scale_dense10hz_report.json"
    report = json.loads(duplicate.read_text())
    report["observations"] = [observation(np.asarray([1.0, 1.02, 1.04]), raw_indices=True)]
    report["observations"][0]["metric_displacement_frame"] = "infrared_left_camera_i"
    write_json(duplicate, report)
    candidate_path = baseline / "case_01" / "both" / "candidate_manifest.json"
    candidate = json.loads(candidate_path.read_text())
    candidate["input_sha256"][str(duplicate.resolve())] = sha(duplicate)
    write_json(candidate_path, candidate)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["physical_stereo_only"]
    assert variant["error_code"] == "VARIANT_FAILED"
    assert "duplicate same-eye raw stereo candidate" in variant["error"]


def test_unused_reference_gap_raw_candidate_is_skipped_not_fatal(tmp_path, monkeypatch):
    manifest, baseline, records = make_fixture(monkeypatch, tmp_path)
    state_times = np.asarray([1.0, 1.02, 1.10])
    captured = patch_runtime(monkeypatch, tmp_path, state_times=state_times)
    root = Path(records[0]["session"]).parent
    left_traj = root / "actual_left" / "trajectory_imu_metric.csv"
    rows = list(csv.DictReader(left_traj.open(newline="", encoding="utf-8")))
    template = dict(rows[-1])
    rows[9]["t_sec"] = "1.041000000"
    for timestamp in ("1.061000000", "1.081000000", "1.101000000"):
        extra = dict(template)
        extra["t_sec"] = timestamp
        rows.append(extra)
    with left_traj.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        writer.writeheader()
        writer.writerows(rows)
    gap_report = root / "actual_left" / "stereo_scale_dense10hz_report.json"
    report = json.loads(gap_report.read_text())
    gap_obs = observation(state_times, raw_indices=True)
    gap_obs["first_index"] = 8
    gap_obs["second_index"] = 12
    gap_obs["first_t_sec"] = 1.021
    gap_obs["second_t_sec"] = 1.101
    gap_obs["metric_displacement_frame"] = "infrared_left_camera_i"
    report["observations"] = [gap_obs]
    write_json(gap_report, report)
    candidate_path = baseline / "case_01" / "both" / "candidate_manifest.json"
    candidate = json.loads(candidate_path.read_text())
    candidate["input_sha256"][str(left_traj.resolve())] = sha(left_traj)
    candidate["input_sha256"][str(gap_report.resolve())] = sha(gap_report)
    write_json(candidate_path, candidate)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 0

    variant = tmp_path / "out" / "case_01" / "physical_stereo_only"
    candidate = json.loads((variant / "candidate_manifest.json").read_text())
    left_report = candidate["eye_candidate_reports"]["left"]
    assert left_report["skipped_candidate_counts"]["reference_interval_gap"] == 1
    assert left_report["candidate_count"] == 1
    assert len(captured["refine_calls"]) == 1


def test_unused_raw_trajectory_gap_candidate_is_skipped_not_fatal(tmp_path, monkeypatch):
    manifest, baseline, records = make_fixture(monkeypatch, tmp_path)
    captured = patch_runtime(monkeypatch, tmp_path)
    root = Path(records[0]["session"]).parent
    left_traj = root / "actual_left" / "trajectory_imu_metric.csv"
    rows = list(csv.DictReader(left_traj.open(newline="", encoding="utf-8")))
    rows[9]["t_sec"] = "1.121000000"
    with left_traj.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        writer.writeheader()
        writer.writerows(rows)
    gap_report = root / "actual_left" / "stereo_scale_dense10hz_report.json"
    report = json.loads(gap_report.read_text())
    gap_obs = observation(np.asarray([1.0, 1.02, 1.04]), raw_indices=True)
    gap_obs["first_index"] = 8
    gap_obs["second_index"] = 9
    gap_obs["first_t_sec"] = 1.021
    gap_obs["second_t_sec"] = 1.121
    gap_obs["metric_displacement_frame"] = "infrared_left_camera_i"
    report["observations"] = [gap_obs]
    write_json(gap_report, report)
    candidate_path = baseline / "case_01" / "both" / "candidate_manifest.json"
    candidate = json.loads(candidate_path.read_text())
    candidate["input_sha256"][str(left_traj.resolve())] = sha(left_traj)
    candidate["input_sha256"][str(gap_report.resolve())] = sha(gap_report)
    write_json(candidate_path, candidate)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 0

    variant = tmp_path / "out" / "case_01" / "physical_stereo_only"
    candidate = json.loads((variant / "candidate_manifest.json").read_text())
    left_report = candidate["eye_candidate_reports"]["left"]
    assert left_report["skipped_candidate_counts"]["raw_trajectory_interval_gap"] == 1
    assert left_report["candidate_count"] == 1
    assert len(captured["refine_calls"]) == 1


def test_reversed_accepted_raw_candidate_is_hard_failure(tmp_path, monkeypatch):
    manifest, baseline, records = make_fixture(monkeypatch, tmp_path)
    patch_runtime(monkeypatch, tmp_path)
    reversed_report = Path(records[0]["session"]).parent / "actual_left" / "stereo_scale_dense10hz_report.json"
    report = json.loads(reversed_report.read_text())
    reversed_obs = observation(np.asarray([1.0, 1.02, 1.04]), raw_indices=True)
    reversed_obs["first_index"] = 8
    reversed_obs["second_index"] = 7
    reversed_obs["first_t_sec"] = 1.021
    reversed_obs["second_t_sec"] = 1.001
    reversed_obs["metric_displacement_frame"] = "infrared_left_camera_i"
    report["observations"] = [reversed_obs]
    write_json(reversed_report, report)
    candidate_path = baseline / "case_01" / "both" / "candidate_manifest.json"
    candidate = json.loads(candidate_path.read_text())
    candidate["input_sha256"][str(reversed_report.resolve())] = sha(reversed_report)
    write_json(candidate_path, candidate)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["physical_stereo_only"]
    assert variant["error_code"] == "VARIANT_FAILED"
    assert "accepted raw stereo observation endpoints are not ordered" in variant["error"]


def test_constant_variant_uses_constant_factors_and_missing_constant_fails_only_combined(tmp_path, monkeypatch):
    manifest, baseline, records = make_fixture(monkeypatch, tmp_path)
    constant_root = tmp_path / "constant"
    write_constant_artifact(constant_root, records[0])
    captured = patch_runtime(monkeypatch, tmp_path)

    assert probe.main([
        "--manifest", str(manifest),
        "--baseline", str(baseline),
        "--constant-gauge", str(constant_root),
        "--output", str(tmp_path / "out"),
    ]) == 0

    assert len(captured["refine_calls"]) == 2
    assert captured["refine_calls"][0]["secondary_visual_factors"] == [BASELINE_FACTOR]
    assert captured["refine_calls"][1]["secondary_visual_factors"] == [CONSTANT_FACTOR]
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["variants"] == ["physical_stereo_only", "physical_stereo_constant_gauge"]


def test_constant_variant_requires_hash_bound_motion_factor_file(tmp_path, monkeypatch):
    manifest, baseline, records = make_fixture(monkeypatch, tmp_path)
    constant_root = tmp_path / "constant"
    write_constant_artifact(constant_root, records[0])
    candidate_path = constant_root / records[0]["id"] / "selected" / "candidate_manifest.json"
    candidate = json.loads(candidate_path.read_text())
    candidate.pop("output_motion_factors_sha256")
    write_json(candidate_path, candidate)
    patch_runtime(monkeypatch, tmp_path)

    assert probe.main([
        "--manifest", str(manifest),
        "--baseline", str(baseline),
        "--constant-gauge", str(constant_root),
        "--output", str(tmp_path / "out"),
    ]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    combined = summary["results"][0]["variants"]["physical_stereo_constant_gauge"]
    assert combined["error_code"] == "VARIANT_FAILED"
    assert "constant gauge motion factor hash missing" in combined["error"]


def test_missing_constant_record_does_not_fallback_to_baseline(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(monkeypatch, tmp_path)
    patch_runtime(monkeypatch, tmp_path)

    assert probe.main([
        "--manifest", str(manifest),
        "--baseline", str(baseline),
        "--constant-gauge", str(tmp_path / "missing_constant"),
        "--output", str(tmp_path / "out"),
    ]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variants = summary["results"][0]["variants"]
    assert "score" in variants["physical_stereo_only"]
    assert variants["physical_stereo_constant_gauge"]["error_code"] == "VARIANT_FAILED"
    assert "missing constant gauge artifact" in variants["physical_stereo_constant_gauge"]["error"]


def test_code_change_after_scoring_stops_without_mixing_following_results(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(monkeypatch, tmp_path, second_status="PREPARATION_OR_INPUT_FAILED")
    patch_runtime(monkeypatch, tmp_path, changed_after_score=True)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 2

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["status"] == "STOP_CODE_CHANGED"
    assert summary["results"] == []


def test_failed_baseline_remains_in_denominator_without_scoring(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(monkeypatch, tmp_path, second_status="PREPARATION_OR_INPUT_FAILED")
    patch_runtime(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(probe.corpus, "score_frozen", lambda *args: calls.append(args) or {"result": "PASS", "samples": 3, "ate_translation_max_m": 0.001})

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["dataset_count"] == 2
    assert [row["status"] for row in summary["results"]] == ["COMPLETED", "PREPARATION_OR_INPUT_FAILED"]
    assert len(calls) == 1
    assert summary["aggregates"]["physical_stereo_only/none"]["unscored_count"] == 1


def test_no_overwrite_existing_output(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(monkeypatch, tmp_path)
    (tmp_path / "out").mkdir()

    with pytest.raises(SystemExit):
        probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")])


def test_real_physical_stereo_module_api_smoke_when_available():
    if not probe.TRANSFORM_MODULE.exists():
        pytest.skip("physical_stereo_lever module not published in this checkout yet")
    from ego_vio.vio.physical_stereo_lever import transform_shared_stereo_body_lever

    times = np.asarray([0.0, 0.02])
    rotations = np.repeat(np.eye(3)[None, :, :], 2, axis=0)
    rows, diag = transform_shared_stereo_body_lever(
        times,
        rotations,
        [{
            "eye": "left",
            "first_index": 0,
            "second_index": 1,
            "first_t_sec": 0.0,
            "second_t_sec": 0.02,
            "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
            "metric_displacement_frame": "infrared_left_camera_i",
            "observation_confidence": 0.8,
            "body_t_camera": np.eye(4).tolist(),
        }],
        [shared_row(times)],
    )
    assert diag["external_ground_truth_used"] is False
    assert rows[0]["metric_displacement_frame"] == "body_i"
