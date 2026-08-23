import struct
import unittest

from tools.combined_capture import (
    PACKET_SIZE,
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
    response: int = 0x9234,
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


class PacketParserTests(unittest.TestCase):
    def test_split_and_concatenated_packets(self) -> None:
        packet = make_packet()
        parser = PacketParser()

        self.assertEqual([], parser.feed(packet[:17]))
        decoded = parser.feed(packet[17:] + packet)

        self.assertEqual(2, len(decoded))
        self.assertEqual(7, decoded[0].sequence)
        self.assertEqual(0x1234, decoded[0].encoder_raw)

    def test_noise_and_bad_crc_resynchronize(self) -> None:
        bad = bytearray(make_packet(sequence=8))
        bad[20] ^= 0x01
        good = make_packet(sequence=9)
        parser = PacketParser()

        decoded = parser.feed(b"noise\xA5" + bytes(bad) + good)

        self.assertEqual([9], [packet.sequence for packet in decoded])
        self.assertEqual(1, parser.crc_errors)
        self.assertGreaterEqual(parser.discarded_bytes, 6)

    def test_payload_fields_and_imu_floats_decode(self) -> None:
        [packet] = PacketParser().feed(
            make_packet(sequence=11, imu_us=0xFFFFFFF0, encoder_us=0x20, counter=123)
        )

        self.assertEqual(11, packet.sequence)
        self.assertEqual(0xFFFFFFF0, packet.imu_first_byte_rx_us)
        self.assertEqual(0x20, packet.encoder_read_us)
        self.assertEqual(123, packet.imu_counter)
        self.assertAlmostEqual(0x1234 * 360.0 / 16384.0, packet.encoder_degrees)
        self.assertAlmostEqual(1.0, packet.imu_values[0])
        self.assertAlmostEqual(-2.0, packet.imu_values[1])
        self.assertAlmostEqual(26.5, packet.imu_values[6])

    def test_timer_unwrapper_extends_one_wrap(self) -> None:
        unwrapper = TimerUnwrapper()

        self.assertEqual(0xFFFFFFF0, unwrapper.extend(0xFFFFFFF0))
        self.assertEqual(0x100000020, unwrapper.extend(0x00000020))

    def test_unsigned_delta_handles_wrap(self) -> None:
        self.assertEqual(0x30, delta_u32(0xFFFFFFF0, 0x00000020))


if __name__ == "__main__":
    unittest.main()
