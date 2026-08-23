from __future__ import annotations

import dataclasses
import enum
import struct
import time


SYNC = b"\xA5\x5A"
VERSION = 1
PACKET_SIZE = 63
CRC_OFFSET = 61
ENCODER_DATA_MASK = 0x3FFF


class PacketFlag(enum.IntFlag):
    IMU_VALID = 1 << 0
    ENCODER_VALID = 1 << 1
    ENCODER_ERROR = 1 << 2
    ENCODER_PARITY_ERROR = 1 << 3
    IMU_COUNTER_GAP = 1 << 4
    IMU_QUEUE_OVERFLOW = 1 << 5
    PC_TX_QUEUE_OVERFLOW = 1 << 6


def crc16_ccitt_false(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def delta_u32(start: int, end: int) -> int:
    return (end - start) & 0xFFFFFFFF


@dataclasses.dataclass(frozen=True)
class ImuData:
    gx: float
    gy: float
    gz: float
    ax: float
    ay: float
    az: float
    temperature_c: float


@dataclasses.dataclass(frozen=True)
class CombinedSample:
    pc_unix_ns: int
    flags: PacketFlag
    sequence: int
    imu_first_byte_rx_us: int
    encoder_read_us: int
    imu_counter: int
    encoder_response: int
    imu: ImuData
    imu_frame: bytes
    raw_packet: bytes

    def _has_flag(self, flag: PacketFlag) -> bool:
        return bool(self.flags & flag)

    @property
    def imu_valid(self) -> bool:
        return self._has_flag(PacketFlag.IMU_VALID)

    @property
    def encoder_valid(self) -> bool:
        return self._has_flag(PacketFlag.ENCODER_VALID)

    @property
    def encoder_error(self) -> bool:
        return self._has_flag(PacketFlag.ENCODER_ERROR)

    @property
    def encoder_parity_error(self) -> bool:
        return self._has_flag(PacketFlag.ENCODER_PARITY_ERROR)

    @property
    def imu_counter_gap(self) -> bool:
        return self._has_flag(PacketFlag.IMU_COUNTER_GAP)

    @property
    def imu_queue_overflow(self) -> bool:
        return self._has_flag(PacketFlag.IMU_QUEUE_OVERFLOW)

    @property
    def pc_tx_queue_overflow(self) -> bool:
        return self._has_flag(PacketFlag.PC_TX_QUEUE_OVERFLOW)

    @property
    def encoder_raw(self) -> int:
        return self.encoder_response & ENCODER_DATA_MASK

    @property
    def encoder_angle_deg(self) -> float:
        return self.encoder_raw * 360.0 / 16384.0

    @property
    def encoder_degrees(self) -> float:
        return self.encoder_angle_deg

    @property
    def sensor_gap_us(self) -> int:
        return delta_u32(self.imu_first_byte_rx_us, self.encoder_read_us)

    @property
    def imu_values(self) -> tuple[float, float, float, float, float, float, float]:
        return (
            self.imu.gx,
            self.imu.gy,
            self.imu.gz,
            self.imu.ax,
            self.imu.ay,
            self.imu.az,
            self.imu.temperature_c,
        )


def decode_packet(packet: bytes, pc_unix_ns: int | None = None) -> CombinedSample:
    if len(packet) != PACKET_SIZE:
        raise ValueError(f"expected {PACKET_SIZE} bytes, got {len(packet)}")
    if packet[:2] != SYNC or packet[2] != VERSION or packet[3] != PACKET_SIZE:
        raise ValueError("invalid combined packet header")
    expected_crc = struct.unpack_from("<H", packet, CRC_OFFSET)[0]
    if crc16_ccitt_false(packet[:CRC_OFFSET]) != expected_crc:
        raise ValueError("combined packet CRC mismatch")

    flags, sequence, imu_us, encoder_us, counter, response = struct.unpack_from(
        "<HIIIIH", packet, 4
    )
    imu_frame = packet[24:61]
    imu = ImuData(*struct.unpack_from("<7f", imu_frame, 4))
    return CombinedSample(
        pc_unix_ns=time.time_ns() if pc_unix_ns is None else pc_unix_ns,
        flags=PacketFlag(flags),
        sequence=sequence,
        imu_first_byte_rx_us=imu_us,
        encoder_read_us=encoder_us,
        imu_counter=counter,
        encoder_response=response,
        imu=imu,
        imu_frame=imu_frame,
        raw_packet=packet,
    )


class PacketParser:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self.crc_errors = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes, pc_unix_ns: int | None = None) -> list[CombinedSample]:
        self._buffer.extend(data)
        decoded: list[CombinedSample] = []

        while True:
            sync_index = self._buffer.find(SYNC)
            if sync_index < 0:
                keep = 1 if self._buffer.endswith(SYNC[:1]) else 0
                discard = len(self._buffer) - keep
                if discard:
                    del self._buffer[:discard]
                    self.discarded_bytes += discard
                break
            if sync_index:
                del self._buffer[:sync_index]
                self.discarded_bytes += sync_index
            if len(self._buffer) < PACKET_SIZE:
                break
            if self._buffer[2] != VERSION or self._buffer[3] != PACKET_SIZE:
                del self._buffer[0]
                self.discarded_bytes += 1
                continue

            candidate = bytes(self._buffer[:PACKET_SIZE])
            expected_crc = struct.unpack_from("<H", candidate, CRC_OFFSET)[0]
            if crc16_ccitt_false(candidate[:CRC_OFFSET]) != expected_crc:
                del self._buffer[0]
                self.discarded_bytes += 1
                self.crc_errors += 1
                continue

            decoded.append(decode_packet(candidate, pc_unix_ns=pc_unix_ns))
            del self._buffer[:PACKET_SIZE]

        return decoded


class TimerUnwrapper:
    def __init__(self) -> None:
        self._last: int | None = None
        self._epoch = 0

    def extend(self, value: int) -> int:
        value &= 0xFFFFFFFF
        if self._last is not None and value < self._last and self._last - value > 0x80000000:
            self._epoch += 1 << 32
        self._last = value
        return self._epoch + value
