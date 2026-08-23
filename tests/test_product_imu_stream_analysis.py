import json
import struct
import time

import numpy as np

from product_calibration.compatible_imu_reader import CompatibleImuReader
from product_calibration.imu_analysis import analyze_allan, analyze_static
from product_calibration.imu_stream import (
    COMBINED_SIZE,
    NORMALIZED_FORMAT,
    StreamDecoder,
    TimerUnwrapper,
    capture_serial,
    crc16_ccitt_false,
    parse_combined,
)


def raw_imu_frame(counter=42, gyro=(0.1, -0.2, 0.3), accel=(0.0, 0.0, 1.0), temp=26.5):
    frame = bytearray(37)
    frame[:4] = b"\xeb\x90\x22\x01"
    struct.pack_into("<7fI", frame, 4, *gyro, *accel, temp, counter)
    frame[36] = sum(frame[:36]) & 0xFF
    return bytes(frame)


def combined_packet(sequence=9, counter=42, imu_us=123456, encoder_us=123500):
    packet = bytearray(COMBINED_SIZE)
    packet[:4] = b"\xa5\x5a\x01\x3f"
    struct.pack_into("<HIIIIH", packet, 4, 0x03, sequence, imu_us, encoder_us, counter, 0x1234)
    packet[24:61] = raw_imu_frame(counter=counter)
    struct.pack_into("<H", packet, 61, crc16_ccitt_false(packet[:61]))
    return bytes(packet)


def write_capture(path, count, *, dt=0.0025, seed=7):
    path.mkdir()
    rng = np.random.default_rng(seed)
    with (path / "imu.bin").open("wb") as stream:
        for index in range(count):
            gyro = rng.normal(0.02, 0.01, 3)
            accel = np.array([0.0, 0.0, 1.0]) + rng.normal(0.0, 0.001, 3)
            stream.write(
                struct.pack(
                    NORMALIZED_FORMAT,
                    index * dt,
                    index,
                    *gyro,
                    *accel,
                    26.0,
                )
            )
    (path / "summary.json").write_text(
        json.dumps(
            {
                "counter_gaps": 0,
                "crc_or_checksum_errors": 0,
                "queue_overflow_flags": 0,
            }
        ),
        encoding="utf-8",
    )


def test_combined_packet_parser_extracts_embedded_imu_and_crc():
    sample = parse_combined(combined_packet())

    assert sample.protocol == "stm32_combined_v1"
    assert sample.sequence == 9
    assert sample.counter == 42
    assert sample.imu_first_byte_rx_us == 123456
    assert sample.encoder_read_us == 123500
    assert sample.imu_valid
    assert abs(sample.az - 1.0) < 1e-9


def test_stream_decoder_resynchronizes_and_handles_fragmented_packets():
    packet = combined_packet()
    decoder = StreamDecoder("auto")

    assert decoder.feed(b"noise" + packet[:13]) == []
    decoded = decoder.feed(packet[13:] + raw_imu_frame(counter=43))

    assert [sample.protocol for sample in decoded] == [
        "stm32_combined_v1",
        "kt_ex9_37",
    ]
    assert decoder.discarded_bytes == 5


def test_compatible_reader_formal_stats_exclude_complete_warmup_batches():
    received = []
    reader = CompatibleImuReader(
        "unused", on_sample=received.append, warmup_frames=2, protocol="auto"
    )

    reader._consume_data(
        b"opening-noise" + raw_imu_frame(counter=10) + raw_imu_frame(counter=12)
    )
    reader._consume_data(raw_imu_frame(counter=13))

    formal = reader.stats_since_warmup()
    assert [sample.counter for sample in received] == [13]
    assert reader.warmup_stats()["resyncs"] == len(b"opening-noise")
    assert reader.warmup_stats()["dropped_frames"] == 1
    assert formal["frames_ok"] == 1
    assert formal["resyncs"] == 0
    assert formal["dropped_frames"] == 0
    assert formal["protocol"] == "kt_ex9_37"


def test_compatible_reader_formal_stats_retain_post_warmup_faults():
    reader = CompatibleImuReader("unused", warmup_frames=1, protocol="auto")
    reader._consume_data(raw_imu_frame(counter=20))

    damaged = bytearray(raw_imu_frame(counter=21))
    damaged[-1] ^= 1
    reader._consume_data(bytes(damaged) + raw_imu_frame(counter=22))

    formal = reader.stats_since_warmup()
    assert formal["frames_bad"] == 1
    assert formal["resyncs"] == len(damaged)
    assert formal["dropped_frames"] == 1


def test_compatible_reader_formal_protocol_excludes_startup_mid_packet_false_raw():
    reader = CompatibleImuReader("unused", warmup_frames=1, protocol="auto")
    first = combined_packet(sequence=1, counter=20)
    second = combined_packet(sequence=2, counter=21)

    # Opening a continuous UART stream can begin inside a combined packet.
    # Auto-detection then sees its embedded 37-byte IMU frame before the next
    # outer header; the entire transition batch belongs to warm-up.
    reader._consume_data(first[10:] + second)
    reader._consume_data(combined_packet(sequence=3, counter=22))

    assert reader.stats()["protocol"] == "mixed"
    assert reader.stats_since_warmup()["protocol"] == "stm32_combined_v1"


def test_bad_combined_crc_cannot_fall_through_to_embedded_raw_frame():
    damaged = bytearray(combined_packet())
    damaged[61] ^= 0x01
    decoder = StreamDecoder("auto")

    assert decoder.feed(bytes(damaged)) == []
    assert decoder.crc_or_checksum_errors == 1
    assert decoder.discarded_bytes == COMBINED_SIZE


def test_raw_capture_uses_counter_clock_not_usb_batch_arrival(tmp_path):
    class FakeSerial:
        def __init__(self, *args, **kwargs):
            self.payload = raw_imu_frame(10) + raw_imu_frame(11) + raw_imu_frame(12)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def reset_input_buffer(self):
            pass

        def read(self, _size):
            payload, self.payload = self.payload, b""
            return payload

    output = tmp_path / "capture"
    capture_serial(
        port="fake", baud=921600, duration_s=0.005, output_dir=output,
        serial_factory=FakeSerial, write_timestamp_csv=False,
    )
    records = np.fromfile(output / "imu.bin", dtype=np.dtype([
        ("ts", "<f8"), ("counter", "<u4"), ("values", "<f4", 7)
    ]))
    assert np.allclose(records["ts"], [0.0, 0.0025, 0.005])


def test_capture_excludes_serial_opening_resync_from_formal_health(tmp_path):
    class FakeSerial:
        def __init__(self, *args, **kwargs):
            self.read_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def reset_input_buffer(self):
            pass

        def read(self, _size):
            self.read_count += 1
            if self.read_count == 1:
                time.sleep(0.002)
                return b"opening-noise" + raw_imu_frame(10)
            if self.read_count == 2:
                return raw_imu_frame(20) + raw_imu_frame(21) + raw_imu_frame(22)
            return b""

    output = tmp_path / "capture"
    stats = capture_serial(
        port="fake",
        baud=921600,
        duration_s=0.005,
        output_dir=output,
        serial_factory=FakeSerial,
        write_timestamp_csv=False,
        startup_discard_s=0.001,
    )

    assert stats.frames == 3
    assert stats.counter_gaps == 0
    assert stats.crc_or_checksum_errors == 0
    assert stats.discarded_bytes == 0
    assert stats.startup_discard_s == 0.001


def test_timer_unwrapper_crosses_uint32_microsecond_wrap():
    unwrapper = TimerUnwrapper()

    assert unwrapper.extend(0xFFFFFFF0) == 0xFFFFFFF0
    assert unwrapper.extend(0x00000020) == (1 << 32) + 0x20


def test_static_capture_is_automatically_solved_and_gated(tmp_path):
    capture = tmp_path / "static"
    write_capture(capture, 500, dt=0.0025)

    report = analyze_static(capture, warmup_s=0.2, formal_s=0.8)

    assert report["result"] == "PASS"
    assert report["checks"]["rate_400hz"]
    assert len(report["gyro_bias_deg_s"]) == 3


def test_allan_capture_outputs_machine_parameters_and_gate(tmp_path):
    capture = tmp_path / "allan"
    write_capture(capture, 4096, dt=0.0025)

    report = analyze_allan(capture, min_duration_s=10.0)

    assert report["result"] == "PASS"
    assert report["gyroscope"]["noise_density"] > 0
    assert report["accelerometer"]["random_walk"] > 0


def test_allan_duration_gate_allows_one_sample_span_shortfall(tmp_path):
    capture = tmp_path / "allan_exact_wall_window"
    # A nominal 10 s capture at 400 Hz spans 9.9975 s from first to last sample.
    write_capture(capture, 4000, dt=0.0025)

    report = analyze_allan(capture, min_duration_s=10.0)

    assert report["result"] == "PASS"
    assert report["checks"]["duration"]
