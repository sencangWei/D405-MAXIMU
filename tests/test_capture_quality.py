import csv
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.capture_d405_720p_rgb_stereo_ir import (
    MetadataFrame,
    StreamContinuity,
    decode_cdr_string,
    pair_metadata_frames,
    required_staging_bytes,
    stats_delta,
    timestamps_aligned,
    write_frames_csv,
)


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


def test_decode_cdr_string_reads_ros2_std_msgs_string():
    value = "Frame number=42;timestamp=1234.5;"
    encoded = value.encode() + b"\0"
    blob = b"\x00\x01\x00\x00" + struct.pack("<I", len(encoded)) + encoded

    assert decode_cdr_string(blob) == value


def test_stats_delta_excludes_warmup_counts():
    start = {"frames_ok": 500, "frames_bad": 3, "dropped_frames": 1}
    end = {"frames_ok": 4500, "frames_bad": 3, "dropped_frames": 1}

    assert stats_delta(start, end) == {
        "frames_ok": 4000,
        "frames_bad": 0,
        "dropped_frames": 0,
    }


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
