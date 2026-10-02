import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "config" / "dual_ir_regression_25_20261002.json"
RECOVERY_PATH = REPO_ROOT / "config" / "dual_ir_regression_25_20261002_recovery.json"
RECOVERY_IDS = {
    "20260929_take04",
    "20260929_take06",
    "20260929_take07",
    "20260929_take09",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def test_recovery_manifest_has_25_unique_records_and_points_to_baseline():
    baseline = _load(BASELINE_PATH)
    recovery = _load(RECOVERY_PATH)
    records = recovery["records"]

    assert recovery["schema"] == "umi_dual_ir_regression_recovery_corpus_v1"
    assert recovery["source_manifest"] == str(BASELINE_PATH)
    assert recovery["source_manifest_sha256"] == _sha(BASELINE_PATH)
    assert len(records) == 25
    assert len({record["id"] for record in records}) == 25
    assert len({record["session"] for record in records}) == 25
    assert recovery["summary"]["record_count"] == 25
    assert recovery["summary"]["unique_session_count"] == 25
    assert recovery["summary"]["source_manifest_schema"] == baseline["schema"]


def test_every_baseline_source_field_is_preserved_unchanged():
    baseline_records = {record["id"]: record for record in _load(BASELINE_PATH)["records"]}
    recovery_records = {record["id"]: record for record in _load(RECOVERY_PATH)["records"]}

    assert set(recovery_records) == set(baseline_records)
    for record_id, baseline_record in baseline_records.items():
        recovery_record = recovery_records[record_id]
        for key, value in baseline_record.items():
            assert recovery_record[key] == value, (record_id, key)


def test_recovery_alternates_are_exactly_the_four_same_record_geometry_caches():
    recovery = _load(RECOVERY_PATH)
    records = {record["id"]: record for record in recovery["records"]}

    assert {
        record["id"]
        for record in recovery["records"]
        if record["alternate_left_status"] == "complete_pass_geometry"
    } == RECOVERY_IDS
    assert records["20260929_take05"]["alternate_left_dir"] is None
    assert records["20260929_take05"]["alternate_left_status"] == "none_unobservable_low_excitation"

    expected_dirs = {
        "20260929_take04": "/home/robot/ego_vio_humble/.planning/keyframe_jump_20260930/joint_se3_event_nodes_ab/take04",
        "20260929_take06": "/home/robot/ego_vio_humble/.planning/keyframe_jump_20260930/joint_se3_ab/take06",
        "20260929_take07": "/home/robot/ego_vio_humble/.planning/keyframe_jump_20260930/joint_se3_walk_ab/take07",
        "20260929_take09": "/home/robot/ego_vio_humble/.planning/keyframe_jump_20260930/joint_se3_ab/take09",
    }
    for record_id, expected_dir in expected_dirs.items():
        record = records[record_id]
        audit = record["alternate_left_audit"]
        assert record["alternate_left_dir"] == expected_dir
        assert audit["alternate_left_dir"] == expected_dir
        assert audit["source_record_session"] == record["session"]
        assert audit["same_record_session_bound"] is True
        assert audit["raw_session_bound"] is True
        assert audit["uses_onboard_only"] is True
        assert audit["external_ground_truth_used"] is False
        assert audit["ground_truth_used_for_selection"] is False
        assert audit["product_fusion_used_for_selection"] is False
        assert audit["eligibility_reason"] == "four_stereo_reports_pass_imu_pass_sessionmatched"
        assert audit["primary_raw_frontend_unchanged"] is True
        assert audit["alternate_recipe_differs_from_primary"] is True
        assert audit["producer_recipe"] == Path(expected_dir).parent.name
        assert "no_product_fusion_selection" in audit["selection_scope"]
        assert audit["tracker_gt_or_external_alignment_references_in_stereo_or_imu_reports"] == []


def test_recovery_alternates_have_complete_pass_geometry_and_fresh_hashes():
    records = {record["id"]: record for record in _load(RECOVERY_PATH)["records"]}

    for record_id in RECOVERY_IDS:
        record = records[record_id]
        audit = record["alternate_left_audit"]
        alt_dir = _path(audit["alternate_left_dir"])

        assert alt_dir.is_dir(), record_id
        assert audit["complete_stereo_report_count"] == 4
        assert audit["all_stereo_reports_pass"] is True
        assert set(audit["stereo_report_hashes"]) == {
            "stereo_scale_bidirectional_report.json",
            "stereo_scale_long_hops_report.json",
            "stereo_scale_dense10hz_report.json",
            "stereo_scale_multisecond_report.json",
        }
        for report in audit["stereo_reports"]:
            report_path = _path(report["path"])
            output_path = _path(report["output"])
            report_json = _load(report_path)
            assert report_path.is_file(), record_id
            assert output_path.is_file(), record_id
            assert report["sha256"] == _sha(report_path)
            assert report["output_sha256"] == _sha(output_path)
            assert report_json["result"] == "PASS"
            assert report_json["failures"] == []
            assert report_json["external_ground_truth_used"] is False
            assert report_json["session"] == record["session"]
            assert report_json["db3"].startswith(record["session"] + "/")
            assert report["accepted_observations"] and report["accepted_observations"] > 0
            assert report["tracker_gt_or_external_alignment_references"] == []

        imu_path = _path(audit["imu_scale_report"])
        orientation_path = _path(audit["orientation_trajectory"])
        assert imu_path.is_file(), record_id
        assert audit["imu_scale_report_sha256"] == _sha(imu_path)
        assert audit["imu_scale_result"] == "PASS"
        assert audit["imu_scale_failures"] == []
        assert audit["imu_scale_external_ground_truth_used"] is False
        assert audit["imu_tracker_gt_or_external_alignment_references"] == []
        assert audit["orientation_trajectory_exists"] is True
        assert orientation_path == _path(record["vins_dir"]) / "vio_corrected_stream.csv"
        assert audit["orientation_trajectory_sha256"] == _sha(orientation_path)

        for entry in audit["raw_trajectory_hashes"].values():
            path = _path(entry["path"])
            assert path.is_file(), record_id
            assert entry["sha256"] == _sha(path)


def test_original_failures_are_preserved_and_graph_failure_not_promoted():
    records = {record["id"]: record for record in _load(RECOVERY_PATH)["records"]}

    for record_id in ["20260929_take04", "20260929_take06", "20260929_take07", "20260929_take09"]:
        original = records[record_id]["recovery_audit"]["original_left_failure_preserved"]
        assert original["left_status"] == "partial_missing_imu_scale"
        assert original["left_imu_scale_report"] is None
        assert len(original["reports"]) == 1
        assert original["reports"][0]["result"] == "FAIL"
        assert original["reports"][0]["accepted_observations"] > 0
        assert "isolated_position_step_jump" in original["reports"][0]["failures"]

    take05_original = records["20260929_take05"]["recovery_audit"]["original_left_failure_preserved"]
    assert take05_original["reports"][0]["result"] == "FAIL"
    assert take05_original["reports"][0]["accepted_observations"] == 0
    assert records["20260929_take05"]["alternate_left_audit"] is None

    take04_alt = records["20260929_take04"]["alternate_left_audit"]
    assert take04_alt["graph_fusion_result"] == "FAIL"
    assert "imu_position_refinement_did_not_improve" in take04_alt["graph_fusion_failures"]
    assert take04_alt["graph_failure_recorded_not_used_for_selection"] is True
