import importlib.util
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
    "joint_rotation_vi_feedback_probe",
    ROOT / "scripts/run_joint_rotation_vi_feedback_probe.py",
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return probe.file_hash(path)


def make_state():
    times = np.asarray([1.0, 1.02, 1.04])
    positions = np.asarray([[0.0, 0.0, 0.0], [0.02, 0.0, 0.0], [0.04, 0.0, 0.0]])
    rows = [
        {
            "t_sec": f"{timestamp:.9f}",
            "x": "0",
            "y": "0",
            "z": "0",
            "qw": "1",
            "qx": "0",
            "qy": "0",
            "qz": "0",
        }
        for timestamp in times
    ]
    return SimpleNamespace(
        times=times,
        positions=positions,
        rotations=Rotation.identity(3),
        rows=rows,
        mono=times.copy(),
        imu_times=times.copy(),
        gyro=np.zeros((3, 3)),
        accel=np.zeros((3, 3)),
        config={"td_s": -0.009109323},
        source_quality={},
        binding={},
        imu_info={},
    )


def write_joint_artifact(root: Path, record: dict, state, rotations: Rotation | None = None):
    artifact = root / record["id"] / probe.JOINT_VARIANT
    artifact.mkdir(parents=True)
    if rotations is None:
        rotations = Rotation.from_euler("z", [0.0, 0.01, 0.02])
    estimate = artifact / "body_trajectory_fused.csv"
    probe.fusion.write_trajectory(estimate, state.rows, state.positions + 1.0, rotations)
    source = artifact / "joint_source.txt"
    source.write_text("joint source\n", encoding="utf-8")
    diagnostic = {
        "schema": "umi_joint_stereo_se3_pose_only_diagnostic_v1",
        "least_squares_success": True,
        "optimize_rotations": True,
    }
    write_json(artifact / "solver_diagnostic.json", diagnostic)
    candidate = {
        "schema": "umi_joint_stereo_se3_pose_only_candidate_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": str(Path(record["session"]).resolve()),
        "variant": probe.JOINT_VARIANT,
        "input_sha256": {str(source.resolve()): sha(source)},
        "output_estimate_sha256": sha(estimate),
        "bound_samples": len(state.times),
        "solver_diagnostic": diagnostic,
    }
    write_json(artifact / "candidate_manifest.json", candidate)
    return artifact


def test_joint_artifact_validation_uses_rotations_not_positions_and_rejects_gt(tmp_path):
    state = make_state()
    record = {"id": "case", "session": str(tmp_path / "session")}
    Path(record["session"]).mkdir()
    write_joint_artifact(tmp_path / "joint", record, state)

    rotations, candidate, paths, gauge_report = probe.validate_joint_rotation_artifact(
        record,
        tmp_path / "joint",
        state,
    )

    assert candidate["variant"] == probe.JOINT_VARIANT
    assert {path.name for path in paths} == {
        "candidate_manifest.json",
        "solver_diagnostic.json",
        "body_trajectory_fused.csv",
    }
    np.testing.assert_allclose(rotations[0], np.eye(3), atol=1e-12)
    assert not np.allclose(rotations[1], np.eye(3))
    assert gauge_report["first_pose_restored_to_original_reference_rotation"] is True

    candidate_path = tmp_path / "joint" / "case" / probe.JOINT_VARIANT / "candidate_manifest.json"
    candidate = json.loads(candidate_path.read_text())
    candidate["external_ground_truth_used"] = True
    write_json(candidate_path, candidate)
    with pytest.raises(ValueError, match="ground truth"):
        probe.validate_joint_rotation_artifact(record, tmp_path / "joint", state)


def test_joint_artifact_accepts_nontrivial_first_gauge_csv_roundtrip_and_restores(tmp_path):
    state = make_state()
    state.rotations = Rotation.from_euler("zyx", [[0.39, -0.01, 0.002], [0.4, 0, 0], [0.41, 0, 0]])
    record = {"id": "case", "session": str(tmp_path / "session")}
    Path(record["session"]).mkdir()
    write_joint_artifact(tmp_path / "joint", record, state, rotations=state.rotations)

    rotations, _candidate, _paths, gauge_report = probe.validate_joint_rotation_artifact(
        record,
        tmp_path / "joint",
        state,
    )

    np.testing.assert_allclose(rotations[0], state.rotations.as_matrix()[0], atol=0.0)
    assert gauge_report["serialized_first_angle_rad"] <= 1e-12
    assert gauge_report["original_vs_serialized_first_angle_rad"] > 0.0


def test_joint_artifact_rejects_non_serialization_first_gauge_change(tmp_path):
    state = make_state()
    state.rotations = Rotation.from_euler("zyx", [[0.39, -0.01, 0.002], [0.4, 0, 0], [0.41, 0, 0]])
    changed = Rotation.from_matrix(state.rotations.as_matrix().copy())
    matrices = changed.as_matrix()
    matrices[0] = Rotation.from_euler("z", 1e-5).as_matrix() @ matrices[0]
    record = {"id": "case", "session": str(tmp_path / "session")}
    Path(record["session"]).mkdir()
    write_joint_artifact(tmp_path / "joint", record, state, rotations=Rotation.from_matrix(matrices))

    with pytest.raises(ValueError, match="first-pose gauge"):
        probe.validate_joint_rotation_artifact(record, tmp_path / "joint", state)


def test_validate_control_identity_requires_exact_combined_estimate_and_vectors(tmp_path):
    combined = tmp_path / "combined"
    generated = tmp_path / "generated"
    combined.mkdir()
    generated.mkdir()
    motion = [{
        "eye": "left",
        "first_index": 0,
        "second_index": 1,
        "confidence": 0.5,
        "metric_displacement_world_m": [1.0, 2.0, 3.0],
    }]
    stereo = [{
        "first_index": 0,
        "second_index": 1,
        "pnp_inlier_ratio": 0.75,
        "metric_displacement_camera_i_m": [0.1, 0.2, 0.3],
    }]
    write_json(combined / "local_motion_factors.json", motion)
    write_json(combined / "shared_stereo_observations.json", stereo)
    write_json(generated / "local_motion_factors.json", motion)
    write_json(generated / "shared_stereo_observations.json", stereo)
    (combined / "body_trajectory_fused.csv").write_text("same\n", encoding="utf-8")
    (generated / "body_trajectory_fused.csv").write_text("same\n", encoding="utf-8")

    identity = probe.validate_control_identity(generated, combined, motion, stereo)

    assert identity["motion"]["max_abs_error_m"] == 0.0
    assert identity["stereo"]["max_abs_error_m"] == 0.0

    (generated / "body_trajectory_fused.csv").write_text("different\n", encoding="utf-8")
    with pytest.raises(ValueError, match="byte-match"):
        probe.validate_control_identity(generated, combined, motion, stereo)


def test_control_identity_rejects_nonfinite_vectors():
    with pytest.raises(ValueError, match="non-finite"):
        probe._compare_vec3_lists(
            [{"metric_displacement_world_m": [float("nan"), 0.0, 0.0]}],
            [{"metric_displacement_world_m": [float("nan"), 0.0, 0.0]}],
            "metric_displacement_world_m",
            "control_motion_factor",
        )


def patch_run_record(
    monkeypatch,
    tmp_path,
    *,
    control_fails=False,
    patch_freeze=True,
    mutate_source_after_snapshot=False,
    mutate_baseline_input_after_snapshot=False,
):
    state = make_state()
    record = {
        "id": "case",
        "session": str(tmp_path / "session"),
        "capture_dir": str(tmp_path / "capture"),
        "vins_dir": str(tmp_path / "vins"),
        "reference_manifest": str(tmp_path / "reference.json"),
    }
    baseline_candidate = {
        "input_sha256": {},
        "policy_arguments": dict(probe.base.BASELINE_POLICY_ARGUMENTS),
    }
    baseline_graph = {"output_frame": "body_imu_origin"}
    joint_rotations = Rotation.from_euler("z", [0.0, 0.01, 0.02]).as_matrix()
    original_factors = [{"first_index": 0, "second_index": 1}]
    original_stereo = [{"first_index": 0, "second_index": 1, "pnp_inlier_ratio": 0.8}]
    paths = []
    for name in ("baseline", "constant", "combined", "joint"):
        path = tmp_path / f"{name}.txt"
        path.write_text(name + "\n", encoding="utf-8")
        paths.append(path)
    baseline_input = tmp_path / "raw_imu.bin"
    baseline_input.write_bytes(b"original raw imu")
    baseline_candidate["input_sha256"] = {
        str(baseline_input.resolve()): sha(baseline_input)
    }
    artifact = tmp_path / "baseline" / "case" / probe.BASELINE_POLICY
    artifact.mkdir(parents=True)
    for name in (
        "candidate_manifest.json",
        "graph_report.json",
        "local_motion_factors.json",
        "shared_stereo_observations.json",
        "body_trajectory_fused.csv",
    ):
        (artifact / name).write_text(name + "\n", encoding="utf-8")

    if patch_freeze:
        monkeypatch.setattr(probe, "check_frozen", lambda hashes: None)
    monkeypatch.setattr(probe.base, "validate_record_sources", lambda rec: None)
    monkeypatch.setattr(
        probe.base,
        "validate_baseline_artifact",
        lambda rec, artifact: (baseline_candidate, baseline_graph),
    )
    monkeypatch.setattr(probe.base, "load_bound_reference", lambda rec: state)
    monkeypatch.setattr(probe.base, "validate_baseline_trajectory_identity", lambda *args: None)
    monkeypatch.setattr(
        probe.constant,
        "reconstruct_tracks",
        lambda rec, st, cand: ({"left": {"eye": "left"}, "right": {"eye": "right"}}, {}, [paths[0]]),
    )
    def fake_read_json(path):
        path = Path(path)
        if path.is_file() and path.name == "candidate_manifest.json":
            return json.loads(path.read_text(encoding="utf-8"))
        if path.name == "local_motion_factors.json":
            return original_factors
        return original_stereo

    monkeypatch.setattr(probe, "read_json", fake_read_json)
    monkeypatch.setattr(
        probe,
        "validate_constant_artifact",
        lambda rec, root: (
            tmp_path / "constant_artifact",
            {"input_sha256": {str(paths[1].resolve()): sha(paths[1])}},
            [paths[1]],
        ),
    )
    monkeypatch.setattr(
        probe,
        "validate_combined_artifact",
        lambda rec, root: (
            tmp_path / "combined_artifact",
            {"input_sha256": {str(paths[2].resolve()): sha(paths[2])}},
            [paths[2]],
        ),
    )
    monkeypatch.setattr(
        probe,
        "validate_joint_rotation_artifact",
        lambda rec, root, st: (
            joint_rotations,
            {
                "output_estimate_sha256": "joint-hash",
                "input_sha256": {str(paths[3].resolve()): sha(paths[3])},
            },
            [paths[3]],
            {"schema": "joint_rotation_first_gauge_serialization_v1"},
        ),
    )
    monkeypatch.setattr(
        probe.physical,
        "load_all_eye_candidates",
        lambda rec, cand, times: ([{"eye": "left"}], {"left": {}}, [paths[0]]),
    )
    transform_calls = []

    def fake_transform_motion(st, tracks, factors, rotations):
        if mutate_source_after_snapshot:
            paths[0].write_text("mutated source\n", encoding="utf-8")
        if mutate_baseline_input_after_snapshot:
            baseline_input.write_bytes(b"mutated raw imu")
        transform_calls.append(("motion", np.asarray(rotations).copy()))
        return ([{"metric_displacement_world_m": [float(rotations[1, 0, 0]), 0.0, 0.0]}],
                {"schema": "constant_ir_gauge_diagnostic_v1"})

    def fake_transform_stereo(st, rotations, eyes, rows):
        transform_calls.append(("stereo", np.asarray(rotations).copy()))
        return ([{"pnp_inlier_ratio": 0.8, "metric_displacement_camera_i_m": [float(rotations[1, 0, 0]), 0.0, 0.0]}],
                {"schema": "physical_stereo_shared_row_identity_v1"})

    monkeypatch.setattr(probe, "transform_motion_factors", fake_transform_motion)
    monkeypatch.setattr(probe, "transform_shared_rows_with_rotations", fake_transform_stereo)
    monkeypatch.setattr(
        probe,
        "_compare_vec3_lists",
        lambda *args, **kwargs: {"schema": "identity", "count": 1, "max_abs_error_m": 0.0},
    )

    if control_fails:
        monkeypatch.setattr(
            probe,
            "validate_control_identity",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("control mismatch")),
        )
    else:
        monkeypatch.setattr(
            probe,
            "validate_control_identity",
            lambda *args, **kwargs: {"schema": "original_rotation_control_identity_v1"},
        )

    run_calls = []

    def fake_run_solver_arm(
        rec,
        artifact,
        variant,
        variant_dir,
        arm_state,
        _baseline_candidate,
        transformed_stereo,
        stereo_report,
        raw_report_paths,
        motion_factors,
        motion_source,
        extra_paths,
        frozen_hashes,
    ):
        probe.check_frozen(frozen_hashes)
        run_calls.append({
            "variant": variant,
            "positions": np.asarray(arm_state.positions).copy(),
            "rotations": arm_state.rotations.as_matrix().copy(),
            "motion_source": motion_source,
            "extra_paths": [Path(path).name for path in extra_paths],
        })
        variant_dir.mkdir(parents=True, exist_ok=True)
        write_json(variant_dir / "candidate_manifest.json", {
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "input_sha256": {},
            "policy_arguments": {},
        })
        write_json(variant_dir / "local_motion_factors.json", motion_factors)
        write_json(variant_dir / "shared_stereo_observations.json", transformed_stereo)
        (variant_dir / "body_trajectory_fused.csv").write_text("estimate\n", encoding="utf-8")
        return {
            "artifact_dir": str(variant_dir.resolve()),
            "score": {"result": "PASS", "ate_translation_max_m": 0.001},
            "estimate_sha256": "estimate-hash",
        }

    monkeypatch.setattr(probe, "run_solver_arm", fake_run_solver_arm)
    return record, state, joint_rotations, transform_calls, run_calls


def test_run_record_uses_only_joint_rotations_for_feedback_and_keeps_timeline(tmp_path, monkeypatch):
    record, state, joint_rotations, transform_calls, run_calls = patch_run_record(
        monkeypatch,
        tmp_path,
    )

    result = probe.run_record(
        record,
        tmp_path / "baseline",
        tmp_path / "constant",
        tmp_path / "joint",
        tmp_path / "combined",
        tmp_path / "out",
        {"code": "hash"},
    )

    assert result["status"] == "COMPLETED"
    assert [call["variant"] for call in run_calls] == probe.VARIANTS
    np.testing.assert_allclose(run_calls[0]["rotations"], state.rotations.as_matrix())
    np.testing.assert_allclose(run_calls[1]["rotations"], joint_rotations)
    np.testing.assert_allclose(run_calls[1]["positions"], state.positions)
    assert "joint.txt" in run_calls[1]["extra_paths"]
    candidate = json.loads(
        (tmp_path / "out" / "case" / probe.FEEDBACK_VARIANT / "candidate_manifest.json").read_text()
    )
    assert candidate["schema"] == "umi_joint_rotation_vi_feedback_candidate_v1"
    assert candidate["external_ground_truth_used"] is False
    assert candidate["staged_single_rotation_feedback"]["uses_joint_positions"] is False
    assert candidate["staged_single_rotation_feedback"]["reference_rotation_source"] == (
        "joint_stereo_se3_pose_only_rotations"
    )
    assert transform_calls[0][0] == "motion"
    np.testing.assert_allclose(transform_calls[0][1], state.rotations.as_matrix())
    np.testing.assert_allclose(transform_calls[2][1], joint_rotations)


def test_control_identity_failure_skips_feedback_arm(tmp_path, monkeypatch):
    record, _state, _joint_rotations, _transform_calls, run_calls = patch_run_record(
        monkeypatch,
        tmp_path,
        control_fails=True,
    )

    result = probe.run_record(
        record,
        tmp_path / "baseline",
        tmp_path / "constant",
        tmp_path / "joint",
        tmp_path / "combined",
        tmp_path / "out",
        {"code": "hash"},
    )

    assert result["status"] == "INCOMPLETE_VARIANTS"
    assert [call["variant"] for call in run_calls] == [probe.ORIGINAL_VARIANT]
    feedback = result["variants"][probe.FEEDBACK_VARIANT]
    assert feedback["error_code"] == "VARIANT_FAILED"
    assert feedback["stage"] == "control_identity"
    assert "feedback skipped" in feedback["error"]


def test_source_mutation_after_snapshot_raises_stop_code_changed(tmp_path, monkeypatch):
    code = tmp_path / "code.py"
    code.write_text("code\n", encoding="utf-8")
    record, _state, _joint_rotations, _transform_calls, _run_calls = patch_run_record(
        monkeypatch,
        tmp_path,
        patch_freeze=False,
        mutate_source_after_snapshot=True,
    )

    with pytest.raises(probe.base.StopCodeChanged):
        probe.run_record(
            record,
            tmp_path / "baseline",
            tmp_path / "constant",
            tmp_path / "joint",
            tmp_path / "combined",
            tmp_path / "out",
            {str(code.resolve()): sha(code)},
        )


def test_baseline_candidate_input_mutation_after_snapshot_raises_stop_code_changed(
    tmp_path,
    monkeypatch,
):
    code = tmp_path / "code.py"
    code.write_text("code\n", encoding="utf-8")
    record, _state, _joint_rotations, _transform_calls, _run_calls = patch_run_record(
        monkeypatch,
        tmp_path,
        patch_freeze=False,
        mutate_baseline_input_after_snapshot=True,
    )

    with pytest.raises(probe.base.StopCodeChanged):
        probe.run_record(
            record,
            tmp_path / "baseline",
            tmp_path / "constant",
            tmp_path / "joint",
            tmp_path / "combined",
            tmp_path / "out",
            {str(code.resolve()): sha(code)},
        )


def test_main_rejects_existing_output(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(SystemExit):
        probe.main([
            "--manifest",
            str(tmp_path / "missing_manifest.json"),
            "--baseline",
            str(tmp_path / "baseline"),
            "--constant-root",
            str(tmp_path / "constant"),
            "--joint-root",
            str(tmp_path / "joint"),
            "--combined-root",
            str(tmp_path / "combined"),
            "--output",
            str(output),
        ])
