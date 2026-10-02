import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_sift_lm_physical_source_probe as probe


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_source_stage(tmp_path):
    source = tmp_path / "refined_left.json"
    source.write_text("refined\n", encoding="utf-8")
    report = {
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "refined_sources": [
            {
                "id": "case",
                "refined_left_sources": [str(source.resolve())],
                "source_override_sha256": {
                    str(source.resolve()): probe.file_hash(source)
                },
            }
        ],
    }
    stage = tmp_path / "stage"
    write_json(stage / "preflight_report.json", report)
    return stage, source


def test_source_stage_requires_independent_override_hash(tmp_path):
    stage, source = make_source_stage(tmp_path)
    loaded = probe.load_source_stage(stage)
    assert loaded["refined_by_id"]["case"]["refined_left_sources"] == [str(source.resolve())]

    source.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="override hash changed"):
        probe.load_source_stage(stage)


def test_matched_left_rows_are_left_only_and_reject_unmatched():
    times = np.linspace(0.0, 1.0, 41)
    common = {
        "eye": "left",
        "first_index": 0,
        "second_index": 40,
        "first_t_sec": 0.0,
        "second_t_sec": 1.0,
        "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
        "metric_displacement_frame": "infrared_left_camera_i",
        "observation_confidence": 0.7,
        "body_t_camera": np.eye(4).tolist(),
        "reference_first_index": 0,
        "reference_second_index": 40,
    }
    original = [dict(common), dict(common, first_index=0, second_index=20,
        second_t_sec=0.5, reference_second_index=20, observation_confidence=0.8)]
    refined = [dict(common, metric_displacement_camera_i_m=[0.2, 0.0, 0.0])]
    state = SimpleNamespace(times=times, rotations=Rotation.identity(len(times)))

    original_rows, refined_rows, report = probe.build_matched_left_measurements(
        state, original, refined
    )

    assert report["matched_pair_count"] == 1
    assert report["dropped_original_only_pair_count"] == 1
    assert report["right_eye_used_as_independent_evidence"] is False
    assert original_rows[0]["metric_displacement_frame"] == "body_i"
    assert refined_rows[0]["metric_displacement_camera_i_m"] == [0.2, 0.0, 0.0]

    with pytest.raises(ValueError, match="LEFT candidates only"):
        probe.build_matched_left_measurements(
            state, [dict(common, eye="right")], refined
        )


def test_physical_body_lever_uses_noncommuting_body_rotation_order():
    times = np.linspace(0.0, 1.0, 41)
    rotation_mats = Rotation.from_euler("xy", [[35.0, 0.0], [0.0, 50.0]], degrees=True).as_matrix()
    rotations = Rotation.from_matrix(
        [rotation_mats[0] if index == 0 else rotation_mats[1] for index in range(len(times))]
    )
    body_t_camera = np.eye(4)
    body_t_camera[:3, 3] = [0.02, 0.01, -0.03]
    candidate = {
        "eye": "left",
        "first_index": 0,
        "second_index": 40,
        "first_t_sec": 0.0,
        "second_t_sec": 1.0,
        "metric_displacement_camera_i_m": [0.01, -0.02, 0.03],
        "metric_displacement_frame": "infrared_left_camera_i",
        "observation_confidence": 1.0,
        "body_t_camera": body_t_camera.tolist(),
        "reference_first_index": 0,
        "reference_second_index": 40,
    }
    rows = probe.matched_body_rows_from_candidates(times, [candidate], [(0, 40)])

    transformed, _report = probe.physical.transform_shared_rows(
        SimpleNamespace(times=times, rotations=rotations),
        [candidate],
        rows,
    )

    actual = np.asarray(transformed[0]["metric_displacement_camera_i_m"])
    r0, r1 = rotations.as_matrix()[0], rotations.as_matrix()[40]
    t = body_t_camera[:3, 3]
    expected = np.asarray(candidate["metric_displacement_camera_i_m"]) - r0.T @ r1 @ t + t
    wrong_commuted = np.asarray(candidate["metric_displacement_camera_i_m"]) - r1 @ r0.T @ t + t
    np.testing.assert_allclose(actual, expected, atol=1e-12)
    assert np.linalg.norm(actual - wrong_commuted) > 1e-4


def orchestration_state():
    times = np.asarray([0.0, 0.5, 1.0])
    return SimpleNamespace(
        times=times,
        mono=times.copy(),
        positions=np.zeros((3, 3)),
        rotations=Rotation.identity(3),
        rows=[
            {"t_sec": f"{t:.9f}", "x": "0", "y": "0", "z": "0",
             "qw": "1", "qx": "0", "qy": "0", "qz": "0"}
            for t in times
        ],
        imu_times=times.copy(),
        gyro=np.zeros((3, 3)),
        accel=np.zeros((3, 3)),
        config={"td_s": -0.009109323},
        source_quality={"ok": True},
        binding={"ok": True},
        imu_info={"ok": True},
    )


def test_run_record_preserves_baseline_hashes_and_writes_two_arms(monkeypatch, tmp_path):
    artifact = tmp_path / "baseline/case/both"
    artifact.mkdir(parents=True)
    for name in ("candidate_manifest.json", "graph_report.json",
                 "body_trajectory_fused.csv", "shared_stereo_observations.json"):
        (artifact / name).write_text(name, encoding="utf-8")
    write_json(artifact / "local_motion_factors.json", [])

    state = orchestration_state()
    baseline_candidate = {
        "policy_arguments": probe.base.BASELINE_POLICY_ARGUMENTS,
        "input_sha256": {str((tmp_path / "baseline_input.txt").resolve()): "original"},
    }
    source_stage = {
        "path": str((tmp_path / "stage/preflight_report.json").resolve()),
        "sha256": "stagehash",
        "refined_by_id": {
            "case": {
                "refined_left_sources": [str((tmp_path / "refined.json").resolve())],
                "source_override_sha256": {str((tmp_path / "refined.json").resolve()): "override"},
            }
        },
    }
    (tmp_path / "stage").mkdir()
    (tmp_path / "stage/preflight_report.json").write_text("stage", encoding="utf-8")
    (tmp_path / "refined.json").write_text("override", encoding="utf-8")
    (tmp_path / "baseline_input.txt").write_text("original", encoding="utf-8")

    monkeypatch.setattr(probe.base, "validate_record_sources", lambda record: None)
    monkeypatch.setattr(probe.base, "validate_baseline_artifact", lambda record, art: (baseline_candidate, {}))
    monkeypatch.setattr(probe.base, "load_bound_reference", lambda record: state)
    monkeypatch.setattr(probe.base, "validate_baseline_trajectory_identity", lambda art, st, graph: None)
    monkeypatch.setattr(probe.physical, "eye_report_paths_from_baseline", lambda cand, eye: [tmp_path / "left.json"])
    monkeypatch.setattr(probe, "load_left_candidates_from_reports", lambda *args, **kwargs: (
        [{"eye": "left", "reference_first_index": 0, "reference_second_index": 2}],
        {"report": "ok"},
        [tmp_path / "left.json"],
    ))
    monkeypatch.setattr(probe, "build_matched_left_measurements", lambda st, orig, refined: (
        [{"accepted": True, "first_index": 0, "second_index": 2, "pnp_inlier_ratio": 1.0,
          "metric_displacement_frame": "body_i", "first_t_sec": 0.0, "second_t_sec": 1.0,
          "metric_displacement_camera_i_m": [0, 0, 0]}],
        [{"accepted": True, "first_index": 0, "second_index": 2, "pnp_inlier_ratio": 1.0,
          "metric_displacement_frame": "body_i", "first_t_sec": 0.0, "second_t_sec": 1.0,
          "metric_displacement_camera_i_m": [1, 0, 0]}],
        {"matched_pair_count": 1},
    ))
    monkeypatch.setattr(probe.fusion, "refine_positions_visual_inertial", lambda *args, **kwargs: (state.positions, {"solver": "ok"}))
    monkeypatch.setattr(probe.fusion, "write_trajectory", lambda path, rows, pos, rot: Path(path).write_text("estimate", encoding="utf-8"))
    monkeypatch.setattr(probe.corpus, "score_frozen", lambda *args, **kwargs: {"result": "PASS", "ate_translation_max_m": 0.001})
    monkeypatch.setattr(probe.base, "code_changed", lambda hashes: False)
    monkeypatch.setattr(probe, "file_hash", lambda path: Path(path).read_text(encoding="utf-8") if Path(path).is_file() else "hash")

    result = probe.run_record(
        {"id": "case", "session": str(tmp_path / "session")},
        tmp_path / "baseline",
        tmp_path / "out",
        source_stage,
        {"code": "hash"},
    )

    assert result["status"] == "COMPLETED"
    assert set(result["variants"]) == {probe.ORIGINAL_VARIANT, probe.REFINED_VARIANT}
    assert result["reference_raw_baseline"]["role"] == "reference_only_raw_adapter_v2_not_combined_source_arm"
    refined_candidate = json.loads(
        (tmp_path / "out/case" / probe.REFINED_VARIANT / "candidate_manifest.json").read_text()
    )
    assert refined_candidate["baseline_input_sha256_preserved"] == baseline_candidate["input_sha256"]
    assert refined_candidate["source_override_sha256"] == source_stage["refined_by_id"]["case"]["source_override_sha256"]
    original_candidate = json.loads(
        (tmp_path / "out/case" / probe.ORIGINAL_VARIANT / "candidate_manifest.json").read_text()
    )
    assert original_candidate["source_override_sha256"] == {}
    assert original_candidate["learned_factor_context"]["source"] == "raw_adapter_v2_local_motion_factors"
    assert refined_candidate["learned_factor_context"] == original_candidate["learned_factor_context"]


def test_main_refuses_existing_output(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(SystemExit):
        probe.main([
            "--manifest", str(tmp_path / "missing_manifest.json"),
            "--baseline", str(tmp_path / "missing_baseline"),
            "--source-stage", str(tmp_path / "missing_stage"),
            "--output", str(output),
        ])
