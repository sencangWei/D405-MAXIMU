import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import scripts.run_timeline_gap_fusion_trial as trial


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _record(tmp_path):
    session = tmp_path / "session"
    vins = tmp_path / "vins"
    (session / "external_imu").mkdir(parents=True)
    (session / "d405_frames.csv").write_text("infrared_left_device_ms\n100000.0\n100033.0\n100066.0\n100099.0\n")
    (session / "external_imu/imu.bin").write_bytes(b"imu")
    vins.mkdir()
    (vins / "vio_corrected_stream.csv").write_text("t_sec,x,y,z,qw,qx,qy,qz\n100.0,0,0,0,1,0,0,0\n")
    (vins / "run_acceptance.json").write_text("{}\n")
    return {"id": "record", "session": str(session), "vins_dir": str(vins), "capture_dir": str(tmp_path), "reference_manifest": str(tmp_path / "ref.json")}


def _gap_report(tmp_path, db3):
    path = tmp_path / "gap.json"
    report = {
        "schema": trial.gap_probe.SCHEMA,
        "status": trial.gap_probe.READY_STATUS,
        "id": "record",
        "session": str(tmp_path / "session"),
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "tracker_reference_used": False,
        "backend_launched": False,
        "scorer_launched": False,
        "gpu_model_used": False,
        "production_promoted": False,
        "not_rejected_row_recovery": True,
        "image_source_lineage": {"selected_frames_loaded_from_db3_directly": True, "db3": str(db3)},
        "consumed_source_guard": {
            "guarded_before_sha256": {"db3": {"path": str(db3), "sha256": trial.file_hash(db3)}},
            "guarded_after_sha256": {"db3": {"path": str(db3), "sha256": trial.file_hash(db3)}},
            "guarded_after_verified": True,
        },
        "pair_policy": {"selection": "uniform_fixed_span_over_full_d405_timeline_tail", "span_frames": 1, "both_raw_frontend_end_t_sec": 99.0},
        "camera_pose_conversion": {"body_T_left_ir": np.eye(4).tolist(), "body_T_right_ir": np.eye(4).tolist()},
        "observations": [],
    }
    _write(path, report)
    return path


def test_load_gap_report_rejects_hash_tamper_and_requires_db3_lineage(tmp_path):
    db3 = tmp_path / "source.db3"
    db3.write_bytes(b"before")
    record = _record(tmp_path)
    gap = _gap_report(tmp_path, db3)
    candidate = {
        "eye_reports": {
            "left": {"effective_body_T_camera": np.eye(4).tolist(), "factory_stereo_calibration": {}},
            "right": {"effective_body_T_camera": np.eye(4).tolist(), "factory_stereo_calibration": {}},
        }
    }

    report = trial.load_gap_report(gap, record, candidate)
    assert report["id"] == "record"

    db3.write_bytes(b"after")
    with pytest.raises(ValueError, match="hash changed"):
        trial.load_gap_report(gap, record, candidate)

    db3.write_bytes(b"before")
    doc = json.loads(gap.read_text())
    doc["image_source_lineage"]["selected_frames_loaded_from_db3_directly"] = False
    _write(gap, doc)
    with pytest.raises(ValueError, match="DB3-direct"):
        trial.load_gap_report(gap, record, candidate)


def test_load_gap_report_requires_exact_before_after_guard_labels_paths_hashes(tmp_path):
    db3 = tmp_path / "source.db3"
    db3.write_bytes(b"before")
    record = _record(tmp_path)
    gap = _gap_report(tmp_path, db3)
    candidate = {
        "eye_reports": {
            "left": {"effective_body_T_camera": np.eye(4).tolist(), "factory_stereo_calibration": {}},
            "right": {"effective_body_T_camera": np.eye(4).tolist(), "factory_stereo_calibration": {}},
        }
    }

    doc = json.loads(gap.read_text())
    doc["consumed_source_guard"]["guarded_after_sha256"] = {}
    _write(gap, doc)
    with pytest.raises(ValueError, match="labels mismatch"):
        trial.load_gap_report(gap, record, candidate)

    doc = json.loads(_gap_report(tmp_path, db3).read_text())
    other = tmp_path / "other.db3"
    other.write_bytes(b"before")
    doc["consumed_source_guard"]["guarded_after_sha256"]["db3"]["path"] = str(other)
    _write(gap, doc)
    with pytest.raises(ValueError, match="path mismatch"):
        trial.load_gap_report(gap, record, candidate)


def test_main_refuses_existing_output(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(SystemExit):
        trial.main([
            "--manifest", str(tmp_path / "manifest.json"),
            "--baseline", str(tmp_path / "baseline"),
            "--constant-gauge", str(tmp_path / "constant"),
            "--combined-reference", str(tmp_path / "combined"),
            "--source-stage", str(tmp_path / "source"),
            "--gap-report", str(tmp_path / "gap.json"),
            "--output", str(output),
            "--dataset", "record",
        ])


def test_run_record_orchestrates_two_arms_without_gt_or_replay(monkeypatch, tmp_path):
    record = _record(tmp_path)
    db3 = tmp_path / "source.db3"
    db3.write_bytes(b"db3")
    gap = _gap_report(tmp_path, db3)
    output = tmp_path / "out"
    baseline = tmp_path / "baseline"
    constant = tmp_path / "constant"
    combined = tmp_path / "combined"
    (tmp_path / "source.json").write_text("{}\n")
    artifact = baseline / "record" / "both"
    artifact.mkdir(parents=True)
    for name in ("candidate_manifest.json", "graph_report.json", "shared_stereo_observations.json", "local_motion_factors.json", "body_trajectory_fused.csv"):
        (artifact / name).write_text("{}\n")
    const_artifact = constant / "record" / "selected"
    const_artifact.mkdir(parents=True)
    for name in ("candidate_manifest.json", "graph_report.json", "body_trajectory_fused.csv"):
        (const_artifact / name).write_text("{}\n")
    (const_artifact / "local_motion_factors.json").write_text("[{\"factor\": 1}]\n")
    comb_artifact = combined / "record" / trial.physical.COMBINED_VARIANT
    comb_artifact.mkdir(parents=True)
    for name in ("candidate_manifest.json", "graph_report.json", "shared_stereo_observations.json", "local_motion_factors.json", "body_trajectory_fused.csv"):
        (comb_artifact / name).write_text("{}\n")

    candidate = {
        "eye_reports": {
            "left": {"effective_body_T_camera": np.eye(4).tolist(), "factory_stereo_calibration": {}},
            "right": {"effective_body_T_camera": np.eye(4).tolist(), "factory_stereo_calibration": {}},
        }
    }
    state = SimpleNamespace(
        times=np.asarray([100.0, 100.033]),
        rotations=Rotation.identity(2),
    )
    calls = []
    gap_build_calls = []

    monkeypatch.setattr(trial.corpus_eval, "validate_source_stage_record", lambda rid, stage: {"id": rid, "left_source_paths": [], "right_source_paths": []})
    monkeypatch.setattr(trial.base, "validate_record_sources", lambda rec: None)
    monkeypatch.setattr(trial.base, "validate_baseline_artifact", lambda rec, art: (candidate, {}))
    monkeypatch.setattr(trial.base, "load_bound_reference", lambda rec: state)
    monkeypatch.setattr(trial.base, "validate_baseline_trajectory_identity", lambda art, state, graph: None)
    def fake_read_json(path):
        if Path(path).resolve() == gap.resolve():
            return json.loads(gap.read_text())
        if str(path).endswith("shared_stereo_observations.json"):
            return []
        if str(path).endswith("local_motion_factors.json"):
            return [{"factor": 1}]
        return {}

    monkeypatch.setattr(trial, "read_json", fake_read_json)
    monkeypatch.setattr(trial, "build_native_recovery_rows", lambda *args, **kwargs: ([{"first_index": 0, "second_index": 1, "first_t_sec": 100.0, "second_t_sec": 100.033, "accepted": True, "metric_displacement_frame": "body_i", "metric_displacement_camera_i_m": [0, 0, 0], "pnp_inlier_ratio": 0.5}], [{"eye": "left"}], {"native": True}, [gap]))
    def fake_gap_build(*args, **kwargs):
        gap_build_calls.append(kwargs)
        return ([{"gap": True}], [{"eye": "left"}, {"eye": "right"}], {"appended_pair_count": 1, "external_ground_truth_used": False})

    monkeypatch.setattr(trial, "build_timeline_gap_shared_rows", fake_gap_build)
    monkeypatch.setattr(trial, "load_full_d405_times", lambda path: np.asarray([100.0, 100.033]))
    monkeypatch.setattr(trial.physical, "validate_constant_artifact", lambda rec, root: (const_artifact, {}, {}, [const_artifact / "local_motion_factors.json"]))
    monkeypatch.setattr(trial.paired, "validate_combined_reference", lambda rec, base_art, root: (comb_artifact, {"schema": "umi_physical_stereo_lever_candidate_v1"}, [comb_artifact / "candidate_manifest.json"]))

    def fake_solver(record, baseline_artifact, variant, variant_dir, state, baseline_candidate, rows, stereo_report, raw_paths, motion_factors, motion_source, extra_paths, frozen_hashes):
        calls.append({"variant": variant, "rows": rows, "report": stereo_report, "raw_paths": [str(path) for path in raw_paths]})
        variant_dir.mkdir(parents=True, exist_ok=True)
        return {"artifact_dir": str(variant_dir), "score": {"result": "PASS", "ate_translation_max_m": 0.001}}

    monkeypatch.setattr(trial.physical, "run_solver_variant", fake_solver)
    result = trial.run_record(
        record,
        baseline=baseline,
        constant_gauge=constant,
        combined_reference=combined,
        source_stage={"path": str(tmp_path / "source.json")},
        gap_report_path=gap,
        output=output,
        frozen_hashes={},
        gap_support_policy="nonoverlap",
    )

    assert result["status"] == "COMPLETED"
    assert gap_build_calls == [{"support_policy": "nonoverlap"}]
    assert [call["variant"] for call in calls] == [trial.CONTROL_VARIANT, trial.GAP_VARIANT]
    assert calls[0]["rows"][0]["metric_displacement_frame"] == "body_i"
    assert calls[1]["rows"] == [{"gap": True}]
    assert calls[1]["report"]["gap_support_policy"] == "nonoverlap"
    assert calls[1]["report"]["timeline_gap_candidates"]["appended_pair_count"] == 1
    assert any(str(gap) == path for call in calls for path in call["raw_paths"])
    assert result["reference_current_best"]["role"] == "frozen_currentbest_comparator_not_replayed_control"


def test_run_record_detects_gap_db3_mutation_inside_solver(monkeypatch, tmp_path):
    record = _record(tmp_path)
    db3 = tmp_path / "source.db3"
    db3.write_bytes(b"db3")
    gap = _gap_report(tmp_path, db3)
    output = tmp_path / "out"
    baseline = tmp_path / "baseline"
    constant = tmp_path / "constant"
    combined = tmp_path / "combined"
    (tmp_path / "source.json").write_text("{}\n")
    artifact = baseline / "record" / "both"
    artifact.mkdir(parents=True)
    for name in ("candidate_manifest.json", "graph_report.json", "shared_stereo_observations.json", "local_motion_factors.json", "body_trajectory_fused.csv"):
        (artifact / name).write_text("{}\n")
    const_artifact = constant / "record" / "selected"
    const_artifact.mkdir(parents=True)
    for name in ("candidate_manifest.json", "graph_report.json", "body_trajectory_fused.csv"):
        (const_artifact / name).write_text("{}\n")
    (const_artifact / "local_motion_factors.json").write_text("[{\"factor\": 1}]\n")
    comb_artifact = combined / "record" / trial.physical.COMBINED_VARIANT
    comb_artifact.mkdir(parents=True)
    for name in ("candidate_manifest.json", "graph_report.json", "shared_stereo_observations.json", "local_motion_factors.json", "body_trajectory_fused.csv"):
        (comb_artifact / name).write_text("{}\n")
    candidate = {
        "eye_reports": {
            "left": {"effective_body_T_camera": np.eye(4).tolist(), "factory_stereo_calibration": {}},
            "right": {"effective_body_T_camera": np.eye(4).tolist(), "factory_stereo_calibration": {}},
        }
    }
    state = SimpleNamespace(times=np.asarray([100.0, 100.033]), rotations=Rotation.identity(2))

    monkeypatch.setattr(trial.corpus_eval, "validate_source_stage_record", lambda rid, stage: {"id": rid, "left_source_paths": [], "right_source_paths": []})
    monkeypatch.setattr(trial.base, "validate_record_sources", lambda rec: None)
    monkeypatch.setattr(trial.base, "validate_baseline_artifact", lambda rec, art: (candidate, {}))
    monkeypatch.setattr(trial.base, "load_bound_reference", lambda rec: state)
    monkeypatch.setattr(trial.base, "validate_baseline_trajectory_identity", lambda art, state, graph: None)
    monkeypatch.setattr(trial, "read_json", lambda path: json.loads(gap.read_text()) if Path(path).resolve() == gap.resolve() else [] if str(path).endswith("shared_stereo_observations.json") else [{"factor": 1}] if str(path).endswith("local_motion_factors.json") else {})
    monkeypatch.setattr(trial, "build_native_recovery_rows", lambda *args, **kwargs: ([{"accepted": True, "first_index": 0, "second_index": 1, "first_t_sec": 100.0, "second_t_sec": 100.033, "metric_displacement_frame": "body_i", "metric_displacement_camera_i_m": [0, 0, 0], "pnp_inlier_ratio": 0.5}], [{"eye": "left"}], {"native": True}, [gap]))
    monkeypatch.setattr(trial, "build_timeline_gap_shared_rows", lambda *args, **kwargs: ([{"gap": True}], [{"eye": "left"}], {"appended_pair_count": 1, "support_sampling": {"support_policy": kwargs.get("support_policy")}}))
    monkeypatch.setattr(trial, "load_full_d405_times", lambda path: np.asarray([100.0, 100.033]))
    monkeypatch.setattr(trial.physical, "validate_constant_artifact", lambda rec, root: (const_artifact, {}, {}, [const_artifact / "local_motion_factors.json"]))
    monkeypatch.setattr(trial.paired, "validate_combined_reference", lambda rec, base_art, root: (comb_artifact, {"schema": "umi_physical_stereo_lever_candidate_v1"}, [comb_artifact / "candidate_manifest.json"]))

    def mutating_solver(*args, **kwargs):
        db3.write_bytes(b"mutated")
        return {"artifact_dir": "x", "score": {"result": "PASS", "ate_translation_max_m": 0.001}}

    monkeypatch.setattr(trial.physical, "run_solver_variant", mutating_solver)
    with pytest.raises(ValueError, match="consumed source changed"):
        trial.run_record(
            record,
            baseline=baseline,
            constant_gauge=constant,
            combined_reference=combined,
            source_stage={"path": str(tmp_path / "source.json")},
            gap_report_path=gap,
            output=output,
            frozen_hashes={},
        )


def test_main_records_terminal_failure_for_generic_exception(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    baseline = tmp_path / "baseline"
    source = tmp_path / "source"
    gap = tmp_path / "gap.json"
    output = tmp_path / "out"
    baseline.mkdir()
    source.mkdir()
    manifest.write_text('{"records":[{"id":"record"}]}\n')
    (baseline / "summary.json").write_text('{"results":[{"id":"record","status":"COMPLETED"}]}\n')
    (source / "preflight_report.json").write_text("{}\n")
    gap.write_text("{}\n")

    monkeypatch.setattr(trial.base, "validate_records", lambda manifest, datasets: [{"id": "record"}])
    monkeypatch.setattr(trial.base, "baseline_results", lambda summary: {"record": {"id": "record", "status": "COMPLETED"}})
    monkeypatch.setattr(trial.corpus_eval, "load_source_stage", lambda path: {"sha256": "stage", "path": str(path / "preflight_report.json")})
    monkeypatch.setattr(trial, "frozen_code_paths", lambda source_stage, gap_report: [manifest])
    monkeypatch.setattr(trial.base, "snapshot_hashes", lambda paths: {str(path): "hash" for path in paths})
    monkeypatch.setattr(trial.base, "code_changed", lambda hashes: False)
    run_calls = []

    def boom(*args, **kwargs):
        run_calls.append(kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(trial, "run_record", boom)

    assert trial.main([
        "--manifest", str(manifest),
        "--baseline", str(baseline),
        "--constant-gauge", str(tmp_path / "constant"),
        "--combined-reference", str(tmp_path / "combined"),
        "--source-stage", str(source),
        "--gap-report", str(gap),
        "--output", str(output),
        "--dataset", "record",
        "--gap-support-policy", "nonoverlap",
    ]) == 3
    summary = json.loads((output / "summary.json").read_text())
    assert summary["gap_support_policy"] == "nonoverlap"
    assert summary["status"] == "COMPLETED_WITH_FAILURES"
    assert run_calls[0]["gap_support_policy"] == "nonoverlap"
    assert summary["results"][0]["status"] == "FAILED_PRECHECK_OR_SOLVER_EXCEPTION"
    assert summary["results"][0]["denominator_retained"] is True
    assert "RuntimeError: boom" == summary["results"][0]["error"]
