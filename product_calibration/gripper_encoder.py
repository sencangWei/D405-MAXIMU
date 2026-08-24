"""App-neutral UMI gripper encoder acquisition and recording.

`GripperEncoderProcessor` is the product integration boundary when another
process already owns the STM32 serial stream. `GripperEncoderCollector` owns a
serial port and is therefore only for standalone use.
"""

from __future__ import annotations

import glob
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Callable, TextIO

from .imu_stream import ImuPacket, StreamDecoder, TimerUnwrapper
from .manual_gripper import ManualGripperCalibration, ManualGripperTracker


SAMPLE_SCHEMA = "umi_gripper_sample_v1"
HEALTH_SCHEMA = "umi_gripper_health_v1"
DEFAULT_PROFILE = Path(__file__).with_name("umi_manual_gripper_20260824.yaml")

FLAG_ENCODER_VALID = 1 << 1
FLAG_ENCODER_ERROR = 1 << 2
FLAG_ENCODER_PARITY_ERROR = 1 << 3
FLAG_IMU_QUEUE_OVERFLOW = 1 << 5
FLAG_PC_TX_QUEUE_OVERFLOW = 1 << 6


@dataclass(frozen=True)
class GripperSample:
    schema: str
    calibration_id: str
    protocol: str
    sequence: int
    imu_counter: int
    device_time_us: int
    sensor_pair_delta_us: int
    host_monotonic_ns: int
    raw_flags: int
    raw_count: int
    angle_deg: float
    direction: str
    closure_ratio: float | None
    estimated_no_load_gap_mm: float | None
    no_load_uncertainty_mm: float | None
    dual_closing_distance_mm: float | None
    single_jaw_travel_mm: float | None
    loaded_object_size_valid: bool
    valid: bool
    status: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GripperHealth:
    schema: str
    state: str
    port: str
    baud: int
    calibration_id: str
    frames: int
    valid_frames: int
    invalid_frames: int
    rate_hz: float
    sequence_gaps: int
    device_time_regressions: int
    crc_errors: int
    discarded_bytes: int
    device_queue_overflow_flags: int
    callback_queue_drops: int
    callback_errors: int
    serial_errors: int
    last_sample_age_ms: float | None
    last_error_code: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class GripperEncoderProcessor:
    """Convert parsed STM32 combined packets into the stable App sample."""

    def __init__(self, calibration: ManualGripperCalibration) -> None:
        self.calibration = calibration
        self.reset()

    @classmethod
    def from_profile(cls, path: Path = DEFAULT_PROFILE) -> "GripperEncoderProcessor":
        return cls(ManualGripperCalibration.load(Path(path)))

    def reset(self) -> None:
        self._tracker = ManualGripperTracker(self.calibration)
        self._encoder_timer = TimerUnwrapper()

    def process(
        self, packet: ImuPacket, *, host_monotonic_ns: int | None = None
    ) -> GripperSample:
        if packet.protocol != "stm32_combined_v1":
            raise ValueError("gripper encoder requires stm32_combined_v1 packets")
        if packet.sequence is None or packet.encoder_read_us is None or packet.encoder_response is None:
            raise ValueError("combined packet is missing encoder metadata")
        host_ns = time.monotonic_ns() if host_monotonic_ns is None else int(host_monotonic_ns)
        raw_count = int(packet.encoder_response) & 0x3FFF
        angle_deg = raw_count * 360.0 / 16384.0
        encoder_valid = bool(packet.flags & FLAG_ENCODER_VALID) and not bool(
            packet.flags & (FLAG_ENCODER_ERROR | FLAG_ENCODER_PARITY_ERROR)
        )
        state = self._tracker.update(angle_deg, encoder_valid=encoder_valid)
        device_time_us = self._encoder_timer.extend(int(packet.encoder_read_us))
        pair_delta_us = _signed_uint32_delta(
            int(packet.encoder_read_us), int(packet.imu_first_byte_rx_us or 0)
        )
        status = (
            "ENCODER_INVALID"
            if not encoder_valid
            else "DIRECTION_UNKNOWN" if state.direction == "unknown" else "OK"
        )
        return GripperSample(
            schema=SAMPLE_SCHEMA,
            calibration_id=self.calibration.profile_id,
            protocol=packet.protocol,
            sequence=int(packet.sequence),
            imu_counter=int(packet.counter),
            device_time_us=device_time_us,
            sensor_pair_delta_us=pair_delta_us,
            host_monotonic_ns=host_ns,
            raw_flags=int(packet.flags),
            raw_count=raw_count,
            angle_deg=angle_deg,
            direction=state.direction,
            closure_ratio=state.closure_ratio,
            estimated_no_load_gap_mm=state.estimated_no_load_gap_mm,
            no_load_uncertainty_mm=state.no_load_uncertainty_mm,
            dual_closing_distance_mm=state.dual_closing_distance_mm,
            single_jaw_travel_mm=state.single_jaw_travel_mm,
            loaded_object_size_valid=False,
            valid=encoder_valid,
            status=status,
        )


def _signed_uint32_delta(current: int, previous: int) -> int:
    delta = (int(current) - int(previous)) & 0xFFFFFFFF
    return delta - (1 << 32) if delta >= (1 << 31) else delta


def resolve_serial_port(requested: str | None = None) -> str:
    if requested:
        return requested
    candidates = sorted(glob.glob("/dev/serial/by-id/*"))
    if len(candidates) != 1:
        raise RuntimeError(f"需要唯一串口或显式port；当前找到{len(candidates)}个")
    return candidates[0]


class GripperEncoderCollector:
    """Standalone serial owner with latest-sample and bounded callback APIs."""

    def __init__(
        self,
        *,
        port: str | None = None,
        baud: int = 921600,
        profile: Path = DEFAULT_PROFILE,
        on_sample: Callable[[GripperSample], None] | None = None,
        callback_queue_size: int = 256,
        serial_factory=None,
    ) -> None:
        if baud <= 0:
            raise ValueError("baud must be positive")
        if callback_queue_size <= 0:
            raise ValueError("callback_queue_size must be positive")
        self.port = resolve_serial_port(port)
        self.baud = int(baud)
        self.profile = Path(profile)
        self.on_sample = on_sample
        self.callback_queue_size = int(callback_queue_size)
        self.serial_factory = serial_factory
        self.processor = GripperEncoderProcessor.from_profile(self.profile)
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._callback_queue: Queue[GripperSample] = Queue(maxsize=self.callback_queue_size)
        self._decoder = StreamDecoder("stm32_combined_v1")
        self._serial = None
        self._reader_thread: threading.Thread | None = None
        self._callback_thread: threading.Thread | None = None
        self._latest: GripperSample | None = None
        self._reset_stats()

    def _reset_stats(self) -> None:
        self._state = "STOPPED"
        self._frames = 0
        self._valid_frames = 0
        self._invalid_frames = 0
        self._sequence_gaps = 0
        self._device_time_regressions = 0
        self._device_queue_overflows = 0
        self._callback_queue_drops = 0
        self._callback_errors = 0
        self._serial_errors = 0
        self._first_sample_ns: int | None = None
        self._last_sample_ns: int | None = None
        self._last_sequence: int | None = None
        self._last_device_time_us: int | None = None
        self._last_error_code: str | None = None

    def start(self) -> "GripperEncoderCollector":
        with self._lock:
            if self._state == "RUNNING":
                raise RuntimeError("collector is already running")
            self._reset_stats()
            self._latest = None
            self._decoder = StreamDecoder("stm32_combined_v1")
            self.processor.reset()
            self._callback_queue = Queue(maxsize=self.callback_queue_size)
            self._stop_event.clear()
        factory = self.serial_factory
        if factory is None:
            try:
                import serial
            except ImportError as exc:
                raise RuntimeError("缺少pyserial") from exc
            factory = serial.Serial
        try:
            self._serial = factory(self.port, baudrate=self.baud, timeout=0.05)
            if hasattr(self._serial, "reset_input_buffer"):
                self._serial.reset_input_buffer()
        except Exception as exc:
            with self._lock:
                self._serial_errors += 1
                self._state = "FAULT"
                self._last_error_code = "SERIAL_OPEN_ERROR"
            raise RuntimeError(f"无法打开编码器串口 {self.port}: {exc}") from exc
        with self._lock:
            self._state = "RUNNING"
        if self.on_sample is not None:
            self._callback_thread = threading.Thread(
                target=self._callback_loop, name="gripper-app-callback", daemon=True
            )
            self._callback_thread.start()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="gripper-serial-reader", daemon=True
        )
        self._reader_thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
        if self._callback_thread is not None:
            self._callback_thread.join(timeout=2.0)
        with self._lock:
            if self._state != "FAULT":
                self._state = "STOPPED"

    def __enter__(self) -> "GripperEncoderCollector":
        return self.start()

    def __exit__(self, *_args) -> None:
        self.stop()

    def latest(self) -> GripperSample | None:
        with self._lock:
            return self._latest

    def wait_for_sample(self, timeout: float | None = None) -> GripperSample | None:
        with self._condition:
            self._condition.wait_for(lambda: self._latest is not None, timeout=timeout)
            return self._latest

    def health(self) -> GripperHealth:
        now_ns = time.monotonic_ns()
        with self._lock:
            duration_s = (
                (self._last_sample_ns - self._first_sample_ns) / 1e9
                if self._first_sample_ns is not None and self._last_sample_ns is not None
                else 0.0
            )
            rate_hz = (self._frames - 1) / duration_s if duration_s > 0 and self._frames > 1 else 0.0
            age_ms = (
                (now_ns - self._last_sample_ns) / 1e6
                if self._last_sample_ns is not None
                else None
            )
            return GripperHealth(
                schema=HEALTH_SCHEMA,
                state=self._state,
                port=self.port,
                baud=self.baud,
                calibration_id=self.processor.calibration.profile_id,
                frames=self._frames,
                valid_frames=self._valid_frames,
                invalid_frames=self._invalid_frames,
                rate_hz=rate_hz,
                sequence_gaps=self._sequence_gaps,
                device_time_regressions=self._device_time_regressions,
                crc_errors=self._decoder.crc_or_checksum_errors,
                discarded_bytes=self._decoder.discarded_bytes,
                device_queue_overflow_flags=self._device_queue_overflows,
                callback_queue_drops=self._callback_queue_drops,
                callback_errors=self._callback_errors,
                serial_errors=self._serial_errors,
                last_sample_age_ms=age_ms,
                last_error_code=self._last_error_code,
            )

    def _reader_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                waiting = int(getattr(self._serial, "in_waiting", 0) or 0)
                data = self._serial.read(waiting if waiting > 0 else 1)
                if not data:
                    continue
                for packet in self._decoder.feed(data):
                    sample = self.processor.process(packet)
                    self._publish(sample, packet.flags)
        except Exception:
            if not self._stop_event.is_set():
                with self._lock:
                    self._serial_errors += 1
                    self._state = "FAULT"
                    self._last_error_code = "SERIAL_READ_ERROR"

    def _publish(self, sample: GripperSample, raw_flags: int) -> None:
        with self._condition:
            if self._last_sequence is not None and (
                (sample.sequence - self._last_sequence) & 0xFFFFFFFF
            ) != 1:
                self._sequence_gaps += 1
            self._last_sequence = sample.sequence
            if (
                self._last_device_time_us is not None
                and sample.device_time_us <= self._last_device_time_us
            ):
                self._device_time_regressions += 1
            self._last_device_time_us = sample.device_time_us
            self._frames += 1
            self._valid_frames += int(sample.valid)
            self._invalid_frames += int(not sample.valid)
            self._device_queue_overflows += int(
                bool(raw_flags & (FLAG_IMU_QUEUE_OVERFLOW | FLAG_PC_TX_QUEUE_OVERFLOW))
            )
            self._first_sample_ns = (
                sample.host_monotonic_ns if self._first_sample_ns is None else self._first_sample_ns
            )
            self._last_sample_ns = sample.host_monotonic_ns
            self._latest = sample
            self._condition.notify_all()
        if self.on_sample is not None:
            try:
                self._callback_queue.put_nowait(sample)
            except Full:
                try:
                    self._callback_queue.get_nowait()
                except Empty:
                    pass
                with self._lock:
                    self._callback_queue_drops += 1
                self._callback_queue.put_nowait(sample)

    def _callback_loop(self) -> None:
        while not self._stop_event.is_set() or not self._callback_queue.empty():
            try:
                sample = self._callback_queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                self.on_sample(sample)
            except Exception:
                with self._lock:
                    self._callback_errors += 1


class JsonlSampleRecorder:
    """Thread-safe line-delimited JSON recorder for all App sample fields."""

    def __init__(self, path: Path, *, flush_every: int = 100) -> None:
        if flush_every <= 0:
            raise ValueError("flush_every must be positive")
        self.path = Path(path)
        self.flush_every = int(flush_every)
        self._stream: TextIO | None = None
        self._count = 0
        self._lock = threading.Lock()

    def __enter__(self) -> "JsonlSampleRecorder":
        self.open()
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def open(self) -> None:
        if self._stream is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", encoding="utf-8")

    def write(self, sample: GripperSample) -> None:
        with self._lock:
            if self._stream is None:
                self.open()
            self._stream.write(json.dumps(sample.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
            self._count += 1
            if self._count % self.flush_every == 0:
                self._stream.flush()

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.flush()
                self._stream.close()
                self._stream = None
