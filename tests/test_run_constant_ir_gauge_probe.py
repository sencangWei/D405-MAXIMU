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
    "constant_ir_gauge_probe", ROOT / "scripts/run_constant_ir_gauge_probe.py"
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


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def sha(path: Path) -> str:
    return probe.file_hash(path)


def make_fixture(tmp_path: Path, *, second_status: str | None = None):
    config = tmp_path / "config" / "vins.yaml"
    imu_config = tmp_path / "config" / "imu.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("vins\n")
    imu_config.write_text("imu config\n")
    probe.symmetric.VINS_CONFIG = config
    probe.symmetric.IMU_CONFIG = imu_config
    records = []
    baseline = tmp_path / "baseline"
    for rid, status in (("case_01", "COMPLETED"), ("case_02", second_status)):
        if status is None:
            continue
        session = tmp_path / rid / "session"
        vins = tmp_path / rid / "vins"
        left = tmp_path / rid / "left"
        right = tmp_path / rid / "right"
        for directory in (session / "external_imu", vins, left, right):
            directory.mkdir(parents=True, exist_ok=True)
        (session / "d405_frames.csv").write_text(f"frames {rid}\n")
        (session / "external_imu/imu.bin").write_bytes(f"imu {rid}".encode())
        (vins / "vio_corrected_stream.csv").write_text(f"vins {rid}\n")
        (vins / "run_acceptance.json").write_text("{}\n")
        for directory, trajectory_name in (
            (left, "trajectory_imu_metric.csv"),
            (right, "imu_metric_trajectory.csv"),
        ):
            for name in (trajectory_name, "imu_scale_report.json", "stereo.json"):
                (directory / name).write_text(f"{directory.name} {name} {rid}\n")
        record = {
            "id": rid,
            "session": str(session),
            "capture_dir": str(tmp_path / rid / "capture"),
            "vins_dir": str(vins),
            "left_dir": str(left),
            "right_dir": str(right),
            "reference_manifest": str(tmp_path / rid / "reference.json"),
        }
        records.append(record)
        write_json(
            baseline / "summary.json",
            {
                "schema": "umi_dual_ir_development_regression_v1",
                "status": "COMPLETED_WITH_FAILURES" if second_status else "COMPLETED",
                "results": [
                    {"id": item["id"], "status": ("PREPARATION_OR_INPUT_FAILED" if item["id"] == "case_02" else "COMPLETED"), "variants": {"both": {}}}
                    for item in records
                ],
            },
        )
        if status != "COMPLETED":
            continue
        artifact = baseline / rid / "both"
        artifact.mkdir(parents=True)
        baseline_inputs = [
            vins / "vio_corrected_stream.csv",
            vins / "run_acceptance.json",
            session / "d405_frames.csv",
            session / "external_imu/imu.bin",
            config,
            imu_config,
            left / "trajectory_imu_metric.csv",
            left / "imu_scale_report.json",
            left / "stereo.json",
            right / "imu_metric_trajectory.csv",
            right / "imu_scale_report.json",
            right / "stereo.json",
        ]
        input_sha = {str(path.resolve()): sha(path) for path in baseline_inputs}
        write_json(
            artifact / "candidate_manifest.json",
            {
                "schema": "umi_dual_ir_symmetric_experiment_v1",
                "external_ground_truth_used": False,
                "slam_supervision": False,
                "session": str(session.resolve()),
                "input_sha256": input_sha,
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
        write_json(artifact / "shared_stereo_observations.json", [{"accepted": True, "first_index": 0, "second_index": 1, "pnp_inlier_ratio": 0.5}])
        (artifact / "body_trajectory_fused.csv").write_text("baseline\n")
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"records": records})
    return manifest, baseline, records


def patch_runtime(monkeypatch, tmp_path, *, changed_after_score=False):
    code = tmp_path / "code.py"
    code.write_text("code\n")
    monkeypatch.setattr(probe, "frozen_code_paths", lambda: [code])
    times = np.asarray([1.0, 1.1, 1.2])
    positions = np.asarray([[0.0, 0, 0], [0.1, 0, 0], [0.2, 0, 0]])
    rotations = Rotation.identity(3)
    rows = [{"t_sec": f"{t:.9f}", "x": "0", "y": "0", "z": "0", "qw": "1", "qx": "0", "qy": "0", "qz": "0"} for t in times]
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

    def fake_load_eye(directory, eye, *_args, **_kwargs):
        path = Path(directory)
        trajectory_name = "trajectory_imu_metric.csv" if eye == "left" else "imu_metric_trajectory.csv"
        return (
            {"eye": eye, "times": times.tolist()},
            {"orientation_refinement": {"eye": eye}},
            [path / trajectory_name, path / "imu_scale_report.json", path / "stereo.json"],
        )

    monkeypatch.setattr(probe.symmetric, "load_eye", fake_load_eye)

    def fake_build_symmetric_ir_factors(*_args, **_kwargs):
        return ([dict(BASELINE_FACTOR)], [], {"factor_count": 1})

    monkeypatch.setattr(probe.symmetric, "build_symmetric_ir_factors", fake_build_symmetric_ir_factors)

    def fake_transform_existing_motion_factors(reference_times, reference_rotations, tracks, original):
        assert isinstance(tracks, list)
        assert [track["eye"] for track in tracks] == ["left", "right"]
        return (
            [{**original[0], "metric_displacement_world_m": [0.5, 0.0, 0.0]}],
            {"schema": "constant_ir_gauge_transform_v1", "input_factor_count": len(original), "changed_factor_count": 1},
        )

    monkeypatch.setattr(probe, "transform_existing_motion_factors", fake_transform_existing_motion_factors)
    captured = {}

    def fake_refine(*_args, **kwargs):
        captured.update(kwargs)
        return positions + [0.01, 0, 0], {"secondary_visual_motion": {"edges": 1}}

    monkeypatch.setattr(probe.fusion, "refine_positions_visual_inertial", fake_refine)
    monkeypatch.setattr(probe.fusion, "write_trajectory", lambda path, rows, refined, rotations: path.write_text("estimate\n"))
    calls = {"code_changed": 0}

    def fake_code_changed(_hashes):
        calls["code_changed"] += 1
        return changed_after_score and calls["code_changed"] >= 3

    monkeypatch.setattr(probe, "code_changed", fake_code_changed)
    monkeypatch.setattr(probe.base, "code_changed", fake_code_changed)
    monkeypatch.setattr(probe.corpus, "score_frozen", lambda *args: {"result": "PASS", "samples": 3, "ate_translation_max_m": 0.001})
    return captured


def test_reconstruct_uses_hash_bound_caches_despite_stale_manifest_paths(tmp_path, monkeypatch):
    _manifest, baseline, records = make_fixture(tmp_path)
    patch_runtime(monkeypatch, tmp_path)
    record = dict(records[0])
    record["alternate_left_dir"] = record["left_dir"]
    record["left_dir"] = str(tmp_path / "original_failed_left")
    record["right_dir"] = None
    artifact = baseline / record["id"] / "both"
    candidate, _graph = probe.base.validate_baseline_artifact(record, artifact)
    state = probe.base.load_bound_reference(record)

    tracks, _reports, paths = probe.reconstruct_tracks(record, state, candidate)

    assert set(tracks) == {"left", "right"}
    assert {path.parent for path in paths} == {
        Path(records[0]["left_dir"]), Path(records[0]["right_dir"])
    }


def test_bound_eye_cache_rejects_missing_or_ambiguous_trajectory_paths():
    with pytest.raises(ValueError, match="exactly one"):
        probe.bound_eye_cache_directory({"input_sha256": {}}, "left")
    with pytest.raises(ValueError, match="exactly one"):
        probe.bound_eye_cache_directory({"input_sha256": {
            "/cache/a/imu_metric_trajectory.csv": "hash_a",
            "/cache/b/imu_metric_trajectory.csv": "hash_b",
        }}, "right")


def test_reconstructs_tracks_transforms_factors_and_scores_after_freeze(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(tmp_path)
    captured = patch_runtime(monkeypatch, tmp_path)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 0

    variant = tmp_path / "out" / "case_01" / "selected"
    candidate = json.loads((variant / "candidate_manifest.json").read_text())
    assert candidate["output_motion_factors_sha256"] == sha(variant / "local_motion_factors.json")
    assert candidate["external_ground_truth_used"] is False
    assert candidate["policy_arguments"]["learned_factor_transform"] == "constant_ir_fixed_so3_gauge_v1"
    assert candidate["track_reports"]["left"]["orientation_refinement"]["eye"] == "left"
    assert candidate["source_factor_reconstruction"]["factor_count"] == 1
    assert str((Path(_records[0]["left_dir"]) / "stereo.json").resolve()) in candidate["input_sha256"]
    assert str(probe.BASE_RUNNER_MODULE.resolve()) in candidate["input_sha256"]
    assert str(probe.TRANSFORM_MODULE.resolve()) in candidate["input_sha256"]
    assert captured["node_stride"] == 1
    assert captured["max_correction_m"] is None
    assert captured["use_visual_position_prior"] is False
    assert captured["solve_metric_scale"] is False
    assert captured["secondary_visual_factors"][0]["metric_displacement_world_m"] == [0.5, 0.0, 0.0]
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["status"] == "COMPLETED"


def test_real_constant_ir_gauge_wrapper_imports_and_uses_sequence_tracks():
    reference_times = np.asarray([1.0, 1.02])
    reference_rotations = np.repeat(np.eye(3)[None, :, :], 2, axis=0)
    body_t_camera = np.eye(4).tolist()
    tracks = [
        {
            "eye": eye,
            "times": reference_times.tolist(),
            "metric_camera_positions": [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]],
            "camera_rotations": reference_rotations.tolist(),
            "body_t_camera": body_t_camera,
        }
        for eye in ("left", "right")
    ]

    factors, diagnostic = probe.transform_existing_motion_factors(
        reference_times,
        reference_rotations,
        tracks,
        [BASELINE_FACTOR],
    )

    assert diagnostic["schema"] == "constant_ir_gauge_diagnostic_v1"
    assert diagnostic["external_ground_truth_used"] is False
    assert factors[0]["metric_displacement_world_m"] == pytest.approx([0.1, 0.0, 0.0])
    assert probe.TRANSFORM_MODULE.name == "constant_ir_gauge.py"
    assert probe.TRANSFORM_MODULE in probe.frozen_code_paths()
    assert probe.BASE_RUNNER_MODULE in probe.frozen_code_paths()


def test_reconstructed_factor_mismatch_fails_before_transform(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(tmp_path)
    patch_runtime(monkeypatch, tmp_path)
    transform_calls = []
    monkeypatch.setattr(
        probe.symmetric,
        "build_symmetric_ir_factors",
        lambda *_args, **_kwargs: (
            [{**BASELINE_FACTOR, "metric_displacement_world_m": [9.0, 0.0, 0.0]}],
            [],
            {"factor_count": 1},
        ),
    )
    monkeypatch.setattr(
        probe,
        "transform_existing_motion_factors",
        lambda *args: transform_calls.append(args) or ([], {}),
    )

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["selected"]
    assert variant["error_code"] == "VARIANT_FAILED"
    assert "reconstructed motion factor 0 vector changed" in variant["error"]
    assert transform_calls == []


def test_missing_track_source_hash_fails_variant(tmp_path, monkeypatch):
    manifest, baseline, records = make_fixture(tmp_path)
    patch_runtime(monkeypatch, tmp_path)
    candidate_path = baseline / "case_01" / "both" / "candidate_manifest.json"
    candidate = json.loads(candidate_path.read_text())
    candidate["input_sha256"].pop(str((Path(records[0]["right_dir"]) / "stereo.json").resolve()))
    write_json(candidate_path, candidate)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["selected"]
    assert variant["error_code"] == "VARIANT_FAILED"
    assert "track source not bound" in variant["error"]


def test_code_change_after_scoring_stops_without_mixing_following_results(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(tmp_path, second_status="PREPARATION_OR_INPUT_FAILED")
    patch_runtime(monkeypatch, tmp_path, changed_after_score=True)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 2

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["status"] == "STOP_CODE_CHANGED"
    assert summary["results"] == []


def test_no_overwrite_existing_output(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(tmp_path)
    (tmp_path / "out").mkdir()
    with pytest.raises(SystemExit):
        probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")])


def test_failed_baseline_remains_in_denominator_without_scoring(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(tmp_path, second_status="PREPARATION_OR_INPUT_FAILED")
    patch_runtime(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(probe.corpus, "score_frozen", lambda *args: calls.append(args) or {"result": "PASS", "samples": 3, "ate_translation_max_m": 0.001})

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["dataset_count"] == 2
    assert [row["status"] for row in summary["results"]] == ["COMPLETED", "PREPARATION_OR_INPUT_FAILED"]
    assert len(calls) == 1
    assert summary["aggregates"]["selected/none"]["unscored_count"] == 1


def test_dataset_option_limits_coverage(tmp_path, monkeypatch):
    manifest, baseline, _records = make_fixture(tmp_path, second_status="PREPARATION_OR_INPUT_FAILED")
    patch_runtime(monkeypatch, tmp_path)

    assert probe.main([
        "--manifest", str(manifest), "--baseline", str(baseline),
        "--output", str(tmp_path / "out"), "--dataset", "case_02",
    ]) == 3

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["dataset_count"] == 1
    assert summary["results"][0]["id"] == "case_02"
