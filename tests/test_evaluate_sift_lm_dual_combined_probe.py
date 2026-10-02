import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_sift_lm_dual_combined_probe as probe


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_refresh_shared_row_confidences_uses_current_winner_and_rejects_duplicates():
    rows = [
        {"accepted": True, "first_index": 0, "second_index": 2, "pnp_inlier_ratio": 0.4},
        {"accepted": True, "first_index": 2, "second_index": 4, "pnp_inlier_ratio": 0.8},
    ]
    candidates = [
        {"eye": "left", "reference_first_index": 0, "reference_second_index": 2,
         "observation_confidence": 0.7},
        {"eye": "right", "reference_first_index": 0, "reference_second_index": 2,
         "observation_confidence": 0.5},
        {"eye": "right", "reference_first_index": 2, "reference_second_index": 4,
         "observation_confidence": 0.8},
    ]

    refreshed, report = probe.refresh_shared_row_confidences(rows, candidates)

    assert refreshed[0]["pnp_inlier_ratio"] == pytest.approx(0.7)
    assert refreshed[1]["pnp_inlier_ratio"] == pytest.approx(0.8)
    assert report["confidence_changed_row_count"] == 1
    assert report["pair_policy"] == "frozen_original_shared_pairs"

    with pytest.raises(ValueError, match="duplicate same-eye"):
        probe.refresh_shared_row_confidences(rows, [*candidates, candidates[0]])

    with pytest.raises(ValueError, match="absent from frozen shared rows"):
        probe.refresh_shared_row_confidences(
            rows,
            [*candidates, {"eye": "left", "reference_first_index": 9,
                           "reference_second_index": 10, "observation_confidence": 0.1}],
        )


def test_postprocess_separates_baseline_hashes_override_and_learned_context(tmp_path, monkeypatch):
    variant = tmp_path / "variant"
    constant = tmp_path / "constant/record/selected"
    combined = tmp_path / "combined/record/physical_stereo_constant_gauge"
    variant.mkdir(parents=True)
    constant.mkdir(parents=True)
    combined.mkdir(parents=True)
    write_json(variant / "candidate_manifest.json", {"input_sha256": {"old": "hash"}})
    write_json(variant / "graph_report.json", {})
    (constant / "local_motion_factors.json").write_text("[{}]", encoding="utf-8")

    monkeypatch.setattr(probe, "file_hash", lambda path: f"sha:{Path(path).name}")
    baseline_candidate = {"input_sha256": {"baseline": "original"}}
    override = {str((tmp_path / "refined.json").resolve()): "overridehash"}
    replay = {"max_position_delta_m": 0.0}

    probe.postprocess_candidate_metadata(
        variant,
        baseline_candidate,
        override,
        combined,
        constant,
        "motion-sha",
        "refined_left_override_original_right",
        replay,
    )

    candidate = json.loads((variant / "candidate_manifest.json").read_text())
    graph = json.loads((variant / "graph_report.json").read_text())
    assert candidate["baseline_input_sha256_preserved"] == {"baseline": "original"}
    assert candidate["source_override_sha256"] == override
    assert candidate["source_upgrade_scope"] == {
        "partial_source_upgrade": True,
        "consistent_geometry_source_upgrade": False,
        "left_refined_sift_lm_reports": True,
        "right_refined_sift_lm_reports": False,
        "right_geometry_source": "original_right_reports_unchanged",
        "stale_right_derived_geometry_unchanged": True,
        "right_eye_used_as_independent_evidence": False,
    }
    assert candidate["learned_factor_context"]["source"] == "constant_ir_gauge_selected"
    assert candidate["learned_factor_context"]["identical_between_arms"] is True
    assert graph["current_best_context"] == candidate["current_best_context"]
    assert graph["source_upgrade_scope"] == candidate["source_upgrade_scope"]


def test_validate_combined_replay_rejects_nonfinite_and_rotation_mismatch(monkeypatch, tmp_path):
    def fake_load_nonfinite(path):
        return (
            np.asarray([0.0]),
            np.asarray([[np.nan, 0.0, 0.0]]),
            Rotation.identity(1),
            [{}],
        )

    monkeypatch.setattr(probe.fusion, "load_trajectory", fake_load_nonfinite)
    with pytest.raises(ValueError, match="non-finite"):
        probe.validate_combined_replay(tmp_path / "control.csv", tmp_path / "combined.csv")

    def fake_load_rotation(path):
        rotation = Rotation.identity(1) if "control" in str(path) else Rotation.from_euler("z", [1e-6])
        return np.asarray([0.0]), np.asarray([[0.0, 0.0, 0.0]]), rotation, [{}]

    monkeypatch.setattr(probe.fusion, "load_trajectory", fake_load_rotation)
    with pytest.raises(ValueError, match="orientation"):
        probe.validate_combined_replay(tmp_path / "control.csv", tmp_path / "combined.csv")


def test_consumed_hash_guard_rejects_midrun_mutation(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("before", encoding="utf-8")
    before = probe.snapshot_paths([source])

    source.write_text("after", encoding="utf-8")

    with pytest.raises(ValueError, match="consumed source changed"):
        probe.assert_hashes_unchanged(before)


def test_override_right_loader_validates_hash_frame_and_dedup(monkeypatch, tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    report_paths = [tmp_path / f"right_{index}.json" for index in range(4)]
    for index, path in enumerate(report_paths):
        path.write_text(f"right-{index}", encoding="utf-8")
    trajectory = tmp_path / "imu_metric_trajectory.csv"
    trajectory.write_text("trajectory", encoding="utf-8")
    baseline_candidate = {"eye_reports": {"right": {}}}
    overrides = {str(path.resolve()): path.read_text(encoding="utf-8") for path in report_paths}
    frame = {"value": "infrared_right_camera_i"}
    observations = {"value": [{"accepted": True}]}

    monkeypatch.setattr(probe.physical, "eye_trajectory_path_from_baseline", lambda candidate, eye: trajectory)
    monkeypatch.setattr(probe.source_eval, "validate_candidate_hashes", lambda candidate, paths: {"trajectory": "hash"})
    monkeypatch.setattr(probe, "file_hash", lambda path: Path(path).read_text(encoding="utf-8"))
    monkeypatch.setattr(
        probe.fusion,
        "load_trajectory",
        lambda path: (np.asarray([0.0, 0.1]), np.zeros((2, 3)), Rotation.identity(2), []),
    )
    monkeypatch.setattr(probe.fusion, "validate_onboard_report", lambda report, path, schema: None)
    monkeypatch.setattr(
        probe.fusion,
        "load_json_report",
        lambda path: {
            "session": str(session.resolve()),
            "observation_frame": frame["value"],
        },
    )
    monkeypatch.setattr(
        probe.fusion,
        "merge_stereo_reports",
        lambda primary, optional, optional_policy: {
            "factory_stereo_calibration": {"baseline_m": 0.018},
            "scale_m_per_mast3r_unit": 1.0,
            "observations": observations["value"],
            "merged_report_paths": [str(path.resolve()) for path in report_paths],
            "merged_report_count": 4,
        },
    )
    monkeypatch.setattr(
        probe.physical,
        "validate_eye_metadata",
        lambda candidate, eye: {
            "factory_stereo_calibration": {"baseline_m": 0.018},
            "effective_body_T_camera": np.eye(4).tolist(),
        },
    )
    monkeypatch.setattr(probe.fusion, "stereo_observation_confidence", lambda observation, scale: 0.7)
    monkeypatch.setattr(
        probe.physical,
        "reference_bound_eye_candidate",
        lambda reference_times, trajectory_times, eye, observation, confidence, body_t_camera: (
            {
                "eye": eye,
                "reference_first_index": 0,
                "reference_second_index": 1,
                "observation_confidence": confidence,
            },
            None,
        ),
    )

    candidates, report, paths = probe.load_override_eye_candidates_from_reports(
        {"id": "case", "session": str(session)},
        "right",
        baseline_candidate,
        np.asarray([0.0, 0.1]),
        report_paths,
        refined_override_sha256=overrides,
    )
    assert candidates == [{
        "eye": "right",
        "reference_first_index": 0,
        "reference_second_index": 1,
        "observation_confidence": 0.7,
    }]
    assert report["measurement_frame"] == "body_i"
    assert report["right_eye_used_as_independent_evidence"] is False
    assert paths == [trajectory, *report_paths]

    bad_overrides = dict(overrides)
    bad_overrides[str(report_paths[0].resolve())] = "wrong"
    with pytest.raises(ValueError, match="source override hash changed"):
        probe.load_override_eye_candidates_from_reports(
            {"id": "case", "session": str(session)},
            "right",
            baseline_candidate,
            np.asarray([0.0, 0.1]),
            report_paths,
            refined_override_sha256=bad_overrides,
        )

    frame["value"] = "infrared_left_camera_i"
    with pytest.raises(ValueError, match="frame mismatch"):
        probe.load_override_eye_candidates_from_reports(
            {"id": "case", "session": str(session)},
            "right",
            baseline_candidate,
            np.asarray([0.0, 0.1]),
            report_paths,
            refined_override_sha256=overrides,
        )
    frame["value"] = "infrared_right_camera_i"

    observations["value"] = [{"accepted": True}, {"accepted": True}]
    with pytest.raises(ValueError, match="duplicate same-eye"):
        probe.load_override_eye_candidates_from_reports(
            {"id": "case", "session": str(session)},
            "right",
            baseline_candidate,
            np.asarray([0.0, 0.1]),
            report_paths,
            refined_override_sha256=overrides,
        )


def test_refined_loader_accepts_explicit_consistent_right_source(monkeypatch, tmp_path):
    left_paths = [tmp_path / f"left_{index}.json" for index in range(4)]
    right_paths = [tmp_path / f"right_{index}.json" for index in range(4)]
    for path in [*left_paths, *right_paths]:
        path.write_text(path.name, encoding="utf-8")
    overrides = {str(path.resolve()): path.read_text(encoding="utf-8") for path in [*left_paths, *right_paths]}
    stage_record = {
        "refined_left_sources": [str(path.resolve()) for path in left_paths],
        "refined_right_sources": [str(path.resolve()) for path in right_paths],
        "source_override_sha256": overrides,
        "right_derivation_left_source_sha256": {
            str(right_path.resolve()): {
                "left_source_path": str(left_paths[index].resolve()),
                "left_source_sha256": left_paths[index].read_text(encoding="utf-8"),
            }
            for index, right_path in enumerate(right_paths)
        },
    }
    left_candidates = [{"eye": "left", "reference_first_index": 0, "reference_second_index": 1,
                        "observation_confidence": 0.4}]
    right_candidates = [{"eye": "right", "reference_first_index": 0, "reference_second_index": 1,
                         "observation_confidence": 0.8}]

    monkeypatch.setattr(
        probe.source_eval,
        "load_left_candidates_from_reports",
        lambda record, baseline_candidate, reference_times, report_paths, refined_override_sha256:
            (left_candidates, {"eye": "left"}, list(report_paths)),
    )
    monkeypatch.setattr(
        probe,
        "load_override_eye_candidates_from_reports",
        lambda record, eye, baseline_candidate, reference_times, report_paths, refined_override_sha256:
            (right_candidates, {"eye": eye}, list(report_paths)),
    )
    monkeypatch.setattr(
        probe.physical,
        "load_eye_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("original RIGHT must not load")),
    )

    candidates, report, paths, source_hashes = probe.load_refined_all_eye_candidates(
        {"id": "case", "session": str(tmp_path / "session")},
        {"input_sha256": {"baseline": "hash"}},
        np.asarray([0.0, 0.1]),
        stage_record,
    )

    assert candidates == [*left_candidates, *right_candidates]
    assert paths == [*left_paths, *right_paths]
    assert source_hashes == overrides
    assert report["right_eye_unchanged_original_source"] is False
    assert report["right_geometry_source"] == "refined_right_reports_consistent_with_left_source"
    assert report["right_eye_used_as_independent_evidence"] is False
    assert probe.refined_arm_role(stage_record) == "refined_left_right_consistent_source_override"
    assert probe.source_upgrade_scope(overrides, probe.refined_arm_role(stage_record)) == {
        "partial_source_upgrade": False,
        "consistent_geometry_source_upgrade": True,
        "left_refined_sift_lm_reports": True,
        "right_refined_sift_lm_reports": True,
        "right_geometry_source": "refined_right_reports_consistent_with_left_source",
        "stale_right_derived_geometry_unchanged": False,
        "right_eye_used_as_independent_evidence": False,
    }


def state():
    times = np.asarray([0.0, 0.025, 0.05])
    return SimpleNamespace(
        times=times,
        mono=times.copy(),
        positions=np.zeros((3, 3)),
        rotations=SimpleNamespace(as_matrix=lambda: np.repeat(np.eye(3)[None], 3, axis=0)),
        rows=[{"t_sec": str(t), "x": "0", "y": "0", "z": "0",
               "qw": "1", "qx": "0", "qy": "0", "qz": "0"} for t in times],
        imu_times=times.copy(),
        gyro=np.zeros((3, 3)),
        accel=np.zeros((3, 3)),
        config={"td_s": -0.009109323},
        source_quality={"ok": True},
        binding={"ok": True},
        imu_info={"ok": True},
    )


def test_run_record_keeps_constant_learned_context_and_checks_control_replay(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline/case/both"
    constant = tmp_path / "constant/case/selected"
    combined = tmp_path / "combined/case/physical_stereo_constant_gauge"
    for path in (baseline, constant, combined):
        path.mkdir(parents=True)
    for artifact in (baseline, combined):
        write_json(artifact / "candidate_manifest.json", {"schema": "candidate"})
        write_json(artifact / "graph_report.json", {})
        write_json(artifact / "local_motion_factors.json", [])
        write_json(artifact / "shared_stereo_observations.json", [
            {"accepted": True, "first_index": 0, "second_index": 2, "pnp_inlier_ratio": 0.4}
        ])
        (artifact / "body_trajectory_fused.csv").write_text("estimate", encoding="utf-8")
    write_json(constant / "candidate_manifest.json", {"schema": "umi_constant_ir_gauge_candidate_v1"})
    write_json(constant / "graph_report.json", {})
    write_json(constant / "local_motion_factors.json", [{"factor": 1}])
    (constant / "body_trajectory_fused.csv").write_text("constant-estimate", encoding="utf-8")

    baseline_candidate = {
        "policy_arguments": probe.base.BASELINE_POLICY_ARGUMENTS,
        "input_sha256": {"baseline": "hash"},
    }
    st = state()
    candidates = [
        {"eye": "left", "reference_first_index": 0, "reference_second_index": 2,
         "observation_confidence": 0.6},
        {"eye": "right", "reference_first_index": 0, "reference_second_index": 2,
         "observation_confidence": 0.4},
    ]
    source_stage = {
        "path": str((tmp_path / "stage/preflight_report.json").resolve()),
        "refined_by_id": {
            "case": {
                "refined_left_sources": [str((tmp_path / "refined.json").resolve())],
                "source_override_sha256": {str((tmp_path / "refined.json").resolve()): "override"},
            }
        },
    }
    (tmp_path / "stage").mkdir()
    (tmp_path / "stage/preflight_report.json").write_text("stage", encoding="utf-8")
    (tmp_path / "refined.json").write_text("refined", encoding="utf-8")

    monkeypatch.setattr(probe.base, "validate_record_sources", lambda record: None)
    monkeypatch.setattr(probe.base, "validate_baseline_artifact", lambda record, artifact: (baseline_candidate, {}))
    monkeypatch.setattr(probe.base, "load_bound_reference", lambda record: st)
    monkeypatch.setattr(probe.base, "validate_baseline_trajectory_identity", lambda artifact, state_arg, graph: None)
    monkeypatch.setattr(probe.physical, "load_all_eye_candidates", lambda *args: (candidates, {"eyes": "original"}, [tmp_path / "orig.json"]))
    monkeypatch.setattr(probe, "load_refined_all_eye_candidates", lambda *args: (candidates, {"eyes": "refined"}, [tmp_path / "refined.json"], {"refined": "override"}))
    monkeypatch.setattr(probe.physical, "transform_shared_rows", lambda state_arg, cands, rows: (rows, {"rows": len(rows)}))
    monkeypatch.setattr(probe.physical, "validate_constant_artifact", lambda record, root: (constant, {}, {}, [constant / "candidate_manifest.json", constant / "local_motion_factors.json"]))
    monkeypatch.setattr(probe, "validate_combined_reference", lambda record, artifact, root: (combined, {"schema": "combined"}, [combined / "candidate_manifest.json"]))
    monkeypatch.setattr(probe, "validate_combined_replay", lambda control, ref: {"max_position_delta_m": 0.0})
    monkeypatch.setattr(probe, "file_hash", lambda path: Path(path).read_text(encoding="utf-8") if Path(path).is_file() else "hash")
    monkeypatch.setattr(probe, "snapshot_paths", lambda paths: {str(Path(path)): "hash" for path in paths})
    monkeypatch.setattr(probe, "assert_hashes_unchanged", lambda before: None)
    scorer_seen = {}

    def fake_score_frozen(record, estimate, score_dir, work_dir, stage):
        manifest = json.loads((Path(work_dir) / "candidate_manifest.json").read_text())
        scorer_seen[Path(work_dir).name] = manifest
        return {"result": "PASS", "ate_translation_max_m": 0.001}

    monkeypatch.setattr(probe.physical.corpus, "score_frozen", fake_score_frozen)

    def fake_run_solver_variant(record, artifact, variant, variant_dir, state_arg,
                                baseline_candidate_arg, stereo_rows, stereo_report,
                                raw_paths, motion_factors, motion_source, extra_paths, frozen_hashes):
        variant_dir.mkdir(parents=True, exist_ok=True)
        (variant_dir / "body_trajectory_fused.csv").write_text("estimate", encoding="utf-8")
        write_json(variant_dir / "candidate_manifest.json", {"schema": "candidate"})
        write_json(variant_dir / "graph_report.json", {})
        score = probe.physical.corpus.score_frozen(record, variant_dir / "body_trajectory_fused.csv", variant_dir / "score", variant_dir, "score")
        return {"score": score}

    monkeypatch.setattr(probe.physical, "run_solver_variant", fake_run_solver_variant)

    result = probe.run_record(
        {"id": "case", "session": str(tmp_path / "session"), "vins_dir": str(tmp_path / "vins")},
        tmp_path / "baseline",
        tmp_path / "constant",
        tmp_path / "combined",
        tmp_path / "out",
        source_stage,
        {"code": "hash"},
    )

    assert result["status"] == "COMPLETED"
    original = json.loads((tmp_path / "out/case" / probe.ORIGINAL_VARIANT / "candidate_manifest.json").read_text())
    refined = json.loads((tmp_path / "out/case" / probe.REFINED_VARIANT / "candidate_manifest.json").read_text())
    assert result["variants"][probe.ORIGINAL_VARIANT]["control_replay_agreement"]["max_position_delta_m"] == 0.0
    assert original["source_override_sha256"] == {}
    assert refined["source_override_sha256"] == {"refined": "override"}
    assert original["learned_factor_context"] == refined["learned_factor_context"]
    assert scorer_seen[probe.ORIGINAL_VARIANT]["current_best_context"]["control_replay_agreement"] == {"max_position_delta_m": 0.0}
    assert scorer_seen[probe.ORIGINAL_VARIANT]["learned_factor_context"]["source"] == "constant_ir_gauge_selected"
    assert scorer_seen[probe.REFINED_VARIANT]["source_override_sha256"] == {"refined": "override"}
    assert scorer_seen[probe.REFINED_VARIANT]["source_upgrade_scope"]["partial_source_upgrade"] is True


def test_control_replay_failure_blocks_refined_arm(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline/case/both"
    constant = tmp_path / "constant/case/selected"
    combined = tmp_path / "combined/case/physical_stereo_constant_gauge"
    for path in (baseline, constant, combined):
        path.mkdir(parents=True)
    for artifact in (baseline, combined):
        write_json(artifact / "candidate_manifest.json", {"schema": "candidate"})
        write_json(artifact / "graph_report.json", {})
        write_json(artifact / "local_motion_factors.json", [])
        write_json(artifact / "shared_stereo_observations.json", [
            {"accepted": True, "first_index": 0, "second_index": 2, "pnp_inlier_ratio": 0.4}
        ])
        (artifact / "body_trajectory_fused.csv").write_text("estimate", encoding="utf-8")
    write_json(constant / "candidate_manifest.json", {})
    write_json(constant / "graph_report.json", {})
    write_json(constant / "local_motion_factors.json", [])
    (constant / "body_trajectory_fused.csv").write_text("estimate", encoding="utf-8")
    (tmp_path / "stage").mkdir()
    (tmp_path / "stage/preflight_report.json").write_text("stage", encoding="utf-8")

    candidates = [
        {"eye": "left", "reference_first_index": 0, "reference_second_index": 2,
         "observation_confidence": 0.6},
        {"eye": "right", "reference_first_index": 0, "reference_second_index": 2,
         "observation_confidence": 0.4},
    ]
    source_stage = {
        "path": str((tmp_path / "stage/preflight_report.json").resolve()),
        "refined_by_id": {
            "case": {
                "refined_left_sources": [str((tmp_path / "refined.json").resolve())],
                "source_override_sha256": {str((tmp_path / "refined.json").resolve()): "override"},
            }
        },
    }
    (tmp_path / "refined.json").write_text("refined", encoding="utf-8")
    monkeypatch.setattr(probe.base, "validate_record_sources", lambda record: None)
    monkeypatch.setattr(probe.base, "validate_baseline_artifact", lambda record, artifact: ({"input_sha256": {}, "policy_arguments": {}}, {}))
    monkeypatch.setattr(probe.base, "load_bound_reference", lambda record: state())
    monkeypatch.setattr(probe.base, "validate_baseline_trajectory_identity", lambda artifact, state_arg, graph: None)
    monkeypatch.setattr(probe.physical, "load_all_eye_candidates", lambda *args: (candidates, {}, [tmp_path / "orig.json"]))
    monkeypatch.setattr(probe, "load_refined_all_eye_candidates", lambda *args: (candidates, {}, [tmp_path / "refined.json"], {"refined": "override"}))
    monkeypatch.setattr(probe.physical, "transform_shared_rows", lambda state_arg, cands, rows: (rows, {}))
    monkeypatch.setattr(probe.physical, "validate_constant_artifact", lambda record, root: (constant, {}, {}, [constant / "candidate_manifest.json"]))
    monkeypatch.setattr(probe, "validate_combined_reference", lambda record, artifact, root: (combined, {}, [combined / "candidate_manifest.json"]))
    monkeypatch.setattr(probe, "snapshot_paths", lambda paths: {str(Path(path)): "hash" for path in paths})
    monkeypatch.setattr(probe, "assert_hashes_unchanged", lambda before: None)
    monkeypatch.setattr(probe, "validate_combined_replay", lambda control, ref: (_ for _ in ()).throw(ValueError("control mismatch")))
    monkeypatch.setattr(probe.physical.corpus, "score_frozen", lambda *args, **kwargs: {"result": "PASS"})
    calls = []

    def fake_run_solver_variant(record, artifact, variant, variant_dir, state_arg,
                                baseline_candidate_arg, stereo_rows, stereo_report,
                                raw_paths, motion_factors, motion_source, extra_paths, frozen_hashes):
        calls.append(variant)
        if variant == probe.REFINED_VARIANT:
            raise AssertionError("refined arm must not run")
        variant_dir.mkdir(parents=True, exist_ok=True)
        (variant_dir / "body_trajectory_fused.csv").write_text("estimate", encoding="utf-8")
        write_json(variant_dir / "candidate_manifest.json", {"schema": "candidate"})
        write_json(variant_dir / "graph_report.json", {})
        probe.physical.corpus.score_frozen(record, variant_dir / "body_trajectory_fused.csv", variant_dir / "score", variant_dir, "score")
        raise AssertionError("refined arm must not run")

    monkeypatch.setattr(probe.physical, "run_solver_variant", fake_run_solver_variant)
    result = probe.run_record(
        {"id": "case", "session": str(tmp_path / "session"), "vins_dir": str(tmp_path / "vins")},
        tmp_path / "baseline",
        tmp_path / "constant",
        tmp_path / "combined",
        tmp_path / "out",
        source_stage,
        {},
    )
    assert calls == [probe.ORIGINAL_VARIANT]
    assert result["variants"][probe.REFINED_VARIANT]["error_code"] == "CONTROL_REPLAY_FAILED"
