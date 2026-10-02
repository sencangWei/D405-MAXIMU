import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "config" / "dual_ir_regression_25_20261002.json"


def _path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_freezes_expected_25_unique_dynamic_sources():
    manifest = _load_manifest()
    records = manifest["records"]

    assert manifest["schema"] == "umi_dual_ir_regression_corpus_v1"
    assert len(records) == 25
    assert len({record["id"] for record in records}) == 25
    assert len({record["session"] for record in records}) == 25
    assert manifest["summary"]["record_count"] == 25
    assert manifest["summary"]["unique_session_count"] == 25

    excluded = manifest["excluded_static_or_no_motion"]
    assert excluded == [
        {
            "capture_dir": "reports/steamvr_umi_sessions/20260927_150314_steamvr_fusion_heldout_take3",
            "reason": "no-motion/static take; replaced by moving retry 20260927_150519",
        }
    ]
    assert all("150314" not in record["capture_dir"] for record in records)


def test_manifest_counts_distinguish_source_status_from_slam_scores():
    manifest = _load_manifest()
    records = manifest["records"]

    assert {record["capture_status"] for record in records} == {
        "PASS_CAPTURE_ONLY_NOT_CALIBRATED"
    }
    assert manifest["status_semantics"]["capture_status"].startswith(
        "source/capture-only"
    )
    assert manifest["summary"]["capture_status_counts"] == {
        "PASS_CAPTURE_ONLY_NOT_CALIBRATED": 25
    }
    assert manifest["summary"]["vins_cache_count"] == 25
    assert manifest["summary"]["left_complete_count"] == 20
    assert manifest["summary"]["left_partial_count"] == 5
    assert manifest["summary"]["right_complete_count"] == 3
    assert manifest["summary"]["right_missing_count"] == 22
    assert manifest["summary"]["product_fusion_score_count"] == 15
    assert manifest["summary"]["scoring_reference_count"] == 25

    scored = [record for record in records if record["product_fusion_score_dir"]]
    assert len(scored) == 15
    assert {record["product_fusion_score_result"] for record in scored} == {
        "PASS",
        "FAIL",
    }
    assert all(record["product_fusion_score_result"] is None for record in records if not record["product_fusion_score_dir"])


def test_all_declared_source_and_left_vins_paths_exist():
    for record in _load_manifest()["records"]:
        capture_dir = _path(record["capture_dir"])
        raw_session = _path(record["session"])
        processing_dir = _path(record["audit"]["processing_dir"])
        vins_dir = _path(record["vins_dir"])
        left_dir = _path(record["left_dir"])
        scoring_dir = _path(record["scoring_reference_dir"])
        reference_manifest = _path(record["reference_manifest"])

        assert capture_dir.is_dir(), record["id"]
        assert (capture_dir / "capture_manifest.json").is_file(), record["id"]
        assert (capture_dir / "d405_session.txt").is_file(), record["id"]
        assert raw_session.is_dir(), record["id"]
        assert processing_dir.is_dir(), record["id"]
        assert vins_dir.is_dir(), record["id"]
        assert (vins_dir / "vio_corrected_stream.csv").is_file(), record["id"]
        assert (vins_dir / "run_acceptance.json").is_file(), record["id"]
        assert left_dir.is_dir(), record["id"]
        assert (
            (left_dir / "trajectory_frames.csv").is_file()
            or (left_dir / "trajectory_imu_metric.csv").is_file()
        ), record["id"]
        assert (
            (left_dir / "stereo_scale_bidirectional_report.json").is_file()
            or (left_dir / "stereo_scale_right_report.json").is_file()
        ), record["id"]
        if record["left_status"] == "full":
            assert (left_dir / "imu_scale_report.json").is_file(), record["id"]

        assert scoring_dir.is_dir(), record["id"]
        assert (scoring_dir / "reference_provenance.json").is_file(), record["id"]
        assert (scoring_dir / "workflow_manifest.json").is_file(), record["id"]
        assert reference_manifest.is_file(), record["id"]
        assert Path(record["capture_dir"]).is_absolute(), record["id"]
        assert Path(record["session"]).is_absolute(), record["id"]
        assert Path(record["left_dir"]).is_absolute(), record["id"]
        assert Path(record["vins_dir"]).is_absolute(), record["id"]

        for stereo_report in record["left_stereo_reports"]:
            assert _path(stereo_report).is_file(), record["id"]
        if record["left_status"] == "full":
            assert len(record["left_stereo_reports"]) == 4, record["id"]
            assert record["left_imu_scale_report"], record["id"]
            assert _path(record["left_imu_scale_report"]).is_file(), record["id"]


def test_declared_product_scores_and_right_caches_exist():
    manifest = _load_manifest()
    right_records = [record for record in manifest["records"] if record["right_dir"]]

    assert {record["id"] for record in right_records} == {
        "20260930_take02",
        "20260930_take04",
        "20260930_take06",
    }
    for record in manifest["records"]:
        if record["product_fusion_score_dir"]:
            score_dir = _path(record["product_fusion_score_dir"])
            assert (score_dir / "precision.json").is_file(), record["id"]
            assert (score_dir / "reference_provenance.json").is_file(), record["id"]
            assert (score_dir / "workflow_manifest.json").is_file(), record["id"]

    for record in right_records:
        right_dir = _path(record["right_dir"])
        assert right_dir.is_dir(), record["id"]
        assert _path(record["right_run_manifest"]).is_file(), record["id"]
        assert _path(record["right_imu_scale_report"]).is_file(), record["id"]
        assert len(record["right_stereo_reports"]) == 4
        for report in record["right_stereo_reports"]:
            assert _path(report).is_file(), record["id"]
        assert record["right_input_sha256"]["source_frames_csv_sha256"]
        assert record["right_input_sha256"]["config_sha256"]
        assert record["right_input_sha256"]["toolchain_commit"]
        assert record["right_input_sha256"]["checkpoint_sha256"]
        assert record["right_input_sha256"]["elapsed_s"] > 0
