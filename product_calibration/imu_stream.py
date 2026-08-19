"""KT-EX9 37-byte and STM32 v1 63-byte IMU stream parsing/capture."""

from __future__ import annotations

import json
import csv
from contextlib import ExitStack
import struct
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO


RAW_HEADER = b"\xeb\x90\x22"
COMBINED_HEADER = b"\xa5\x5a"
RAW_SIZE = 37
COMBINED_SIZE = 63
NORMALIZED_FORMAT = "<dI7f"
NORMALIZED_SIZE = struct.calcsize(NORMALIZED_FORMAT)
UINT32 = 1 << 32


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def raw_imu_checksum_valid(frame: bytes) -> bool:
    return len(frame) == RAW_SIZE and (sum(frame[:36]) & 0xFF) == frame[36]


@dataclass(frozen=True)
class ImuPacket:
    protocol: str
    counter: int
    gx: float
    gy: float
    gz: float
    ax: float
    ay: float
    az: float
    temperature_c: float
    raw_packet: bytes
    sequence: int | None = None
    flags: int = 0
    imu_first_byte_rx_us: int | None = None
    encoder_read_us: int | None = None
    encoder_response: int | None = None

    @property
    def imu_valid(self) -> bool:
        return self.protocol == "kt_ex9_37" or bool(self.flags & 0x01)


def parse_raw_imu(frame: bytes) -> ImuPacket:
    if len(frame) != RAW_SIZE or frame[:3] != RAW_HEADER:
        raise ValueError("invalid KT-EX9 frame header/length")
    if not raw_imu_checksum_valid(frame):
        raise ValueError("invalid KT-EX9 checksum")
    gx, gy, gz, ax, ay, az, temperature = struct.unpack_from("<7f", frame, 4)
    counter = struct.unpack_from("<I", frame, 32)[0]
    return ImuPacket(
        protocol="kt_ex9_37",
        counter=counter,
        gx=gx,
        gy=gy,
        gz=gz,
        ax=ax,
        ay=ay,
        az=az,
        temperature_c=temperature,
        raw_packet=frame,
    )


def parse_combined(packet: bytes) -> ImuPacket:
    if len(packet) != COMBINED_SIZE or packet[:2] != COMBINED_HEADER:
        raise ValueError("invalid combined packet header/length")
    if packet[2] != 1 or packet[3] != COMBINED_SIZE:
        raise ValueError("unsupported combined packet version/length")
    expected = struct.unpack_from("<H", packet, 61)[0]
    if crc16_ccitt_false(packet[:61]) != expected:
        raise ValueError("invalid combined packet CRC")
    flags = struct.unpack_from("<H", packet, 4)[0]
    sequence, imu_us, encoder_us, outer_counter = struct.unpack_from("<IIII", packet, 6)
    encoder_response = struct.unpack_from("<H", packet, 22)[0]
    embedded = parse_raw_imu(packet[24:61])
    if embedded.counter != outer_counter:
        raise ValueError("combined/embedded IMU counter mismatch")
    return ImuPacket(
        protocol="stm32_combined_v1",
        counter=embedded.counter,
        gx=embedded.gx,
        gy=embedded.gy,
        gz=embedded.gz,
        ax=embedded.ax,
        ay=embedded.ay,
        az=embedded.az,
        temperature_c=embedded.temperature_c,
        raw_packet=packet,
        sequence=sequence,
        flags=flags,
        imu_first_byte_rx_us=imu_us,
        encoder_read_us=encoder_us,
        encoder_response=encoder_response,
    )


class StreamDecoder:
    """Resynchronizing decoder for either supported serial protocol."""

    def __init__(self, protocol: str = "auto") -> None:
        if protocol not in {"auto", "kt_ex9_37", "stm32_combined_v1"}:
            raise ValueError(f"unsupported protocol: {protocol}")
        self.protocol = protocol
        self.buffer = bytearray()
        self.crc_or_checksum_errors = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[ImuPacket]:
        self.buffer.extend(data)
        packets: list[ImuPacket] = []
        while True:
            choices = []
            if self.protocol in {"auto", "stm32_combined_v1"}:
                choices.append((self.buffer.find(COMBINED_HEADER), COMBINED_SIZE, parse_combined))
            if self.protocol in {"auto", "kt_ex9_37"}:
                choices.append((self.buffer.find(RAW_HEADER), RAW_SIZE, parse_raw_imu))
            found = [(index, size, parser) for index, size, parser in choices if index >= 0]
            if not found:
                keep = 2 if self.protocol != "stm32_combined_v1" else 1
                if len(self.buffer) > keep:
                    removed = len(self.buffer) - keep
                    del self.buffer[:removed]
                    self.discarded_bytes += removed
                break
            index, size, parser = min(found, key=lambda item: item[0])
            if index:
                del self.buffer[:index]
                self.discarded_bytes += index
            if len(self.buffer) < size:
                break
            candidate = bytes(self.buffer[:size])
            try:
                packets.append(parser(candidate))
                del self.buffer[:size]
            except ValueError:
                self.crc_or_checksum_errors += 1
                # A syntactically valid 63-byte envelope with a bad CRC must
                # never fall through and expose its embedded 37-byte payload as
                # a standalone valid frame.  That would bypass STM32 flags/CRC.
                discard = (
                    size if parser is parse_combined and candidate[:4] == COMBINED_HEADER + bytes((1, COMBINED_SIZE))
                    else 1
                )
                del self.buffer[:discard]
                self.discarded_bytes += discard
        return packets


class TimerUnwrapper:
    def __init__(self) -> None:
        self.previous: int | None = None
        self.epoch = 0

    def extend(self, value: int) -> int:
        value &= 0xFFFFFFFF
        if self.previous is not None and value < self.previous and self.previous - value > UINT32 // 2:
            self.epoch += UINT32
        self.previous = value
        return self.epoch + value


@dataclass
class CaptureStats:
    protocol: str = "unknown"
    frames: int = 0
    invalid_imu_flags: int = 0
    counter_gaps: int = 0
    sequence_gaps: int = 0
    queue_overflow_flags: int = 0
    crc_or_checksum_errors: int = 0
    discarded_bytes: int = 0
    duration_s: float = 0.0
    rate_hz: float = 0.0
    interrupted: bool = False


def _counter_gap(previous: int | None, current: int) -> bool:
    return previous is not None and ((current - previous) & 0xFFFFFFFF) != 1


def capture_serial(
    *,
    port: str,
    baud: int,
    duration_s: float,
    output_dir: Path,
    protocol: str = "auto",
    write_timestamp_csv: bool = True,
    serial_factory=None,
) -> CaptureStats:
    """Capture normalized imu.bin plus timestamped raw packets and summary.json."""
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    if serial_factory is None:
        import serial

        serial_factory = serial.Serial
    decoder = StreamDecoder(protocol)
    unwrapper = TimerUnwrapper()
    stats = CaptureStats()
    previous_counter = None
    previous_sequence = None
    raw_sample_clock = -1
    start = time.monotonic()
    first_sensor_time = None
    last_sensor_time = None
    raw_path = output_dir / "raw_packets.bin"
    imu_path = output_dir / "imu.bin"
    with ExitStack() as stack:
        serial_port = stack.enter_context(serial_factory(port, baudrate=baud, timeout=0.1))
        if hasattr(serial_port, "reset_input_buffer"):
            serial_port.reset_input_buffer()
        raw_stream = stack.enter_context(raw_path.open("wb"))
        imu_stream = stack.enter_context(imu_path.open("wb"))
        timestamp_stream = (
            stack.enter_context((output_dir / "imu_ts.csv").open("w", newline="", encoding="utf-8"))
            if write_timestamp_csv else None
        )
        timestamp_writer = csv.writer(timestamp_stream) if timestamp_stream else None
        if timestamp_writer:
            timestamp_writer.writerow(["counter", "ts_mono", "rx_mono", "ts_wall"])
        try:
            while time.monotonic() - start < duration_s:
                chunk = serial_port.read(4096)
                if not chunk:
                    continue
                for packet in decoder.feed(chunk):
                    # Each parsed packet gets its own host-arrival observation.  A
                    # whole USB batch sharing one timestamp makes the later robust
                    # counter fit unnecessarily ill-conditioned.
                    received_ns = time.time_ns()
                    received_mono = time.monotonic()
                    stats.frames += 1
                    stats.protocol = packet.protocol
                    raw_stream.write(struct.pack("<QH", received_ns, len(packet.raw_packet)))
                    raw_stream.write(packet.raw_packet)
                    if not packet.imu_valid:
                        stats.invalid_imu_flags += 1
                        continue
                    counter_before = previous_counter
                    if _counter_gap(counter_before, packet.counter):
                        stats.counter_gaps += 1
                    previous_counter = packet.counter
                    if packet.sequence is not None:
                        if _counter_gap(previous_sequence, packet.sequence):
                            stats.sequence_gaps += 1
                        previous_sequence = packet.sequence
                    if packet.flags & ((1 << 5) | (1 << 6)):
                        stats.queue_overflow_flags += 1
                    if packet.imu_first_byte_rx_us is not None:
                        sensor_time = unwrapper.extend(packet.imu_first_byte_rx_us) / 1_000_000.0
                    else:
                        delta = ((packet.counter - counter_before) & 0xFFFFFFFF) if counter_before is not None else 1
                        raw_sample_clock += delta if 1 <= delta <= 8 else 1
                        sensor_time = raw_sample_clock / 400.0
                    first_sensor_time = sensor_time if first_sensor_time is None else first_sensor_time
                    last_sensor_time = sensor_time
                    imu_stream.write(
                        struct.pack(
                            NORMALIZED_FORMAT,
                            sensor_time,
                            packet.counter,
                            packet.gx,
                            packet.gy,
                            packet.gz,
                            packet.ax,
                            packet.ay,
                            packet.az,
                            packet.temperature_c,
                        )
                    )
                    if timestamp_writer:
                        timestamp_writer.writerow(
                            [packet.counter, f"{sensor_time:.9f}", f"{received_mono:.9f}", f"{received_ns / 1e9:.9f}"]
                        )
        except KeyboardInterrupt:
            stats.interrupted = True
    stats.duration_s = (
        float(last_sensor_time - first_sensor_time)
        if first_sensor_time is not None and last_sensor_time is not None
        else 0.0
    )
    stats.rate_hz = (stats.frames - 1) / stats.duration_s if stats.duration_s > 0 and stats.frames > 1 else 0.0
    stats.crc_or_checksum_errors = decoder.crc_or_checksum_errors
    stats.discarded_bytes = decoder.discarded_bytes
    (output_dir / "summary.json").write_text(
        json.dumps(asdict(stats), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return stats


def iter_normalized(stream: BinaryIO):
    while True:
        chunk = stream.read(NORMALIZED_SIZE)
        if not chunk:
            return
        if len(chunk) != NORMALIZED_SIZE:
            raise ValueError("truncated normalized IMU record")
        yield struct.unpack(NORMALIZED_FORMAT, chunk)
