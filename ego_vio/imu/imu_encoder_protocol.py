"""STM32 v1 combined IMU/encoder wire protocol.

This module intentionally keeps the encoder outside the estimator.  A valid
combined packet is converted to the existing :class:`ImuSample` contract so
the legacy recorder and VINS bridge continue to receive exactly the same
fields and units as the 37-byte KT-EX9 reader.
"""

from __future__ import annotations

import csv
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Callable, Deque, Dict, List, Optional

from .imu_reader import ImuSample, parse_frame

COMBINED_HEADER = b"\xa5\x5a"
COMBINED_VERSION = 1
COMBINED_PACKET_SIZE = 63
EMBEDDED_IMU_OFFSET = 24
EMBEDDED_IMU_SIZE = 37
UINT32_MODULUS = 1 << 32


class PacketFlag(IntFlag):
    IMU_VALID = 1 << 0
    ENCODER_VALID = 1 << 1
    ENCODER_ERROR = 1 << 2
    ENCODER_PARITY_ERROR = 1 << 3
    IMU_COUNTER_GAP = 1 << 4
    IMU_QUEUE_OVERFLOW = 1 << 5
    PC_TX_QUEUE_OVERFLOW = 1 << 6


class CombinedPacketError(ValueError):
    """A complete 63-byte candidate violates the v1 protocol."""


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly=0x1021, init=0xffff, no reflection/xor."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def signed_u32_delta(earlier: int, later: int) -> int:
    """Return ``later-earlier`` in the signed modular uint32 domain."""
    return ((int(later) - int(earlier) + (1 << 31)) % UINT32_MODULUS) - (1 << 31)


class UInt32MicrosUnwrapper:
    """Expand a wrapping uint32 microsecond clock into a monotonic integer."""

    def __init__(self) -> None:
        self._last_raw: Optional[int] = None
        self._wrap_offset = 0
        self.wraps = 0
        self.regressions = 0

    def reset(self) -> None:
        self._last_raw = None
        self._wrap_offset = 0
        self.wraps = 0
        self.regressions = 0

    def feed(self, raw_us: int) -> int:
        raw_us = int(raw_us) & 0xFFFF_FFFF
        if self._last_raw is not None and raw_us < self._last_raw:
            if self._last_raw >= 0xF000_0000 and raw_us <= 0x0FFF_FFFF:
                self._wrap_offset += UINT32_MODULUS
                self.wraps += 1
            else:
                self.regressions += 1
                raise CombinedPacketError(
                    f"MCU timestamp regression: {self._last_raw} -> {raw_us}"
                )
        self._last_raw = raw_us
        return self._wrap_offset + raw_us


class McuTimeMapper:
    """Map unwrapped MCU microseconds into the PC monotonic time domain.

    The first packet establishes phase.  The MCU's microsecond scale supplies
    inter-sample timing, so USB batching cannot collapse several samples onto
    one host-arrival timestamp.  A long-window centered fit estimates clock
    scale without letting an individual USB burst step timestamps backwards.
    """

    def __init__(self, phase_window: int = 2048, fit_every: int = 400) -> None:
        self._phase: Optional[float] = None
        self._last_output: Optional[float] = None
        self._last_unwrapped_us: Optional[int] = None
        self._scale = 1e-6
        self._fit_every = int(fit_every)
        self._samples_since_fit = 0
        self._samples: Deque[tuple[int, float]] = deque(maxlen=phase_window)
        self._phases: Deque[float] = deque(maxlen=phase_window)

    def reset(self) -> None:
        self._phase = None
        self._last_output = None
        self._last_unwrapped_us = None
        self._scale = 1e-6
        self._samples_since_fit = 0
        self._samples.clear()
        self._phases.clear()

    def feed(self, unwrapped_us: int, arrival_monotonic: float) -> float:
        observed_phase = float(arrival_monotonic) - float(unwrapped_us) * 1e-6
        self._phases.append(observed_phase)
        self._samples.append((int(unwrapped_us), float(arrival_monotonic)))
        self._samples_since_fit += 1
        if len(self._samples) >= 400 and self._samples_since_fit >= self._fit_every:
            self._fit_scale()
            self._samples_since_fit = 0
        if self._phase is None:
            self._phase = observed_phase
        if self._last_output is None or self._last_unwrapped_us is None:
            mapped = self._phase + float(unwrapped_us) * self._scale
        else:
            delta_us = int(unwrapped_us) - self._last_unwrapped_us
            if delta_us <= 0:
                raise CombinedPacketError("unwrapped MCU timestamp is not increasing")
            mapped = self._last_output + delta_us * self._scale
            mapped = max(mapped, self._last_output + 1e-9)
        self._last_unwrapped_us = int(unwrapped_us)
        self._last_output = mapped
        return mapped

    def _fit_scale(self) -> None:
        x0, y0 = self._samples[0]
        xs = [float(x - x0) for x, _ in self._samples]
        ys = [float(y - y0) for _, y in self._samples]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        variance = sum((x - mean_x) ** 2 for x in xs)
        if variance <= 0.0:
            return
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance
        # A value outside ±5000 ppm is a transport/reset fault, not plausible
        # oscillator drift.  Keep the last accepted mapping in that case.
        if 0.995e-6 <= slope <= 1.005e-6:
            self._scale = slope

    @property
    def arrival_phase_span_ms(self) -> float:
        """Observed arrival-phase span; includes transport jitter and clock drift."""
        if len(self._phases) < 2:
            return 0.0
        return (max(self._phases) - min(self._phases)) * 1000.0

    @property
    def clock_scale_ppm(self) -> float:
        return (self._scale / 1e-6 - 1.0) * 1e6


@dataclass
class CombinedPacket:
    sequence: int
    flags: PacketFlag
    imu_first_byte_rx_us: int
    encoder_read_us: int
    imu_counter: int
    encoder_response: int
    encoder_raw: int
    encoder_angle_deg: float
    sensor_gap_us: int
    encoder_ts: float
    imu: ImuSample
    arrival_monotonic: float
    arrival_wall: Optional[float]
    raw: bytes


def parse_combined_packet(
    raw: bytes,
    *,
    arrival_monotonic: Optional[float] = None,
    arrival_wall: Optional[float] = None,
) -> CombinedPacket:
    """Validate and decode one complete v1 packet."""
    if len(raw) != COMBINED_PACKET_SIZE:
        raise CombinedPacketError(
            f"length must be {COMBINED_PACKET_SIZE}, got {len(raw)}"
        )
    if raw[:2] != COMBINED_HEADER:
        raise CombinedPacketError("header mismatch")
    if raw[2] != COMBINED_VERSION:
        raise CombinedPacketError(f"version mismatch: {raw[2]}")
    if raw[3] != COMBINED_PACKET_SIZE:
        raise CombinedPacketError(f"length field mismatch: {raw[3]}")
    expected_crc = struct.unpack_from("<H", raw, 61)[0]
    actual_crc = crc16_ccitt_false(raw[:61])
    if actual_crc != expected_crc:
        raise CombinedPacketError(
            f"CRC mismatch: expected 0x{expected_crc:04x}, calculated 0x{actual_crc:04x}"
        )

    flags, sequence, imu_us, encoder_us, imu_counter = struct.unpack_from(
        "<HIIII", raw, 4
    )
    encoder_response = struct.unpack_from("<H", raw, 22)[0]
    if not flags & PacketFlag.IMU_VALID:
        raise CombinedPacketError("IMU_VALID flag is not set")
    embedded = raw[EMBEDDED_IMU_OFFSET:EMBEDDED_IMU_OFFSET + EMBEDDED_IMU_SIZE]
    imu = parse_frame(embedded)
    if imu is None:
        raise CombinedPacketError("embedded IMU frame is invalid")
    if imu.counter != imu_counter:
        raise CombinedPacketError(
            f"embedded IMU counter mismatch: {imu.counter} != {imu_counter}"
        )

    if arrival_monotonic is None:
        arrival_monotonic = time.monotonic()
    imu.ts = float(arrival_monotonic)
    imu.rx_time = float(arrival_monotonic)
    encoder_raw = encoder_response & 0x3FFF
    return CombinedPacket(
        sequence=sequence,
        flags=PacketFlag(flags),
        imu_first_byte_rx_us=imu_us,
        encoder_read_us=encoder_us,
        imu_counter=imu_counter,
        encoder_response=encoder_response,
        encoder_raw=encoder_raw,
        encoder_angle_deg=encoder_raw * 360.0 / 16384.0,
        sensor_gap_us=signed_u32_delta(imu_us, encoder_us),
        encoder_ts=float(arrival_monotonic) + signed_u32_delta(imu_us, encoder_us) * 1e-6,
        imu=imu,
        arrival_monotonic=float(arrival_monotonic),
        arrival_wall=arrival_wall,
        raw=raw,
    )


class CombinedPacketStream:
    """Incremental byte-stream decoder with a bounded drop-oldest queue."""

    def __init__(self, queue_capacity: int = 1024) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        self._buffer = bytearray()
        self._queue: Deque[CombinedPacket] = deque()
        self._capacity = int(queue_capacity)
        self._imu_unwrapper = UInt32MicrosUnwrapper()
        self._encoder_unwrapper = UInt32MicrosUnwrapper()
        self._clock = McuTimeMapper()
        self._last_sequence: Optional[int] = None
        self.stats: Dict[str, int] = {
            "packets_ok": 0,
            "crc_errors": 0,
            "protocol_errors": 0,
            "resync_bytes": 0,
            "sequence_gaps": 0,
            "pc_queue_overflow": 0,
            "firmware_imu_counter_gap": 0,
            "firmware_imu_queue_overflow": 0,
            "firmware_pc_tx_queue_overflow": 0,
            "encoder_errors": 0,
            "encoder_parity_errors": 0,
        }

    def feed(
        self,
        data: bytes,
        *,
        arrival_monotonic: Optional[float] = None,
        arrival_wall: Optional[float] = None,
    ) -> int:
        if arrival_monotonic is None:
            arrival_monotonic = time.monotonic()
        if arrival_wall is None:
            arrival_wall = time.time()
        self._buffer.extend(data)
        emitted = 0
        while True:
            start = self._buffer.find(COMBINED_HEADER)
            if start < 0:
                keep = 1 if self._buffer.endswith(COMBINED_HEADER[:1]) else 0
                discard = len(self._buffer) - keep
                self.stats["resync_bytes"] += discard
                if discard:
                    del self._buffer[:discard]
                break
            if start:
                self.stats["resync_bytes"] += start
                del self._buffer[:start]
            if len(self._buffer) < COMBINED_PACKET_SIZE:
                break
            candidate = bytes(self._buffer[:COMBINED_PACKET_SIZE])
            try:
                packet = parse_combined_packet(
                    candidate,
                    arrival_monotonic=arrival_monotonic,
                    arrival_wall=arrival_wall,
                )
                imu_unwrapped = self._imu_unwrapper.feed(packet.imu_first_byte_rx_us)
                self._encoder_unwrapper.feed(packet.encoder_read_us)
            except CombinedPacketError as exc:
                if "CRC" in str(exc):
                    self.stats["crc_errors"] += 1
                else:
                    self.stats["protocol_errors"] += 1
                del self._buffer[0]
                self.stats["resync_bytes"] += 1
                continue

            del self._buffer[:COMBINED_PACKET_SIZE]
            packet.imu.ts = self._clock.feed(imu_unwrapped, arrival_monotonic)
            packet.encoder_ts = packet.imu.ts + packet.sensor_gap_us * 1e-6
            self._update_stats(packet)
            if len(self._queue) >= self._capacity:
                self._queue.popleft()
                self.stats["pc_queue_overflow"] += 1
            self._queue.append(packet)
            self.stats["packets_ok"] += 1
            emitted += 1
        return emitted

    def _update_stats(self, packet: CombinedPacket) -> None:
        if self._last_sequence is not None:
            delta = (packet.sequence - self._last_sequence) & 0xFFFF_FFFF
            if delta > 1:
                self.stats["sequence_gaps"] += delta - 1
        self._last_sequence = packet.sequence
        mapping = (
            (PacketFlag.IMU_COUNTER_GAP, "firmware_imu_counter_gap"),
            (PacketFlag.IMU_QUEUE_OVERFLOW, "firmware_imu_queue_overflow"),
            (PacketFlag.PC_TX_QUEUE_OVERFLOW, "firmware_pc_tx_queue_overflow"),
            (PacketFlag.ENCODER_ERROR, "encoder_errors"),
            (PacketFlag.ENCODER_PARITY_ERROR, "encoder_parity_errors"),
        )
        for flag, name in mapping:
            if packet.flags & flag:
                self.stats[name] += 1

    def drain(self) -> List[CombinedPacket]:
        packets = list(self._queue)
        self._queue.clear()
        return packets

    @property
    def arrival_phase_span_ms(self) -> float:
        return self._clock.arrival_phase_span_ms


class CombinedPacketRecorder:
    """Write lossless packet bytes plus an encoder/timing CSV sidecar."""

    CSV_NAME = "encoder_ts.csv"
    RAW_NAME = "imu_encoder_packets.bin"

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)
        self._raw_fp = None
        self._csv_fp = None
        self._csv_writer = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._raw_fp = open(self.out_dir / self.RAW_NAME, "wb")
        self._csv_fp = open(
            self.out_dir / self.CSV_NAME, "w", newline="", encoding="utf-8"
        )
        self._csv_writer = csv.writer(self._csv_fp)
        self._csv_writer.writerow([
            "sequence", "flags", "imu_counter", "imu_first_byte_rx_us",
            "encoder_read_us", "sensor_gap_us", "encoder_response",
            "encoder_raw", "encoder_angle_deg", "imu_ts_mono",
            "encoder_ts_mono", "arrival_mono", "arrival_wall",
        ])

    def put(self, packet: CombinedPacket) -> None:
        if self._raw_fp is None or self._csv_writer is None:
            raise RuntimeError("CombinedPacketRecorder is not started")
        with self._lock:
            self._raw_fp.write(packet.raw)
            self._csv_writer.writerow([
                packet.sequence,
                int(packet.flags),
                packet.imu_counter,
                packet.imu_first_byte_rx_us,
                packet.encoder_read_us,
                packet.sensor_gap_us,
                packet.encoder_response,
                packet.encoder_raw,
                f"{packet.encoder_angle_deg:.9f}",
                f"{packet.imu.ts:.9f}",
                f"{packet.encoder_ts:.9f}",
                f"{packet.arrival_monotonic:.9f}",
                "" if packet.arrival_wall is None else f"{packet.arrival_wall:.9f}",
            ])

    def stop(self) -> None:
        with self._lock:
            for fp in (self._raw_fp, self._csv_fp):
                if fp is not None:
                    fp.flush()
                    fp.close()
            self._raw_fp = None
            self._csv_fp = None
            self._csv_writer = None


class CombinedImuEncoderReader:
    """Serial reader for the 63-byte protocol with legacy IMU callbacks.

    Parsing and callback delivery use separate threads.  The delivery queue is
    bounded and drops its oldest item on overflow, preventing a slow recorder
    or ROS publisher from growing memory without bound.
    """

    def __init__(
        self,
        port: str,
        baud: int = 921600,
        on_sample: Optional[Callable[[ImuSample], None]] = None,
        on_packet: Optional[Callable[[CombinedPacket], None]] = None,
        name: str = "imu_encoder",
        warmup_frames: int = 500,
        queue_capacity: int = 2048,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        self.port = port
        self.baud = baud
        self.on_sample = on_sample
        self.on_packet = on_packet
        self.name = name
        self._warmup_frames = max(0, int(warmup_frames))
        self._warmup_remaining = self._warmup_frames
        self._queue_capacity = int(queue_capacity)
        self._delivery: Queue = Queue(maxsize=self._queue_capacity)
        self._stream = CombinedPacketStream(queue_capacity=self._queue_capacity)
        self._ser = None
        self._running = False
        self._read_thread: Optional[threading.Thread] = None
        self._delivery_thread: Optional[threading.Thread] = None
        self.serial_errors = 0
        self.callback_errors = 0
        self.delivery_queue_overflow = 0

    def _open_port(self) -> bool:
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("需要 pyserial: pip install pyserial") from exc
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0.01)
            self._ser.reset_input_buffer()
            return True
        except Exception as exc:
            print(f"[{self.name}] 打开 {self.port} 失败: {exc}")
            return False

    def start(self) -> bool:
        if not self._open_port():
            return False
        self._stream = CombinedPacketStream(queue_capacity=self._queue_capacity)
        self._delivery = Queue(maxsize=self._queue_capacity)
        self._warmup_remaining = self._warmup_frames
        self._running = True
        self._read_thread = threading.Thread(
            target=self._read_loop, name=f"combined-read-{self.name}", daemon=True
        )
        self._delivery_thread = threading.Thread(
            target=self._delivery_loop, name=f"combined-deliver-{self.name}", daemon=True
        )
        self._delivery_thread.start()
        self._read_thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        if self._read_thread is not None:
            self._read_thread.join(timeout=2.0)
        if self._delivery_thread is not None:
            self._delivery_thread.join(timeout=2.0)

    def _read_loop(self) -> None:
        while self._running and self._ser is not None:
            try:
                waiting = self._ser.in_waiting
                data = self._ser.read(waiting if waiting > 0 else 1)
            except Exception as exc:
                self.serial_errors += 1
                print(f"[{self.name}] 联合串口读取失败: {exc}")
                self._running = False
                break
            if data:
                self._consume_bytes(data)

    def _consume_bytes(
        self,
        data: bytes,
        *,
        arrival_monotonic: Optional[float] = None,
        arrival_wall: Optional[float] = None,
    ) -> None:
        self._stream.feed(
            data,
            arrival_monotonic=arrival_monotonic,
            arrival_wall=arrival_wall,
        )
        for packet in self._stream.drain():
            try:
                self._delivery.put_nowait(packet)
            except Full:
                try:
                    self._delivery.get_nowait()
                except Empty:
                    pass
                self.delivery_queue_overflow += 1
                self._delivery.put_nowait(packet)

    def feed_bytes_for_test(
        self,
        data: bytes,
        *,
        arrival_monotonic: Optional[float] = None,
        arrival_wall: Optional[float] = None,
    ) -> None:
        """Deterministically exercise decoding/callbacks without a serial port."""
        self._stream.feed(
            data,
            arrival_monotonic=arrival_monotonic,
            arrival_wall=arrival_wall,
        )
        for packet in self._stream.drain():
            self._deliver(packet)

    def _delivery_loop(self) -> None:
        while self._running or not self._delivery.empty():
            try:
                packet = self._delivery.get(timeout=0.05)
            except Empty:
                continue
            self._deliver(packet)

    def _deliver(self, packet: CombinedPacket) -> None:
        if self._warmup_remaining > 0:
            self._warmup_remaining -= 1
            return
        try:
            if self.on_packet is not None:
                self.on_packet(packet)
            if self.on_sample is not None:
                self.on_sample(packet.imu)
        except Exception:
            self.callback_errors += 1

    def stats(self) -> Dict[str, object]:
        values: Dict[str, object] = dict(self._stream.stats)
        values.update({
            "serial_errors": self.serial_errors,
            "callback_errors": self.callback_errors,
            "delivery_queue_overflow": self.delivery_queue_overflow,
            "delivery_queue_depth": self._delivery.qsize(),
            "serial_connected": self._ser is not None,
            "mcu_wraps": self._stream._imu_unwrapper.wraps,
            "arrival_phase_span_ms": self._stream.arrival_phase_span_ms,
            "mcu_clock_scale_ppm": self._stream._clock.clock_scale_ppm,
        })
        return values
