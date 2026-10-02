import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_sift_lm_right_source_overrides as prep  # noqa: E402
import evaluate_sift_lm_dual_combined_probe as dual_eval  # noqa: E402


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_trajectory(path: Path, positions: list[list[float]], rotations: list[Rotation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["t_sec,x,y,z,qw,qx,qy,qz"]
    for index, (position, rotation) in enumerate(zip(positions, rotations)):
        qx, qy, qz, qw = rotation.as_quat()
        rows.append(f"{index},{position[0]},{position[1]},{position[2]},{qw},{qx},{qy},{qz}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def make_noncommuting_observation():
    right_from_left = Rotation.from_euler("z", 35, degrees=True)
    left_rotation = Rotation.from_euler("x", 20, degrees=True)
    assert not np.allclose(
        (right_from_left * left_rotation).as_matrix(),
        (left_rotation * right_from_left).as_matrix(),
    )
    left_displacement = np.asarray([0.004, -0.012, 0.003])
    left_translation = -left_rotation.apply(left_displacement)
    right_rotation, right_translation = prep.derivation.change_relative_pose_frame(
        left_rotation,
        left_translation,
        right_from_left,
        np.zeros(3),
    )
    right_displacement = -right_rotation.inv().apply(right_translation)
    return {
        "right_from_left": right_from_left,
        "right_rotation": right_rotation,
        "right_displacement": right_displacement,
        "left_observation": {
            "accepted": True,
            "method": "sift",
            "first_index": 0,
            "second_index": 1,
            "first_t_sec": 0.0,
            "second_t_sec": 1.0,
            "metric_displacement_frame": "infrared_left_camera_i",
            "metric_displacement_camera_i_m": left_displacement.tolist(),
            "pnp_rotation_quaternion_xyzw": left_rotation.as_quat().tolist(),
            "pnp_inlier_ratio": 0.8,
        },
    }


def make_fixture(tmp_path: Path, monkeypatch):
    session = tmp_path / "session"
    (session / "external_imu").mkdir(parents=True)
    (session / "d405_frames.csv").write_text("frame\n", encoding="utf-8")
    (session / "external_imu" / "imu.bin").write_bytes(b"imu")
    db3 = tmp_path / "capture.db3"
    db3.write_bytes(b"factory")
    left_traj = tmp_path / "left_cache" / "trajectory_imu_metric.csv"
    right_raw_traj = tmp_path / "right_cache" / "trajectory_frames.csv"
    right_metric_traj = tmp_path / "right_cache" / "imu_metric_trajectory.csv"
    geom = make_noncommuting_observation()
    write_trajectory(left_traj, [[0, 0, 0], [0.01, 0, 0]], [Rotation.identity(), Rotation.identity()])
    write_trajectory(
        right_raw_traj,
        [[0, 0, 0], geom["right_displacement"].tolist()],
        [Rotation.identity(), geom["right_rotation"].inv()],
    )
    write_trajectory(
        right_metric_traj,
        [[0, 0, 0], [9.0, 0, 0]],
        [Rotation.identity(), Rotation.identity()],
    )
    factory = {"baseline_m": 0.018}
    left_reports = []
    right_reports = []
    refined_reports = []
    for left_name, right_name in zip(prep.physical.eye_report_names("left"), prep.physical.eye_report_names("right")):
        left_path = tmp_path / "left_cache" / left_name
        right_path = tmp_path / "right_cache" / right_name
        refined_path = tmp_path / "source" / "rec1" / "refined_left_sources" / left_name
        observations = [dict(geom["left_observation"], first_index=0, second_index=1) for _ in range(4)]
        left_report = {
            "schema": "umi_mast3r_stereo_scale_v2",
            "result": "PASS",
            "session": str(session.resolve()),
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "observation_frame": "infrared_left_camera_i",
            "trajectory": str(left_traj.resolve()),
            "db3": str(db3.resolve()),
            "factory_stereo_calibration": factory,
            "observations": observations,
        }
        write_json(left_path, left_report)
        write_json(refined_path, left_report)
        write_json(
            right_path,
            {
                **left_report,
                "observation_frame": "infrared_right_camera_i",
                "trajectory": str(right_raw_traj.resolve()),
                "factory_stereo_calibration": {
                    **factory,
                    "right_rotation_from_left": geom["right_from_left"].as_matrix().tolist(),
                },
                "derived_from_left_stereo_report": str(left_path.resolve()),
            },
        )
        left_reports.append(left_path)
        right_reports.append(right_path)
        refined_reports.append(refined_path)

    candidate_inputs = {}
    for path in [left_traj, right_metric_traj, *left_reports, *right_reports]:
        candidate_inputs[str(path.resolve())] = prep.file_hash(path)
    candidate = {
        "schema": "umi_dual_ir_symmetric_experiment_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": str(session.resolve()),
        "shared_gauge": "VINS_body_world_first_node_only",
        "primary_eye": None,
        "shared_scale_state": False,
        "body_t_camera_in_solver": np.eye(4).tolist(),
        "input_sha256": candidate_inputs,
        "policy_arguments": prep.base.BASELINE_POLICY_ARGUMENTS,
    }
    graph = {
        "schema": "umi_dual_ir_symmetric_graph_diagnostic_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "output_frame": "body_imu_origin",
        "policy_arguments": prep.base.BASELINE_POLICY_ARGUMENTS,
    }
    artifact = tmp_path / "baseline" / "rec1" / "both"
    write_json(artifact / "candidate_manifest.json", candidate)
    write_json(artifact / "graph_report.json", graph)
    for name in ("local_motion_factors.json", "shared_stereo_observations.json"):
        write_json(artifact / name, [])
    (artifact / "body_trajectory_fused.csv").write_text("t_sec,x,y,z,qw,qx,qy,qz\n", encoding="utf-8")
    override = {str(path.resolve()): prep.file_hash(path) for path in refined_reports}
    source_stage = tmp_path / "source"
    write_json(
        source_stage / "preflight_report.json",
        {
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "refined_sources": [
                {
                    "id": "rec1",
                    "refined_left_sources": [str(path.resolve()) for path in refined_reports],
                    "source_override_sha256": override,
                }
            ],
        },
    )
    manifest = tmp_path / "manifest.json"
    record = {
        "id": "rec1",
        "session": str(session.resolve()),
        "capture_dir": str(tmp_path.resolve()),
        "vins_dir": str((tmp_path / "vins").resolve()),
        "reference_manifest": str((tmp_path / "reference.json").resolve()),
    }
    write_json(manifest, {"records": [record]})
    monkeypatch.setattr(
        prep.derivation,
        "load_stereo_calibration",
        lambda _db3: {
            "baseline_m": 0.018,
            "right_rotation_from_left": geom["right_from_left"].as_matrix(),
            "right_translation_from_left_m": np.zeros(3),
        },
    )
    monkeypatch.setattr(prep.base, "validate_baseline_artifact", lambda _record, _artifact: (candidate, graph))
    return manifest, tmp_path / "baseline", source_stage, geom


def run_fixture(tmp_path: Path, monkeypatch):
    manifest, baseline, source_stage, geom = make_fixture(tmp_path, monkeypatch)
    args = prep.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(source_stage),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    return prep.run(args), geom


def test_generates_right_overrides_with_native_noncommuting_conversion(tmp_path, monkeypatch):
    result, geom = run_fixture(tmp_path, monkeypatch)
    assert result["status"] == "PREFLIGHT_COMPLETE"
    row = result["records"][0]
    assert len(row["refined_right_sources"]) == 4
    assert row["original_baseline_input_sha256_preserved"] is True
    assert row["factor_output_count"] == 0
    right_report = json.loads(Path(row["refined_right_sources"][0]).read_text(encoding="utf-8"))
    assert right_report["observation_frame"] == "infrared_right_camera_i"
    assert Path(right_report["trajectory"]).name == "trajectory_frames.csv"
    assert Path(right_report["downstream_metric_trajectory"]).name == "imu_metric_trajectory.csv"
    converted = right_report["observations"][0]
    assert converted["accepted"] is True
    assert np.allclose(converted["metric_displacement_camera_i_m"], geom["right_displacement"])
    assert row["right_derivation_left_source_sha256"][row["refined_right_sources"][0]]["left_source_path"]
    assert row["right_derivation_left_source_sha256"][row["refined_right_sources"][0]]["left_source_sha256"]
    assert row["refined_right_sources"][0] in row["source_override_sha256"]
    assert row["refined_left_sources"][0] in row["source_override_sha256"]
    loaded = dual_eval.load_source_stage(tmp_path / "out")
    stage_record = dual_eval.source_eval.validate_source_stage_record("rec1", loaded)
    evidence = dual_eval.validate_right_derivation_evidence(
        stage_record,
        [Path(path) for path in stage_record["refined_right_sources"]],
    )
    assert len(evidence) == 4
    assert result["refined_sources"] == result["records"]


def test_raw_geometry_trajectory_is_used_for_conversion_not_metric(tmp_path, monkeypatch):
    result, geom = run_fixture(tmp_path, monkeypatch)
    right_report = json.loads(Path(result["records"][0]["refined_right_sources"][0]).read_text(encoding="utf-8"))
    assert right_report["quality"]["accepted_observations"] == 4
    assert np.allclose(
        right_report["observations"][0]["metric_displacement_camera_i_m"],
        geom["right_displacement"],
    )
    assert result["records"][0]["right_raw_geometry_trajectory"].endswith("trajectory_frames.csv")
    assert result["records"][0]["right_downstream_metric_trajectory"].endswith("imu_metric_trajectory.csv")


def test_wrong_refined_left_lineage_fails_visible(tmp_path, monkeypatch):
    manifest, baseline, source_stage, _geom = make_fixture(tmp_path, monkeypatch)
    refined = next((source_stage / "rec1" / "refined_left_sources").glob("stereo_scale_bidirectional_report.json"))
    data = json.loads(refined.read_text(encoding="utf-8"))
    data["trajectory"] = str((tmp_path / "other.csv").resolve())
    write_json(refined, data)
    stage = json.loads((source_stage / "preflight_report.json").read_text(encoding="utf-8"))
    stage["refined_sources"][0]["source_override_sha256"][str(refined.resolve())] = prep.file_hash(refined)
    write_json(source_stage / "preflight_report.json", stage)
    args = prep.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(source_stage),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    result = prep.run(args)
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "does not preserve trajectory" in result["failures"][0]["error"]
    assert result["records"] == []
    assert result["refined_sources"] == []


def test_original_right_must_bind_corresponding_original_left(tmp_path, monkeypatch):
    manifest, baseline, source_stage, _geom = make_fixture(tmp_path, monkeypatch)
    right = next((tmp_path / "right_cache").glob("stereo_scale_right_report.json"))
    data = json.loads(right.read_text(encoding="utf-8"))
    data["derived_from_left_stereo_report"] = str((tmp_path / "left_cache" / "wrong.json").resolve())
    write_json(right, data)
    candidate = json.loads((baseline / "rec1" / "both" / "candidate_manifest.json").read_text(encoding="utf-8"))
    candidate["input_sha256"][str(right.resolve())] = prep.file_hash(right)
    write_json(baseline / "rec1" / "both" / "candidate_manifest.json", candidate)
    graph = json.loads((baseline / "rec1" / "both" / "graph_report.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(
        prep.base,
        "validate_baseline_artifact",
        lambda _record, _artifact: (json.loads((baseline / "rec1" / "both" / "candidate_manifest.json").read_text(encoding="utf-8")), graph),
    )
    args = prep.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(source_stage),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    result = prep.run(args)
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "not derived from corresponding original LEFT" in result["failures"][0]["error"]
    assert result["refined_sources"] == []


def test_original_right_factory_rotation_must_match_db3(tmp_path, monkeypatch):
    manifest, baseline, source_stage, _geom = make_fixture(tmp_path, monkeypatch)
    right = next((tmp_path / "right_cache").glob("stereo_scale_right_report.json"))
    data = json.loads(right.read_text(encoding="utf-8"))
    data["factory_stereo_calibration"]["right_rotation_from_left"] = Rotation.from_euler(
        "y",
        11,
        degrees=True,
    ).as_matrix().tolist()
    write_json(right, data)
    candidate = json.loads((baseline / "rec1" / "both" / "candidate_manifest.json").read_text(encoding="utf-8"))
    candidate["input_sha256"][str(right.resolve())] = prep.file_hash(right)
    write_json(baseline / "rec1" / "both" / "candidate_manifest.json", candidate)
    graph = json.loads((baseline / "rec1" / "both" / "graph_report.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(
        prep.base,
        "validate_baseline_artifact",
        lambda _record, _artifact: (json.loads((baseline / "rec1" / "both" / "candidate_manifest.json").read_text(encoding="utf-8")), graph),
    )
    args = prep.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(source_stage),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    result = prep.run(args)
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "right_rotation_from_left does not match DB3" in result["failures"][0]["error"]
    assert result["refined_sources"] == []


def test_db3_report_hash_is_deduplicated_per_record(tmp_path, monkeypatch):
    manifest, baseline, source_stage, _geom = make_fixture(tmp_path, monkeypatch)
    original_file_hash = prep.file_hash
    db3 = tmp_path / "capture.db3"
    calls = []

    def tracking_hash(path):
        if Path(path).resolve() == db3.resolve():
            calls.append(Path(path))
        return original_file_hash(path)

    monkeypatch.setattr(prep, "file_hash", tracking_hash)
    args = prep.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(source_stage),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    result = prep.run(args)
    assert result["status"] == "PREFLIGHT_COMPLETE"
    # before guard + one report provenance hash + after guard, not once per four reports
    assert len(calls) == 3


def test_failed_derived_right_report_is_not_marked_ready(tmp_path, monkeypatch):
    manifest, baseline, source_stage, _geom = make_fixture(tmp_path, monkeypatch)

    def fail_scale(_observations, min_observations):
        raise ValueError(f"insufficient accepted stereo scale observations: 0 < {min_observations}")

    monkeypatch.setattr(prep.derivation, "robust_scale", fail_scale)
    args = prep.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(source_stage),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    result = prep.run(args)
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "derived RIGHT report failed scale/quality validation" in result["failures"][0]["error"]
    assert result["records"] == []
    assert result["refined_sources"] == []


def test_source_hash_failure_is_visible(tmp_path, monkeypatch):
    manifest, baseline, source_stage, _geom = make_fixture(tmp_path, monkeypatch)
    refined = next((source_stage / "rec1" / "refined_left_sources").glob("stereo_scale_bidirectional_report.json"))
    refined.write_text(refined.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    args = prep.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(source_stage),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    result = prep.run(args)
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert result["failures"][0]["stage"] == "load_source_stage"
    assert "override hash changed" in result["failures"][0]["error"]
    assert result["refined_sources"] == []


def test_output_must_be_new(tmp_path, monkeypatch):
    manifest, baseline, source_stage, _geom = make_fixture(tmp_path, monkeypatch)
    output = tmp_path / "out"
    output.mkdir()
    args = prep.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(source_stage),
            "--output",
            str(output),
        ]
    )
    try:
        prep.run(args)
    except FileExistsError as error:
        assert str(output) in str(error)
    else:
        raise AssertionError("expected output overwrite refusal")
