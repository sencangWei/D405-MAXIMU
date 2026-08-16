import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from freeze_slam_session_inputs import freeze_session


def make_session(root: Path) -> Path:
    session = root / "recording"
    (session / "external_imu").mkdir(parents=True)
    (session / "acceptance.json").write_text('{"result":"PASS"}')
    (session / "d405_frames.csv").write_text("frame,timestamp\n1,0\n")
    (session / "external_imu" / "imu.bin").write_bytes(b"imu")
    (session / "small.db3").write_bytes(b"small")
    (session / "capture.db3").write_bytes(b"complete-camera-data")
    return session


def test_freeze_session_hashes_all_product_inputs(tmp_path: Path):
    session = make_session(tmp_path)
    output = tmp_path / "frozen.json"

    frozen = freeze_session(session, output)

    assert frozen["frozen_before_slam"] is True
    assert frozen["truth_usage_policy"] == (
        "withheld_from_slam_until_post_run_scoring"
    )
    assert set(frozen["files"]) == {
        "capture_acceptance",
        "camera_db3",
        "camera_timestamps",
        "imu_samples",
    }
    assert Path(frozen["files"]["camera_db3"]["path"]).name == "capture.db3"
    for item in frozen["files"].values():
        path = Path(item["path"])
        assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert json.loads(output.read_text()) == frozen


def test_freeze_session_refuses_to_overwrite(tmp_path: Path):
    session = make_session(tmp_path)
    output = tmp_path / "frozen.json"
    output.write_text("sealed")

    with pytest.raises(FileExistsError):
        freeze_session(session, output)


def test_freeze_session_rejects_failed_capture(tmp_path: Path):
    session = make_session(tmp_path)
    (session / "acceptance.json").write_text('{"result":"FAIL"}')

    with pytest.raises(ValueError, match="capture acceptance must be PASS"):
        freeze_session(session, tmp_path / "frozen.json")
