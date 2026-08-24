import json
import struct
import threading
import time
from pathlib import Path

import pytest

from product_calibration.gripper_encoder import (
    GripperEncoderCollector,
    GripperEncoderProcessor,
    HEALTH_SCHEMA,
    JsonlSampleRecorder,
    SAMPLE_SCHEMA,
)
from product_calibration.imu_stream import COMBINED_SIZE, crc16_ccitt_false, parse_combined


ROOT = Path(__file__).parents[1]
PROFILE = ROOT / "product_calibration/umi_manual_gripper_20260824.yaml"
JSON_SCHEMA = ROOT / "product_calibration/gripper_encoder_sample_v1.schema.json"
HEALTH_JSON_SCHEMA = ROOT / "product_calibration/gripper_encoder_health_v1.schema.json"


def raw_imu_frame(counter=42):
    frame = bytearray(37)
    frame[:4] = b"\xeb\x90\x22\x01"
    struct.pack_into("<7fI", frame, 4, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 26.0, counter)
    frame[36] = sum(frame[:36]) & 0xFF
    return bytes(frame)


def combined_packet(
    *, sequence=9, counter=42, imu_us=123456, encoder_us=123521,
    raw_count=2200, flags=0x03,
):
    packet = bytearray(COMBINED_SIZE)
    packet[:4] = b"\xa5\x5a\x01\x3f"
    struct.pack_into(
        "<HIIIIH", packet, 4, flags, sequence, imu_us, encoder_us, counter, raw_count
    )
    packet[24:61] = raw_imu_frame(counter)
    struct.pack_into("<H", packet, 61, crc16_ccitt_false(packet[:61]))
    return bytes(packet)


def test_processor_emits_versioned_app_sample_with_units_and_source_time():
    processor = GripperEncoderProcessor.from_profile(PROFILE)

    sample = processor.process(parse_combined(combined_packet()), host_monotonic_ns=99)
    document = sample.to_dict()

    assert document["schema"] == SAMPLE_SCHEMA
    assert document["calibration_id"] == "UMI_MANUAL_GRIPPER_20260824_V1"
    assert document["protocol"] == "stm32_combined_v1"
    assert document["sequence"] == 9
    assert document["imu_counter"] == 42
    assert document["device_time_us"] == 123521
    assert document["sensor_pair_delta_us"] == 65
    assert document["host_monotonic_ns"] == 99
    assert document["raw_count"] == 2200
    assert document["angle_deg"] == pytest.approx(2200 * 360.0 / 16384.0)
    assert document["valid"] is True
    assert document["loaded_object_size_valid"] is False
    assert 0.0 <= document["closure_ratio"] <= 1.0


def test_invalid_encoder_flags_never_emit_distance_or_closure():
    processor = GripperEncoderProcessor.from_profile(PROFILE)
    sample = processor.process(
        parse_combined(combined_packet(flags=0x05)), host_monotonic_ns=100
    )

    assert sample.valid is False
    assert sample.status == "ENCODER_INVALID"
    assert sample.closure_ratio is None
    assert sample.estimated_no_load_gap_mm is None
    assert sample.single_jaw_travel_mm is None
    assert sample.dual_closing_distance_mm is None


def test_processor_unwraps_encoder_timestamp_and_preserves_pair_delta():
    processor = GripperEncoderProcessor.from_profile(PROFILE)

    first = processor.process(parse_combined(combined_packet(
        sequence=1, counter=1, imu_us=0xFFFFFFB0, encoder_us=0xFFFFFFF0
    )))
    second = processor.process(parse_combined(combined_packet(
        sequence=2, counter=2, imu_us=0x00000020, encoder_us=0x00000060
    )))

    assert second.device_time_us > first.device_time_us
    assert second.device_time_us == (1 << 32) + 0x60
    assert first.sensor_pair_delta_us == 64
    assert second.sensor_pair_delta_us == 64


def test_sample_schema_exactly_covers_public_sample_keys():
    processor = GripperEncoderProcessor.from_profile(PROFILE)
    sample = processor.process(parse_combined(combined_packet())).to_dict()
    schema = json.loads(JSON_SCHEMA.read_text(encoding="utf-8"))

    assert schema["$id"].endswith("gripper_encoder_sample_v1.schema.json")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(sample)
    assert set(schema["properties"]) == set(sample)


def test_health_schema_exactly_covers_public_health_keys():
    collector = GripperEncoderCollector(port="fake", profile=PROFILE, serial_factory=lambda *a, **k: None)
    health = collector.health().to_dict()
    schema = json.loads(HEALTH_JSON_SCHEMA.read_text(encoding="utf-8"))

    assert health["schema"] == HEALTH_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(health)
    assert set(schema["properties"]) == set(health)


def test_jsonl_recorder_writes_one_versioned_object_per_line(tmp_path):
    processor = GripperEncoderProcessor.from_profile(PROFILE)
    sample = processor.process(parse_combined(combined_packet()))
    path = tmp_path / "gripper.jsonl"

    with JsonlSampleRecorder(path, flush_every=1) as recorder:
        recorder.write(sample)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["schema"] == SAMPLE_SCHEMA


def test_collector_keeps_latest_and_bounds_slow_callback_queue():
    payload = b"".join(
        combined_packet(
            sequence=index,
            counter=index,
            imu_us=100000 + index * 2500,
            encoder_us=100065 + index * 2500,
            raw_count=2000 + index,
        )
        for index in range(1, 21)
    )

    class FakeSerial:
        def __init__(self, *args, **kwargs):
            self.payload = payload
            self.closed = False

        @property
        def in_waiting(self):
            return len(self.payload)

        def reset_input_buffer(self):
            pass

        def read(self, _size):
            data, self.payload = self.payload, b""
            if not data:
                time.sleep(0.001)
            return data

        def close(self):
            self.closed = True

    release_callback = threading.Event()

    def slow_callback(_sample):
        release_callback.wait(timeout=0.2)

    collector = GripperEncoderCollector(
        port="fake",
        profile=PROFILE,
        on_sample=slow_callback,
        callback_queue_size=2,
        serial_factory=FakeSerial,
    )
    collector.start()
    deadline = time.monotonic() + 1.0
    while collector.health().frames < 20 and time.monotonic() < deadline:
        time.sleep(0.005)
    latest = collector.latest()
    health = collector.health()
    release_callback.set()
    collector.stop()

    assert latest is not None
    assert latest.sequence == 20
    assert health.frames == 20
    assert health.callback_queue_drops > 0
    assert health.sequence_gaps == 0
    assert health.device_time_regressions == 0
    assert health.crc_errors == 0


def test_collector_health_detects_device_timestamp_regression():
    collector = GripperEncoderCollector(
        port="fake", profile=PROFILE, serial_factory=lambda *a, **k: None
    )
    first = collector.processor.process(parse_combined(combined_packet(
        sequence=1, counter=1, imu_us=900, encoder_us=1000
    )))
    second = collector.processor.process(parse_combined(combined_packet(
        sequence=2, counter=2, imu_us=800, encoder_us=900
    )))

    collector._publish(first, first.raw_flags)
    collector._publish(second, second.raw_flags)

    assert collector.health().device_time_regressions == 1


def test_collector_rejects_non_combined_protocol_packets():
    processor = GripperEncoderProcessor.from_profile(PROFILE)
    packet = parse_combined(combined_packet())
    object.__setattr__(packet, "protocol", "kt_ex9_37")

    with pytest.raises(ValueError, match="stm32_combined_v1"):
        processor.process(packet)
