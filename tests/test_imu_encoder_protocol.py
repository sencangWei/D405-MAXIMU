"""63-byte STM32 IMU+encoder protocol tests; no hardware required."""

import csv
import struct

import numpy as np
import pytest

from ego_vio.imu.imu_encoder_protocol import (
    COMBINED_PACKET_SIZE,
    CombinedImuEncoderReader,
    CombinedPacketError,
    CombinedPacketRecorder,
    CombinedPacketStream,
    McuTimeMapper,
    PacketFlag,
    UInt32MicrosUnwrapper,
    crc16_ccitt_false,
    parse_combined_packet,
    signed_u32_delta,
)
from ego_vio.recorder.recorder import IMU_PACK_FMT, IMU_PACK_SIZE, UnitRecorder


def make_imu_frame(counter=123, values=(1.0, 2.0, 3.0, 0.1, 0.2, 0.9, 25.0)):
    frame = bytearray(37)
    frame[:4] = b"\xeb\x90\x22\x01"
    struct.pack_into("<7fI", frame, 4, *values, counter)
    frame[36] = sum(frame[:36]) & 0xFF
    return bytes(frame)


def make_packet(
    *,
    sequence=7,
    imu_us=1_000_000,
    encoder_us=1_000_244,
    imu_counter=123,
    encoder_response=0x1234,
    flags=PacketFlag.IMU_VALID | PacketFlag.ENCODER_VALID,
):
    packet = bytearray(COMBINED_PACKET_SIZE)
    struct.pack_into(
        "<2sBBHIIII",
        packet,
        0,
        b"\xa5\x5a",
        1,
        COMBINED_PACKET_SIZE,
        int(flags),
        sequence,
        imu_us,
        encoder_us,
        imu_counter,
    )
    struct.pack_into("<H", packet, 22, encoder_response)
    packet[24:61] = make_imu_frame(counter=imu_counter)
    struct.pack_into("<H", packet, 61, crc16_ccitt_false(packet[:61]))
    return bytes(packet)


def test_crc16_ccitt_false_known_vector():
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_parse_combined_packet_and_preserve_legacy_imu_shape():
    packet = parse_combined_packet(
        make_packet(), arrival_monotonic=50.0, arrival_wall=1_800_000_000.0
    )

    assert packet.sequence == 7
    assert packet.imu_first_byte_rx_us == 1_000_000
    assert packet.encoder_read_us == 1_000_244
    assert packet.sensor_gap_us == 244
    assert packet.encoder_ts - packet.imu.ts == pytest.approx(244e-6)
    assert packet.encoder_raw == 0x1234
    assert packet.encoder_angle_deg == pytest.approx(0x1234 * 360.0 / 16384.0)
    assert packet.imu.counter == 123
    assert packet.imu.rx_time == 50.0
    assert packet.raw == make_packet()


def test_signed_sensor_gap_handles_u32_wrap_in_both_directions():
    assert signed_u32_delta(0xFFFF_FFF0, 0x0000_0010) == 32
    assert signed_u32_delta(0x0000_0010, 0xFFFF_FFF0) == -32


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p: p.__setitem__(2, 2), "version"),
        (lambda p: p.__setitem__(3, 62), "length"),
        (lambda p: p.__setitem__(20, p[20] ^ 0x20), "CRC"),
    ],
)
def test_parse_rejects_invalid_protocol_fields(mutate, match):
    packet = bytearray(make_packet())
    mutate(packet)
    with pytest.raises(CombinedPacketError, match=match):
        parse_combined_packet(bytes(packet), arrival_monotonic=1.0)


def test_parse_rejects_invalid_embedded_imu_even_with_valid_outer_crc():
    packet = bytearray(make_packet())
    packet[24] = 0x00
    struct.pack_into("<H", packet, 61, crc16_ccitt_false(packet[:61]))

    with pytest.raises(CombinedPacketError, match="embedded IMU"):
        parse_combined_packet(bytes(packet), arrival_monotonic=1.0)


def test_stream_recovers_from_noise_fragmentation_and_bad_crc():
    good1 = make_packet(sequence=10, imu_us=100)
    bad = bytearray(make_packet(sequence=11, imu_us=2600))
    bad[40] ^= 0x80
    good2 = make_packet(sequence=12, imu_us=5100)
    stream = CombinedPacketStream(queue_capacity=8)

    stream.feed(b"noise" + good1[:17], arrival_monotonic=10.0)
    stream.feed(good1[17:] + bytes(bad) + good2[:9], arrival_monotonic=10.01)
    stream.feed(good2[9:], arrival_monotonic=10.02)
    packets = stream.drain()

    assert [packet.sequence for packet in packets] == [10, 12]
    assert stream.stats["packets_ok"] == 2
    assert stream.stats["crc_errors"] == 1
    assert stream.stats["sequence_gaps"] == 1
    assert stream.stats["resync_bytes"] >= len(b"noise")


def test_bounded_queue_drops_oldest_and_reports_it():
    stream = CombinedPacketStream(queue_capacity=2)
    for sequence in range(3):
        stream.feed(
            make_packet(sequence=sequence, imu_us=sequence * 2500),
            arrival_monotonic=20.0 + sequence * 0.0025,
        )

    assert [packet.sequence for packet in stream.drain()] == [1, 2]
    assert stream.stats["pc_queue_overflow"] == 1


def test_firmware_fault_flags_and_sequence_wrap_are_reported():
    stream = CombinedPacketStream(queue_capacity=4)
    fault_flags = (
        PacketFlag.IMU_VALID
        | PacketFlag.ENCODER_VALID
        | PacketFlag.ENCODER_ERROR
        | PacketFlag.ENCODER_PARITY_ERROR
        | PacketFlag.IMU_COUNTER_GAP
        | PacketFlag.IMU_QUEUE_OVERFLOW
        | PacketFlag.PC_TX_QUEUE_OVERFLOW
    )
    stream.feed(
        make_packet(sequence=0xFFFF_FFFF, imu_us=100, flags=fault_flags),
        arrival_monotonic=1.0,
    )
    stream.feed(make_packet(sequence=0, imu_us=2600), arrival_monotonic=1.0025)

    assert stream.stats["sequence_gaps"] == 0
    assert stream.stats["encoder_errors"] == 1
    assert stream.stats["encoder_parity_errors"] == 1
    assert stream.stats["firmware_imu_counter_gap"] == 1
    assert stream.stats["firmware_imu_queue_overflow"] == 1
    assert stream.stats["firmware_pc_tx_queue_overflow"] == 1


def test_uint32_microsecond_unwrapper_crosses_multiple_wraps():
    unwrap = UInt32MicrosUnwrapper()
    raw = [0xFFFF_FFF0, 0x0000_0010, 0xFFFF_FFF0, 0x0000_0010]
    # The third point represents almost one full uint32 period later.
    values = [unwrap.feed(value) for value in raw]

    assert values[1] - values[0] == 32
    assert values[2] > values[1]
    assert values[3] - values[2] == 32
    assert unwrap.wraps == 2


def test_mcu_time_mapping_is_monotonic_through_wrap_and_usb_batches():
    stream = CombinedPacketStream(queue_capacity=32)
    start = 0xFFFF_F000
    for index in range(20):
        raw_us = (start + index * 2500) & 0xFFFF_FFFF
        # Four packets can arrive in one USB batch.
        arrival = 100.0 + (index // 4) * 0.010 + 0.004
        stream.feed(
            make_packet(sequence=index, imu_us=raw_us, encoder_us=(raw_us + 244) & 0xFFFF_FFFF),
            arrival_monotonic=arrival,
        )

    timestamps = np.asarray([packet.imu.ts for packet in stream.drain()])
    assert np.all(np.diff(timestamps) > 0.0)
    np.testing.assert_allclose(np.diff(timestamps)[4:], 0.0025, atol=0.0003)


def test_mcu_time_mapping_estimates_long_term_clock_scale():
    mapper = McuTimeMapper(phase_window=1200, fit_every=400)
    timestamps = []
    for index in range(1200):
        mcu_us = index * 2500
        true_time = 500.0 + mcu_us * 1.001e-6
        # Deterministic USB batching jitter, which must not become sample jitter.
        arrival = true_time + 0.003 + (0.002 if index % 8 < 4 else 0.0)
        timestamps.append(mapper.feed(mcu_us, arrival))

    assert np.all(np.diff(timestamps) > 0.0)
    assert mapper.clock_scale_ppm == pytest.approx(1000.0, abs=20.0)
    np.testing.assert_allclose(np.diff(timestamps)[800:], 0.0025025, atol=2e-6)


def test_combined_sidecars_and_legacy_imu_bin_are_exact(tmp_path):
    raw = make_packet(sequence=42, imu_us=10_000)
    stream = CombinedPacketStream(queue_capacity=4)
    stream.feed(raw, arrival_monotonic=77.0, arrival_wall=1_800_000_123.0)
    packet = stream.drain()[0]

    sidecars = CombinedPacketRecorder(tmp_path)
    sidecars.start()
    sidecars.put(packet)
    sidecars.stop()

    assert (tmp_path / "imu_encoder_packets.bin").read_bytes() == raw
    row = next(csv.DictReader((tmp_path / "encoder_ts.csv").open()))
    assert int(row["sequence"]) == 42
    assert int(row["encoder_raw"]) == 0x1234

    legacy = UnitRecorder("unit", tmp_path, max_queue=4)
    legacy.start()
    legacy.put_imu(packet.imu)
    legacy.stop()
    legacy_bytes = (tmp_path / "unit" / "imu.bin").read_bytes()

    assert len(legacy_bytes) == IMU_PACK_SIZE == 40
    unpacked = struct.unpack(IMU_PACK_FMT, legacy_bytes)
    assert unpacked[1] == packet.imu.counter
    np.testing.assert_allclose(unpacked[2:], [1.0, 2.0, 3.0, 0.1, 0.2, 0.9, 25.0])


def test_reader_feeds_existing_vins_imu_contract_without_hardware():
    class FakeVins:
        def __init__(self):
            self.samples = []

        def feed_imu(self, sample):
            self.samples.append(sample)

    vins = FakeVins()
    packets = []
    reader = CombinedImuEncoderReader(
        "unused",
        warmup_frames=0,
        on_sample=vins.feed_imu,
        on_packet=packets.append,
    )

    reader.feed_bytes_for_test(
        make_packet(sequence=99, imu_us=123_456),
        arrival_monotonic=88.0,
        arrival_wall=1_800_000_000.0,
    )

    assert len(vins.samples) == 1
    assert vins.samples[0] is packets[0].imu
    assert vars(vins.samples[0]).keys() == {
        "ts", "counter", "gx", "gy", "gz", "ax", "ay", "az", "temp", "rx_time"
    }
