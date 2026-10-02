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
        "left_refined_sift_lm_reports": True,
        "right_geometry_source": "original_right_reports_unchanged",
        "stale_right_derived_geometry_unchanged": True,
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
