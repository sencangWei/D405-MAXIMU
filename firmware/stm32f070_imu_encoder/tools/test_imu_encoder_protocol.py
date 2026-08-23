import struct
import unittest

from tools.imu_encoder_protocol import (
    PACKET_SIZE,
    PacketFlag,
    PacketParser,
    TimerUnwrapper,
    crc16_ccitt_false,
    delta_u32,
)


def make_imu_frame(counter: int) -> bytes:
    frame = bytearray(37)
    frame[:4] = b"\xEB\x90\x22\x01"
    struct.pack_into("<7f", frame, 4, 1.0, -2.0, 3.5, 0.1, -0.2, 0.3, 26.5)
    struct.pack_into("<I", frame, 32, counter)
    frame[36] = sum(frame[:36]) & 0xFF
    return bytes(frame)


def make_packet(
    *,
    sequence: int = 7,
    imu_us: int = 1000,
    encoder_us: int = 1050,
    counter: int = 99,
    response: int = 0x1234,
    flags: int = 0x0003,
) -> bytes:
    packet = bytearray(PACKET_SIZE)
    packet[:4] = b"\xA5\x5A\x01\x3F"
    struct.pack_into(
        "<HIIIIH", packet, 4, flags, sequence, imu_us, encoder_us, counter, response
    )
    packet[24:61] = make_imu_frame(counter)
    struct.pack_into("<H", packet, 61, crc16_ccitt_false(packet[:61]))
    return bytes(packet)


class ProtocolTests(unittest.TestCase):
    def test_decodes_named_fields_and_status(self) -> None:
        flags = int(
            PacketFlag.IMU_VALID
            | PacketFlag.ENCODER_VALID
            | PacketFlag.IMU_COUNTER_GAP
        )
        [sample] = PacketParser().feed(
            make_packet(flags=flags), pc_unix_ns=123456789
        )

        self.assertEqual(123456789, sample.pc_unix_ns)
        self.assertEqual(7, sample.sequence)
        self.assertEqual(99, sample.imu_counter)
        self.assertEqual(1.0, sample.imu.gx)
        self.assertEqual(-2.0, sample.imu.gy)
        self.assertEqual(3.5, sample.imu.gz)
        self.assertAlmostEqual(0.1, sample.imu.ax)
        self.assertAlmostEqual(-0.2, sample.imu.ay)
        self.assertAlmostEqual(0.3, sample.imu.az)
        self.assertEqual(26.5, sample.imu.temperature_c)
        self.assertTrue(sample.imu_valid)
        self.assertTrue(sample.encoder_valid)
        self.assertTrue(sample.imu_counter_gap)
        self.assertFalse(sample.encoder_error)
        self.assertFalse(sample.encoder_parity_error)
        self.assertFalse(sample.imu_queue_overflow)
        self.assertFalse(sample.pc_tx_queue_overflow)

    def test_converts_encoder_angle_and_preserves_raw_data(self) -> None:
        packet = make_packet(response=0xD234)
        [sample] = PacketParser().feed(packet, pc_unix_ns=1)

        self.assertEqual(0x1234, sample.encoder_raw)
        self.assertAlmostEqual(0x1234 * 360.0 / 16384.0, sample.encoder_angle_deg)
        self.assertEqual(sample.encoder_angle_deg, sample.encoder_degrees)
        self.assertEqual(
            (
                sample.imu.gx,
                sample.imu.gy,
                sample.imu.gz,
                sample.imu.ax,
                sample.imu.ay,
                sample.imu.az,
                sample.imu.temperature_c,
            ),
            sample.imu_values,
        )
        self.assertEqual(packet, sample.raw_packet)
        self.assertEqual(packet[24:61], sample.imu_frame)

    def test_returns_imu_sample_when_encoder_is_invalid(self) -> None:
        [sample] = PacketParser().feed(
            make_packet(flags=int(PacketFlag.IMU_VALID), response=0x4000),
            pc_unix_ns=2,
        )

        self.assertTrue(sample.imu_valid)
        self.assertFalse(sample.encoder_valid)
        self.assertEqual(1.0, sample.imu.gx)

    def test_handles_split_and_concatenated_packets(self) -> None:
        first = make_packet(sequence=1)
        second = make_packet(sequence=2)
        parser = PacketParser()

        self.assertEqual([], parser.feed(first[:17], pc_unix_ns=10))
        decoded = parser.feed(first[17:] + second, pc_unix_ns=20)

        self.assertEqual([1, 2], [sample.sequence for sample in decoded])
        self.assertEqual([20, 20], [sample.pc_unix_ns for sample in decoded])

    def test_recovers_after_noise_bad_crc_and_unsupported_header(self) -> None:
        bad_crc = bytearray(make_packet(sequence=2))
        bad_crc[20] ^= 1
        bad_version = bytearray(make_packet(sequence=3))
        bad_version[2] = 2
        good = make_packet(sequence=4)
        parser = PacketParser()

        decoded = parser.feed(
            b"noise" + bytes(bad_crc) + bytes(bad_version) + good,
            pc_unix_ns=30,
        )

        self.assertEqual([4], [sample.sequence for sample in decoded])
        self.assertEqual(1, parser.crc_errors)
        self.assertGreaterEqual(parser.discarded_bytes, 5 + PACKET_SIZE + PACKET_SIZE)

    def test_timer_helpers_handle_u32_wrap(self) -> None:
        timer = TimerUnwrapper()

        self.assertEqual(0xFFFFFFF0, timer.extend(0xFFFFFFF0))
        self.assertEqual(0x100000020, timer.extend(0x20))
        self.assertEqual(0x30, delta_u32(0xFFFFFFF0, 0x20))


if __name__ == "__main__":
    unittest.main()
