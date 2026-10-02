import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("dual_corpus", ROOT / "scripts/run_dual_ir_regression_corpus.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_global_projection_caps_correction_not_unscaled_mono_distance():
    reference = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)
    uncapped = reference + [[0, 0, 0], [0.092, 0, 0], [0.046, 0, 0]]
    projected, report = runner.project_global_cap(reference, uncapped, 0.025)
    np.testing.assert_allclose(projected - reference, [[0, 0, 0], [0.025, 0, 0], [0.0125, 0, 0]])
    assert report["requested_max_m"] == pytest.approx(0.092)
    assert report["changed_frames"] == 2
    unaffected, report = runner.project_global_cap(reference, uncapped, 0.1)
    np.testing.assert_allclose(unaffected, uncapped)
    assert report["changed_frames"] == 0


def test_graph_command_has_no_external_reference_input():
    record = {"session": "/umi", "vins_dir": "/vins", "capture_dir": "/tracker", "reference_manifest": "/gt.json"}
    command = runner.graph_command(record, Path("/left"), Path("/right"), Path("/new"), "both")
    assert "/tracker" not in command and "/gt.json" not in command
    assert command[command.index("--max-correction-mm") + 1] == "none"
    assert "--eyes" in runner.graph_command(record, Path("/left"), Path("/right"), Path("/new"), "right")


def test_optional_window_policy_is_explicit_and_not_selected_by_reference():
    record = {"session": "/umi", "vins_dir": "/vins", "capture_dir": "/tracker", "reference_manifest": "/gt.json"}
    command = runner.graph_command(
        record, Path("/left"), Path("/right"), Path("/new"), "both",
        optional_stereo_policy="reject_window",
    )
    assert command[command.index("--optional-stereo-policy") + 1] == "reject_window"
    assert "/tracker" not in command and "/gt.json" not in command
    default = runner.graph_command(record, Path("/left"), Path("/right"), Path("/new"), "both")
    assert "--optional-stereo-policy" not in default


def test_primary_unobservable_is_explicit_not_missing_optional_file(tmp_path):
    source = tmp_path / "left"
    source.mkdir()
    (source / "stereo_scale_bidirectional_report.json").write_text(json.dumps({
        "schema": "umi_mast3r_stereo_scale_v2", "result": "FAIL",
        "slam_supervision": False, "external_ground_truth_used": False,
        "failures": ["stereo_scale_unobservable"], "observations": [],
    }))
    (tmp_path / "work").mkdir()
    with pytest.raises(ValueError, match="primary_stereo_scale_unobservable"):
        runner.ensure_left({"left_dir": str(source)}, tmp_path / "work")


def test_right_cache_reuse_cannot_mutate_frozen_raw_frontend(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    raw = cache / "trajectory_frames.csv"
    manifest = cache / "run_manifest.json"
    raw.write_text("raw tracked poses")
    manifest.write_text("{}")
    record = {"right_cache_reuse_audit": {
        "cache": str(cache), "raw_trajectory_sha256": runner.file_hash(raw),
        "run_manifest_sha256": runner.file_hash(manifest),
        "ground_truth_used_for_selection": False,
    }}
    runner.validate_right_cache_reuse_binding(record, cache)
    raw.write_text("silently interpolated substitute")
    with pytest.raises(ValueError, match="reuse source hash"):
        runner.validate_right_cache_reuse_binding(record, cache)


def test_adapter_manifest_preserves_all_source_identities_and_failures():
    repaired = runner.read_json(ROOT / "config/dual_ir_regression_25_20261002_adapters.json")
    source = runner.read_json(Path(repaired["source_baseline"]["manifest"]))
    baseline = runner.read_json(Path(repaired["source_baseline"]["summary"]))
    assert len(repaired["records"]) == 25
    original = {record["id"]: record for record in source["records"]}
    assert {record["id"] for record in repaired["records"]} == set(original)
    for record in repaired["records"]:
        for key in ("session", "capture_dir", "left_dir", "vins_dir", "reference_manifest"):
            assert record[key] == original[record["id"]][key]
        if record.get("right_cache_reuse_audit"):
            assert record["right_cache_reuse_audit"]["ground_truth_used_for_selection"] is False
    failures = {row["id"] for row in baseline["results"] if row["status"] != "COMPLETED"}
    assert failures <= {record["id"] for record in repaired["records"]}


def test_corpus_rejects_duplicate_recordings_before_processing():
    record = {"id": "a", "session": "/umi", "capture_dir": "/capture"}
    with pytest.raises(ValueError, match="duplicate"):
        runner.validate_corpus({"records": [record, record]})


def test_no_motion_pass_capture_is_not_dynamic_dataset(tmp_path):
    capture = tmp_path / "capture"
    capture.mkdir()
    (capture / "capture_manifest.json").write_text(json.dumps({"status": "PASS_CAPTURE_ONLY_NOT_CALIBRATED", "d405_session": str(tmp_path)}))
    (capture / "EXCLUDED_FROM_DYNAMIC_VALIDATION.md").write_text("operator did not move")
    with pytest.raises(ValueError, match="no-motion"):
        runner.validate_corpus({"records": [{"id": "a", "session": str(tmp_path), "capture_dir": str(capture)}]})


def test_failed_source_retained_in_denominator_and_original_not_written(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    marker = source / "original.csv"
    marker.write_text("unchanged")
    monkeypatch.setattr(runner, "ensure_left", lambda *args: (_ for _ in ()).throw(ValueError("scale_unobservable")))
    result = runner.process_record({"id": "bad", "left_dir": str(source)}, tmp_path / "work", ["both"])
    assert result["status"] == "PREPARATION_OR_INPUT_FAILED"
    assert "scale_unobservable" in result["error"]
    assert marker.read_text() == "unchanged"
    assert (tmp_path / "work/progress.json").is_file()


def test_eye_cache_requires_all_four_reports_not_just_raw_trajectory(tmp_path):
    (tmp_path / "imu_metric_trajectory.csv").write_text("fixture")
    (tmp_path / "imu_scale_report.json").write_text("{}")
    assert not runner.complete_eye(tmp_path, "right")
    assert not runner.complete_eye(None, "right")


def test_reused_right_cache_rejects_stale_model_and_foreign_session(tmp_path, monkeypatch):
    config, checkpoint = tmp_path / "config.yaml", tmp_path / "weights.pth"
    config.write_text("frozen config")
    checkpoint.write_text("official weights")
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("timestamps")
    monkeypatch.setattr(runner, "OFFLINE_CONFIG", config)
    monkeypatch.setattr(runner, "CHECKPOINT", checkpoint)
    monkeypatch.setattr(runner, "FROZEN_CONFIG_SHA256", runner.file_hash(config))
    monkeypatch.setattr(runner, "OFFICIAL_CHECKPOINT_SHA256", runner.file_hash(checkpoint))
    cache = tmp_path / "cache"
    (cache / "dataset").mkdir(parents=True)
    run = {"schema": "umi_mast3r_run_v1", "slam_supervision": False,
           "config": str(config), "config_sha256": runner.file_hash(config),
           "checkpoint": str(checkpoint), "checkpoint_sha256": runner.file_hash(checkpoint)}
    dataset = {"stream": "infrared_right", "slam_supervision": False,
               "source_session": str(session), "source_frames_csv_sha256": runner.file_hash(session / "d405_frames.csv"),
               "image_preprocessing": {"crop_bottom_px": 0, "mask_fixed_self_occlusion": False}}
    (cache / "run_manifest.json").write_text(json.dumps(run))
    (cache / "dataset/dataset_manifest.json").write_text(json.dumps(dataset))
    runner.validate_reused_right_cache({"session": str(session)}, cache)
    runner.validate_reused_right_cache({"session": str(session), "right_input_sha256": None}, cache)
    run["checkpoint_sha256"] = "stale"
    (cache / "run_manifest.json").write_text(json.dumps(run))
    with pytest.raises(ValueError, match="checkpoint hash"):
        runner.validate_reused_right_cache({"session": str(session)}, cache)
    run["checkpoint_sha256"] = runner.file_hash(checkpoint)
    (cache / "run_manifest.json").write_text(json.dumps(run))
    with pytest.raises(ValueError, match="session mismatch"):
        runner.validate_reused_right_cache({"session": str(tmp_path / "foreign")}, cache)


def test_scored_accuracy_failure_is_not_infrastructure_failure(tmp_path, monkeypatch):
    score_dir = tmp_path / "score"
    score_dir.mkdir()
    (score_dir / "workflow_manifest.json").write_text(json.dumps({"result": "SCORING_COMPLETED", "estimate_unchanged": True}))
    keys = ("result", "failures", "samples", "ate_translation_mean_m", "ate_translation_p95_m",
            "ate_translation_max_m", "ate_translation_rmse_m", "ate_translation_within_10mm_ratio", "timestamp_overlap_ratio")
    score = dict.fromkeys(keys, 0)
    score["result"] = "FAIL"
    (score_dir / "precision.json").write_text(json.dumps(score))
    calls = []
    monkeypatch.setattr(runner, "_run_command", lambda *args, **kwargs: calls.append(kwargs))
    result = runner.score_frozen({"capture_dir": "/capture", "reference_manifest": "/gt.json"}, "/trajectory", score_dir, tmp_path, "score")
    assert result["result"] == "FAIL"
    assert calls[0]["allowed_returncodes"] == (0, 3)
    (score_dir / "workflow_manifest.json").write_text(json.dumps({"result": "SCORING_COMPLETED", "estimate_unchanged": False}))
    with pytest.raises(ValueError, match="unchanged estimate"):
        runner.score_frozen({"capture_dir": "/capture", "reference_manifest": "/gt.json"}, "/trajectory", score_dir, tmp_path, "score")


def test_aggregate_does_not_drop_failed_or_unscored_records():
    results = [{"variants": {"both": {"score": {"result": "PASS", "ate_translation_max_m": 0.005}}}},
               {"variants": {"both": {"score": {"result": "FAIL", "ate_translation_max_m": 0.050}}}},
               {"status": "PREPARATION_OR_INPUT_FAILED", "variants": {}}]
    summary = runner.aggregate_results(results, 25, ["both"])["both/none"]
    assert summary["dataset_count"] == 25
    assert summary["scored_count"] == 2 and summary["unscored_count"] == 23
    assert summary["max_within_10mm_count"] == summary["precision_pass_count"] == 1
    assert summary["worst_max_m"] == 0.050
