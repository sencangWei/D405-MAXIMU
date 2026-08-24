import csv
from pathlib import Path

import pytest

from ego_vio.imu.imu_reader import ImuSample
from ego_vio.recorder.recorder import UnitRecorder
from ego_vio.gripper.training_sync import (
    analyze_gripper_csv,
    write_gripper_camera_alignment,
)


def combined_sample(ts=10.0, encoder_ts=10.000065):
    return ImuSample(
        ts=ts,
        counter=123,
        gx=1.0,
        gy=2.0,
        gz=3.0,
        ax=0.1,
        ay=0.2,
        az=1.0,
        temp=25.0,
        rx_time=10.01,
        protocol="stm32_combined_v1",
        sequence=456,
        flags=0x03,
        imu_first_byte_rx_us=1_000_000,
        encoder_read_us=1_000_065,
        encoder_response=0x1234,
        encoder_ts=encoder_ts,
        encoder_sensor_gap_us=65,
    )


def test_recorder_preserves_imu_format_and_adds_synchronized_gripper(tmp_path):
    recorder = UnitRecorder(
        "external_imu", tmp_path, record_gripper=True, max_queue=8
    )
    recorder.start()
    recorder.put_imu(combined_sample())
    recorder.stop()

    gripper_path = tmp_path / "external_imu" / "gripper_encoder.csv"
    with gripper_path.open(newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) == 1
    assert rows[0]["imu_ts_mono"] == "10.000000000"
    assert rows[0]["encoder_ts_mono"] == "10.000065000"
    assert rows[0]["sensor_pair_delta_us"] == "65"
    assert rows[0]["encoder_valid"] == "1"
    assert rows[0]["loaded_object_size_valid"] == "0"
    assert 0.0 <= float(rows[0]["closure_ratio"]) <= 1.0
    assert (tmp_path / "external_imu" / "imu.bin").stat().st_size == 40

    report = analyze_gripper_csv(gripper_path)
    assert report["result"] == "PASS"
    assert report["rows"] == 1


def test_camera_frames_join_to_nearest_gripper_timestamp(tmp_path):
    gripper_dir = tmp_path / "external_imu"
    gripper_dir.mkdir()
    gripper_path = gripper_dir / "gripper_encoder.csv"
    recorder = UnitRecorder("unused", tmp_path / "seed", record_gripper=True)
    recorder.start()
    recorder.put_imu(combined_sample(ts=9.998935, encoder_ts=9.999000))
    recorder.put_imu(combined_sample(ts=10.031935, encoder_ts=10.032000))
    recorder.stop()
    source = tmp_path / "seed" / "unused" / "gripper_encoder.csv"
    gripper_path.write_bytes(source.read_bytes())

    frame_path = tmp_path / "d405_frames.csv"
    with frame_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=["set_index", "arrival_mono", "color_frame_number", "color_mono"],
        )
        writer.writeheader()
        writer.writerow({
            "set_index": 0, "arrival_mono": "10.0",
            "color_frame_number": 100, "color_mono": "10.0",
        })
        writer.writerow({
            "set_index": 1, "arrival_mono": "10.033",
            "color_frame_number": 101, "color_mono": "10.033",
        })

    output = tmp_path / "gripper_camera_alignment.csv"
    report = write_gripper_camera_alignment(frame_path, gripper_path, output)
    assert report["result"] == "PASS"
    assert report["rows"] == 2
    assert report["absolute_delta_ms"]["max"] == pytest.approx(1.0)
    with output.open(newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert [row["camera_frame_number"] for row in rows] == ["100", "101"]
    assert [row["encoder_sequence"] for row in rows] == ["456", "456"]


def test_legacy_imu_does_not_create_fake_gripper_rows(tmp_path):
    recorder = UnitRecorder("external_imu", tmp_path, record_gripper=True)
    recorder.start()
    sample = combined_sample()
    sample.protocol = "kt_ex9_37"
    recorder.put_imu(sample)
    recorder.stop()
    path = tmp_path / "external_imu" / "gripper_encoder.csv"
    with path.open(newline="", encoding="utf-8") as fp:
        assert list(csv.DictReader(fp)) == []


def test_invalid_encoder_row_fails_training_acceptance(tmp_path):
    recorder = UnitRecorder("external_imu", tmp_path, record_gripper=True)
    recorder.start()
    sample = combined_sample()
    sample.flags = 0x01
    recorder.put_imu(sample)
    recorder.stop()
    report = analyze_gripper_csv(
        tmp_path / "external_imu" / "gripper_encoder.csv"
    )
    assert report["result"] == "FAIL"
    assert report["invalid_rows"] == 1
