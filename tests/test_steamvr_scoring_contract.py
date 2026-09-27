"""Official reference scoring must pin identity, frames and time domains."""
import csv
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from test_apply_lighthouse_aprilgrid_calibration import MODULE, write_tracker


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case(tmp_path):
    epoch = 1_788_000_000.0
    query = tmp_path / "estimate.csv"
    with query.open("w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["t_sec", "x", "y", "z"])
        for t in np.arange(1, 2, 1 / 30):
            writer.writerow([epoch + t, 999, 999, 999])
    frames = tmp_path / "d405_frames.csv"
    with frames.open("w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["sensor_event_wall", "sensor_event_mono"])
        for t in np.arange(1, 2, 1 / 30):
            writer.writerow([epoch + t, t])
    config = tmp_path / "vins.yaml"
    fs = cv2.FileStorage(str(config), cv2.FileStorage_WRITE)
    fs.write("body_T_cam0", np.eye(4))
    fs.write("td", -0.009)
    fs.write("estimate_td", 0)
    fs.release()
    tracker = tmp_path / "tracker.csv"
    tt = np.arange(0, 3, 1 / 120)
    poses = np.tile(np.eye(4), (len(tt), 1, 1))
    poses[:, 0, 3] = tt
    write_tracker(tracker, tt, poses, -0.012)
    capture = tmp_path / "capture_manifest.json"
    capture.write_text(json.dumps({"status": "PASS_CAPTURE_ONLY_NOT_CALIBRATED",
        "reference_backend": "steamvr_official", "reference_pose_frame": "steamvr_standing_tracker",
        "serial": "LHR-TEST", "d405_session": str(tmp_path),
        "tracker_integrity": {"status": "PASS", "hashes": {"output_sha256": digest(tracker)}}}))
    historical_capture = tmp_path / "historical_capture.json"
    historical_capture.write_bytes(capture.read_bytes())
    calibration = tmp_path / "calibration.json"
    calibration.write_text(json.dumps({"schema": "lighthouse_d405_aprilgrid_handeye_v1",
        "result": "PASS_CANDIDATE", "slam_supervision": False, "slam_inputs": [],
        "calibration_target_frame": "docker2_vins_body", "tracker_serial": "LHR-TEST",
        "tracker_time_source": "host_monotonic", "tracker_query_offset_ms": -12.0,
        "tracker_T_body": np.eye(4).tolist(),
        "time_offset_policy": "fixed_imu_tracker_sync_composed_with_camera_imu_td",
        "time_alignment": {"camera_imu_td_ms": -9.0, "imu_tracker_query_offset_ms": -3.0,
            "effective_camera_tracker_query_offset_ms": -12.0}}))
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps({"result": "PASS_FIXED_CANDIDATE_VALIDATION",
        "calibration_sha256": digest(calibration), "transform_refit": False, "time_offset_refit": False}))
    reference = tmp_path / "reference.json"
    artifacts = {"calibration": calibration, "original_validated_candidate": calibration,
        "heldout_validation": validation, "calibration_capture": historical_capture,
        "heldout_capture": historical_capture}
    reference.write_text(json.dumps({"schema": "steamvr_aprilgrid_reference_frozen_v1",
        "result": "PASS_RELATIVE_MOTION_REFERENCE", "reference_backend": "steamvr_official",
        "reference_pose_frame": "steamvr_standing_tracker", "target_frame": "docker2_vins_body",
        "tracker_serial": "LHR-TEST", "slam_supervision": False,
        "formal_configuration_sha256": digest(config),
        "artifacts": {k: {"path": str(v), "sha256": digest(v)} for k, v in artifacts.items()}}))
    return query, tracker, calibration, frames, config, reference, capture


def run_case(paths, output, domain="camera"):
    query, tracker, calibration, frames, config, reference, capture = paths
    return MODULE.write_ground_truth(query, tracker, calibration, frames, config,
        output, "body", 0.03, reference_manifest_path=reference,
        capture_manifest_path=capture, query_time_domain=domain)


def test_official_camera_lookup_is_composed_exactly_once(tmp_path):
    paths = case(tmp_path)
    original = paths[0].read_bytes()
    report = run_case(paths, tmp_path / "gt.csv")
    rows = list(csv.DictReader((tmp_path / "gt.csv").open()))
    assert float(rows[0]["x"]) == pytest.approx(1.0, abs=1e-6)
    assert report["tracker_query_offset_ms"] == -12.0
    assert report["reference_backend"] == "steamvr_official"
    assert report["query_time_domain"] == "camera"
    assert paths[0].read_bytes() == original


def test_imu_timestamp_does_not_receive_camera_td_again(tmp_path):
    paths = case(tmp_path)
    report = run_case(paths, tmp_path / "gt.csv", "imu")
    assert report["tracker_query_offset_ms"] == -3.0
    rows = list(csv.DictReader((tmp_path / "gt.csv").open()))
    assert float(rows[0]["x"]) == pytest.approx(1.009, abs=1e-6)


@pytest.mark.parametrize("field,value", [("reference_backend", "libsurvive"),
    ("reference_pose_frame", "other_origin"), ("serial", "OTHER"),
    ("d405_session", "/wrong/session"), ("status", "FAIL_CAPTURE")])
def test_wrong_current_capture_rejected_before_output(tmp_path, field, value):
    paths = case(tmp_path)
    capture = json.loads(paths[-1].read_text())
    capture[field] = value
    paths[-1].write_text(json.dumps(capture))
    with pytest.raises(ValueError):
        run_case(paths, tmp_path / "gt.csv")
    assert not (tmp_path / "gt.csv").exists()


def test_changed_formal_config_is_rejected(tmp_path):
    paths = case(tmp_path)
    paths[4].write_text(paths[4].read_text() + "\n# changed\n")
    with pytest.raises(ValueError):
        run_case(paths, tmp_path / "gt.csv")


def test_changed_tracker_bytes_are_rejected(tmp_path):
    paths = case(tmp_path)
    paths[1].write_bytes(paths[1].read_bytes() + b"\n")
    with pytest.raises(ValueError):
        run_case(paths, tmp_path / "gt.csv")


def test_unknown_query_time_domain_is_rejected(tmp_path):
    paths = case(tmp_path)
    with pytest.raises(ValueError):
        run_case(paths, tmp_path / "gt.csv", "arrival")
