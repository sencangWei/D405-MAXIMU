import csv
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.capture_d405_720p_rgb_stereo_ir import (
    MetadataFrame,
    StreamContinuity,
    analyze_ir_exposure,
    capture_streams_for_mode,
    configure_ir_auto_exposure,
    decode_cdr_string,
    metadata_frame_from_text,
    pair_metadata_frames,
    required_staging_bytes,
    stats_delta,
    timestamps_aligned,
    live_vins_timestamp_monotonic,
    imu_transport_accepted,
    write_frames_csv,
)


class FakeOptionRange:
    def __init__(self, minimum, maximum):
        self.min = minimum
        self.max = maximum


class FakeSensor:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    def supports(self, _option):
        return True

    def get_option_range(self, option):
        if "gain" in str(option).lower():
            return FakeOptionRange(16.0, 248.0)
        return FakeOptionRange(1.0, 165000.0)

    def set_option(self, option, value):
        self.values[option] = value
        self.set_calls.append((option, value))

    def get_option(self, option):
        return self.values[option]


class MismatchedReadbackSensor(FakeSensor):
    def get_option(self, option):
        value = super().get_option(option)
        if "exposure limit" in str(option).lower():
            return value + 1000.0
        return value


def test_stream_continuity_reports_frame_gaps_and_timestamp_regression():
    stats = StreamContinuity()
    stats.add(10, 1000.0)
    stats.add(11, 1033.333)
    stats.add(14, 1133.333)
    stats.add(15, 1100.0)

    report = stats.report()

    assert report["received"] == 4
    assert report["skipped_frames"] == 2
    assert report["gap_events"] == 1
    assert report["timestamp_regressions"] == 1
    assert report["gap_ratio"] == 0.4


def test_depth_stereo_mode_replaces_color_without_dropping_dual_ir():
    names = [item[0] for item in capture_streams_for_mode("depth_stereo_ir")]

    assert names == ["depth", "infrared_left", "infrared_right"]


def test_live_vins_uses_the_same_global_time_mapping_as_recorded_db3():
    """Live VINS must consume the exposure time later reconstructed for replay."""
    assert live_vins_timestamp_monotonic(1_785_952_580_123.0, 1_785_952_000.0) == pytest.approx(580.123)


def test_decode_cdr_string_reads_ros2_std_msgs_string():
    value = "Frame number=42;timestamp=1234.5;"
    encoded = value.encode() + b"\0"
    blob = b"\x00\x01\x00\x00" + struct.pack("<I", len(encoded)) + encoded

    assert decode_cdr_string(blob) == value


def test_metadata_parser_reads_actual_exposure_and_gain():
    frame = metadata_frame_from_text(
        "Frame number=42;timestamp=1234.5;Actual Exposure=7954;Gain Level=209;"
    )

    assert frame == MetadataFrame(42, 1234.5, exposure_us=7954.0, gain=209.0)


def test_ir_exposure_report_rejects_recorded_frame_over_limit():
    records = {
        "color": [MetadataFrame(1, 1000.0)],
        "infrared_left": [MetadataFrame(1, 1000.0, 7954.0, 209.0)],
        "infrared_right": [MetadataFrame(1, 1000.0, 8200.0, 209.0)],
    }

    report = analyze_ir_exposure(records, limit_us=8000.0)

    assert report["result"] == "FAIL"
    assert report["streams"]["infrared_left"]["result"] == "PASS"
    assert report["streams"]["infrared_right"]["result"] == "FAIL"


def test_ir_exposure_report_requires_complete_metadata():
    records = {
        "infrared_left": [MetadataFrame(1, 1000.0, 7954.0, None)],
        "infrared_right": [],
    }

    report = analyze_ir_exposure(records, limit_us=8000.0)

    assert report["result"] == "FAIL"
    assert report["streams"]["infrared_left"]["metadata_complete"] is False
    assert report["streams"]["infrared_right"]["metadata_complete"] is False


def test_ir_exposure_report_accepts_exact_tolerance_boundary():
    records = {
        key: [MetadataFrame(1, 1000.0, 8100.0, 248.0)]
        for key in ("infrared_left", "infrared_right")
    }

    assert analyze_ir_exposure(records, limit_us=8000.0)["result"] == "PASS"


def test_configure_ir_auto_exposure_sets_limits_before_streaming():
    sensor = FakeSensor()

    report = configure_ir_auto_exposure(sensor, exposure_limit_us=8000.0, gain_limit=248.0)

    assert report["result"] == "PASS"
    assert report["applied"]["auto_exposure_limit_us"] == 8000.0
    assert report["applied"]["auto_gain_limit"] == 248.0
    assert len(sensor.set_calls) == 5


def test_configure_ir_auto_exposure_rejects_mismatched_readback():
    sensor = MismatchedReadbackSensor()

    with pytest.raises(RuntimeError, match="读回值不一致"):
        configure_ir_auto_exposure(
            sensor, exposure_limit_us=8000.0, gain_limit=248.0
        )


def test_stats_delta_excludes_warmup_counts():
    start = {"frames_ok": 500, "frames_bad": 3, "dropped_frames": 1}
    end = {"frames_ok": 4500, "frames_bad": 3, "dropped_frames": 1}

    assert stats_delta(start, end) == {
        "frames_ok": 4000,
        "frames_bad": 0,
        "dropped_frames": 0,
    }


def test_stm32_transport_gate_requires_outer_protocol_evidence():
    clean = {
        "frames_bad": 0,
        "resyncs": 0,
        "dropped_frames": 0,
        "counter_resets": 0,
        "counter_stalls": 0,
        "sequence_gaps": 0,
        "invalid_imu_flags": 0,
        "queue_overflow_flags": 0,
        "serial_errors": 0,
        "serial_reconnects": 0,
    }

    assert imu_transport_accepted("stm32_combined_v1", 400.0, clean, 0)
    assert not imu_transport_accepted("mixed", 400.0, clean, 0)
    assert not imu_transport_accepted(
        "stm32_combined_v1", 400.0, {**clean, "sequence_gaps": 1}, 0
    )


def test_legacy_transport_gate_remains_backward_compatible():
    clean = {
        "frames_bad": 0,
        "resyncs": 0,
        "dropped_frames": 0,
        "counter_resets": 0,
        "counter_stalls": 0,
        "serial_errors": 0,
        "serial_reconnects": 0,
    }

    assert imu_transport_accepted("kt_ex9_37", 400.0, clean, 0)


def test_timestamps_aligned_matches_d405_rgb_and_ir_without_equal_frame_numbers():
    assert timestamps_aligned([840.3936, 839.8533, 839.8533], 2.0)
    assert not timestamps_aligned([840.3936, 806.6047, 806.6047], 2.0)


def test_pair_metadata_frames_skips_missing_stream_frame_without_cross_pairing():
    frame = lambda number, timestamp: MetadataFrame(number, timestamp)
    records = {
        "color": [frame(10, 1000.5), frame(11, 1033.8), frame(12, 1067.2)],
        "infrared_left": [frame(20, 1000.0), frame(22, 1066.7)],
        "infrared_right": [frame(20, 1000.0), frame(21, 1033.3), frame(22, 1066.7)],
    }

    pairs = pair_metadata_frames(records, 2.0)

    assert [pair["color"].number for pair in pairs] == [10, 12]
    assert [pair["infrared_left"].number for pair in pairs] == [20, 22]
    assert [pair["infrared_right"].number for pair in pairs] == [20, 22]


def test_required_staging_bytes_includes_headroom_and_disables_unbounded_capture():
    assert required_staging_bytes(10.0) == 1_271_808_000
    assert required_staging_bytes(0.0) == 0


def test_write_frames_csv_uses_global_time_not_relative_bag_timestamp(tmp_path):
    frame = lambda number, device_ms: MetadataFrame(number, device_ms)
    records = {
        "color": [frame(10, 1_786_463_632_107.8)],
        "infrared_left": [frame(20, 1_786_463_632_107.3)],
        "infrared_right": [frame(20, 1_786_463_632_107.3)],
    }
    path = tmp_path / "frames.csv"

    assert write_frames_csv(path, records, 1_785_952_580.3986, 2.0) == 1
    row = next(csv.DictReader(path.open()))

    assert float(row["arrival_wall"]) > 1_700_000_000
    assert 500_000 < float(row["arrival_mono"]) < 600_000
