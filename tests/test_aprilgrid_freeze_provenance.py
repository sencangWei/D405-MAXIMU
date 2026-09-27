"""Execute the session wrapper's actual freeze block without ROS/hardware."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


def freeze_case(tmp_path, change=None):
    names = ("calibration.json", "left.yaml", "right.yaml", "stereo.yaml", "grid.yaml", "sync.json")
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.write_text("{}\n")
    paths[0].write_text(json.dumps({"result": "PASS_CANDIDATE",
        "calibration_target_frame": "docker2_vins_body",
        "time_offset_policy": "fixed_imu_tracker_sync_composed_with_camera_imu_td",
        "time_alignment": {"input_domain": "imu"},
        "tracker_T_body": [], "tracker_serial": "TEST-TRACKER"}))
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("frame,t\n1,1.0\n")
    tracker = tmp_path / "tracker.csv"
    tracker.write_text("raw accepted tracker fixture\n")
    capture = {"status": "PASS_CAPTURE_ONLY_NOT_CALIBRATED",
        "reference_backend": "steamvr_official", "reference_pose_frame": "steamvr_standing_tracker",
        "serial": "TEST-TRACKER", "d405_session": str(session),
        "tracker_integrity": {"status": "PASS", "hashes": {
            "output_sha256": hashlib.sha256(tracker.read_bytes()).hexdigest()}}}
    if change:
        change(capture)
    (tmp_path / "capture_manifest.json").write_text(json.dumps(capture))
    text = (Path(__file__).resolve().parents[1] / "scripts/calibrate_lighthouse_aprilgrid_session.sh").read_text()
    block = text.split('"$tracker_csv" <<\'PY\'\n', 1)[1].split("\nPY", 1)[0]
    return subprocess.run([sys.executable, "-", *map(str, paths), str(session), str(tracker)],
        input=block, text=True, capture_output=True)


def test_freeze_records_direct_source_hashes_and_frame(tmp_path):
    process = freeze_case(tmp_path)
    assert process.returncode == 0, process.stderr
    frozen = json.loads((tmp_path / "frozen_manifest.json").read_text())
    assert frozen["reference_backend"] == "steamvr_official"
    assert frozen["reference_pose_frame"] == "steamvr_standing_tracker"
    assert frozen["time_alignment"]["input_domain"] == "imu"
    assert frozen["heldout_validation"] == "NOT_PERFORMED_BY_THIS_SESSION_WRAPPER"
    for artifact in frozen["artifacts"].values():
        assert hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]


@pytest.mark.parametrize("field,value", [("reference_backend", "libsurvive"),
    ("reference_pose_frame", "unknown"), ("serial", "OTHER"),
    ("d405_session", "/missing/session"), ("status", "FAIL_CAPTURE")])
def test_freeze_rejects_wrong_capture_identity(tmp_path, field, value):
    process = freeze_case(tmp_path, lambda capture: capture.update({field: value}))
    assert process.returncode != 0
    assert not (tmp_path / "frozen_manifest.json").exists()


def test_freeze_rejects_changed_tracker_bytes(tmp_path):
    process = freeze_case(tmp_path, lambda capture: capture["tracker_integrity"]["hashes"].update({"output_sha256": "wrong"}))
    assert process.returncode != 0
    assert "no longer matches" in process.stderr
    assert not (tmp_path / "frozen_manifest.json").exists()
