import csv
import importlib.util
import json
import tempfile
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/capture_steamvr_with_d405.py"
spec = importlib.util.spec_from_file_location("steamvr_capture", SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def write_csv(path, field, values):
    with path.open("w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow([field])
        writer.writerows([[v] for v in values])


def test_conflicting_reader_detects_truncated_and_python_processes():
    rows = "900001 vive_pose_strea /opt/bin/vive_pose_stream\n900002 python3 python3 /opt/vive_pose_stream.py\n900003 survive-cli /opt/survive-cli"
    assert len(capture.conflicting_readers(rows)) == 3


def test_process_arguments_do_not_falsely_match_reader():
    assert capture.conflicting_readers("900004 rg rg vive_pose_stream\n900005 python3 python3 capture_steamvr_with_d405.py") == []


def test_unrelated_unmatched_quote_does_not_abort_preflight():
    assert capture.conflicting_readers('900006 weird /tmp/process "unterminated') == []


def test_staged_trace_hash_checked_then_preserved(tmp_path):
    fd, name = tempfile.mkstemp(prefix="umi_steamvr_pose_", suffix=".csv", dir="/dev/shm")
    with os.fdopen(fd, "wb") as fp:
        fp.write(b"complete,raw\n1,2\n")
    target = tmp_path / "raw.csv"
    capture.preserve_staged_trace(Path(name), target)
    assert target.read_bytes() == b"complete,raw\n1,2\n"
    assert not Path(name).exists()


def test_staging_copy_never_overwrites_existing_target(tmp_path):
    staged, target = tmp_path / "staged.csv", tmp_path / "existing.csv"
    staged.write_bytes(b"new")
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        capture.preserve_staged_trace(staged, target)
    assert staged.read_bytes() == b"new"
    assert target.read_bytes() == b"old"


def test_camera_window_preserves_every_in_window_row(tmp_path):
    raw, frames, out = (tmp_path / n for n in ("raw.csv", "frames.csv", "selected.csv"))
    with raw.open("w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["query_monotonic_begin_ns", "query_monotonic_end_ns", "pose_valid"])
        writer.writerows([[t, t, valid] for t, valid in
                         [(100000000, 1), (1000000000, 1), (1010000000, 0),
                          (1060000000, 1), (1070000000, 1), (2000000000, 1)]])
    write_csv(frames, "sensor_event_mono", [1.005, 1.065])
    before = raw.read_bytes()
    result = capture.camera_window_trace(raw, frames, out)
    selected = list(csv.DictReader(out.open()))
    assert result["selected_rows"] == 4
    assert selected[1]["pose_valid"] == "0"
    assert int(selected[2]["query_monotonic_begin_ns"]) - int(selected[1]["query_monotonic_begin_ns"]) == 50000000
    assert raw.read_bytes() == before


def test_overlap_uses_exposure_not_arrival(tmp_path):
    tracker, frames = tmp_path / "tracker.csv", tmp_path / "frames.csv"
    write_csv(tracker, "host_monotonic_ns", [1000000000, 1010000000, 1020000000])
    with frames.open("w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["sensor_event_mono", "arrival_mono"])
        writer.writerow([1.005, 90])
        writer.writerow([1.015, 91])
    result = capture.overlap_report(tracker, frames)
    assert result["status"] == "PASS_CLOCK_COVERAGE_ONLY"
    assert result["supported_frames"] == 2
    assert result["dynamic_time_offset"] == "NOT_ESTIMATED"


def test_overlap_rejects_long_gap(tmp_path):
    tracker, frames = tmp_path / "tracker.csv", tmp_path / "frames.csv"
    write_csv(tracker, "host_monotonic_ns", [1000000000, 1100000000])
    write_csv(frames, "sensor_event_mono", [1.01, 1.02])
    assert capture.overlap_report(tracker, frames)["status"] == "FAIL_CLOCK_COVERAGE"


def test_missing_exposure_time_is_not_silently_aliased(tmp_path):
    tracker, frames = tmp_path / "tracker.csv", tmp_path / "frames.csv"
    write_csv(tracker, "host_monotonic_ns", [1000000000, 1010000000])
    write_csv(frames, "arrival_mono", [1.005])
    with pytest.raises(ValueError, match="authoritative"):
        capture.overlap_report(tracker, frames)


def test_regressing_exposure_time_rejected(tmp_path):
    tracker, frames = tmp_path / "tracker.csv", tmp_path / "frames.csv"
    write_csv(tracker, "host_monotonic_ns", [1000000000, 1010000000])
    write_csv(frames, "sensor_event_mono", [1.005, 1.004])
    with pytest.raises(ValueError, match="increasing"):
        capture.overlap_report(tracker, frames)


def test_dry_run_does_not_start_devices_or_create_output(tmp_path, monkeypatch, capsys):
    out = tmp_path / "should_not_exist"
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--dry-run", "--output", str(out)])
    monkeypatch.setattr(capture.os, "access", lambda *args: True)
    def forbidden(*args, **kwargs):
        raise AssertionError("Dry run must not start subprocesses")
    monkeypatch.setattr(capture.subprocess, "run", forbidden)
    monkeypatch.setattr(capture.subprocess, "Popen", forbidden)
    assert capture.main() == 0
    assert not out.exists()
    assert '"starts_libsurvive": false' in capsys.readouterr().out


def test_lighthouse_world_snapshot_detects_changed_base_geometry(tmp_path):
    database = tmp_path / "lighthousedb.json"
    world = {"revision": 42, "known_universes": [{"id": "1790459304", "base_stations": [
        {"base_serial_number": int("6E49384F", 16), "target_pose": {"pose": [0, 0, 0, 1, 0, 0, 0]}},
        {"base_serial_number": int("9D67E4BD", 16), "target_pose": {"pose": [0, 0, 0, 1, 1, 0, 0]}},
    ]}]}
    database.write_text(json.dumps(world))
    before = capture.snapshot_lighthouse_world(database, tmp_path / "before.json")
    assert before["revision"] == 42
    assert before["universe_id"] == "1790459304"
    world["revision"] = 43
    database.write_text(json.dumps(world))
    same_geometry = capture.snapshot_lighthouse_world(database, tmp_path / "same.json")
    assert capture.same_lighthouse_geometry(before, same_geometry)
    world["known_universes"][0]["base_stations"][1]["target_pose"]["pose"][4] += 0.016
    database.write_text(json.dumps(world))
    changed = capture.snapshot_lighthouse_world(database, tmp_path / "changed.json")
    assert not capture.same_lighthouse_geometry(before, changed)
    assert json.loads((tmp_path / "before.json").read_text())["revision"] == 42


def test_lighthouse_world_snapshot_rejects_missing_base(tmp_path):
    database = tmp_path / "lighthousedb.json"
    database.write_text(json.dumps({"revision": 1, "known_universes": [
        {"id": "x", "base_stations": [{"base_serial_number": int("6E49384F", 16),
                                  "target_pose": {"pose": [0, 0, 0, 1, 0, 0, 0]}}]}
    ]}))
    with pytest.raises(ValueError, match="expected Lighthouse bases"):
        capture.snapshot_lighthouse_world(database, tmp_path / "invalid.json")
    assert not (tmp_path / "invalid.json").exists()
