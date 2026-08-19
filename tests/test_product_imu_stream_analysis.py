import json
import struct

import numpy as np

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
