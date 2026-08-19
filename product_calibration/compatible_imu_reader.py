"""Drop-in ImuReader for legacy collectors, accepting KT-EX9 37B or STM32 63B."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .imu_stream import StreamDecoder, TimerUnwrapper, _counter_gap


@dataclass
class ImuSample:
    ts: float
    counter: int
    gx: float
    gy: float
    gz: float
    ax: float
    ay: float
    az: float
    temp: float
    rx_time: float


class CompatibleImuReader:
    """Legacy collector interface with auto-resyncing dual-protocol parsing."""

    def __init__(self, port: str, baud: int = 921600,
                 on_sample: Callable[[ImuSample], None] | None = None,
                 name: str = "imu", warmup_frames: int = 500,
                 protocol: str = "auto") -> None:
        self.port = port
        self.baud = baud
        self.on_sample = on_sample
        self.name = name
        self.protocol = protocol
        self._warmup_frames = max(0, int(warmup_frames))
        self._warmup_remaining = self._warmup_frames
        self._decoder = StreamDecoder(protocol)
        self._mcu_timer = TimerUnwrapper()
        self._mcu_to_host_offset = None
        self._ser = None
        self._thread = None
        self._running = False
        self._first_rx = None
        self._last_rx = None
        self.last_counter = None
        self.last_sequence = None
        self.frames_ok = 0
        self.frames_bad = 0
        self.resyncs = 0
        self.dropped_frames = 0
        self.sequence_gaps = 0
        self.counter_resets = 0
        self.counter_stalls = 0
        self.invalid_imu_flags = 0
        self.queue_overflow_flags = 0
        self.serial_errors = 0
        self.serial_reconnects = 0
        self.recent_dt = deque(maxlen=400)

    def _open_port(self) -> bool:
        try:
            import serial
            self._ser = serial.Serial(self.port, self.baud, timeout=0.01)
            self._ser.reset_input_buffer()
            return True
        except Exception as exc:
            print(f"[{self.name}] 打开 {self.port} 失败: {exc}")
            self._ser = None
            return False

    def start(self) -> bool:
        if not self._open_port():
            return False
        self._running = True
        self._warmup_remaining = self._warmup_frames
        self._thread = threading.Thread(target=self._loop, name=f"imu-{self.name}", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._ser:
            self._ser.close()
            self._ser = None

    def _loop(self) -> None:
        while self._running:
            try:
                waiting = self._ser.in_waiting
                data = self._ser.read(waiting if waiting > 0 else 1)
            except Exception as exc:
                self.serial_errors += 1
                print(f"[{self.name}] 串口读取失败: {exc}")
                return
            if not data:
                continue
            for packet in self._decoder.feed(data):
                rx = time.monotonic()
                self.frames_ok += 1
                if not packet.imu_valid:
                    self.invalid_imu_flags += 1
                    continue
                if self.last_counter is not None:
                    delta = (packet.counter - self.last_counter) & 0xFFFFFFFF
                    if delta == 0:
                        self.counter_stalls += 1
                    elif packet.counter == 1 and self.last_counter != 0:
                        self.counter_resets += 1
                    elif delta != 1:
                        self.dropped_frames += max(1, delta - 1) if delta < 4096 else 1
                self.last_counter = packet.counter
                if packet.sequence is not None:
                    if _counter_gap(self.last_sequence, packet.sequence):
                        self.sequence_gaps += 1
                    self.last_sequence = packet.sequence
                if packet.flags & ((1 << 5) | (1 << 6)):
                    self.queue_overflow_flags += 1
                if self._last_rx is not None:
                    self.recent_dt.append(rx - self._last_rx)
                self._first_rx = rx if self._first_rx is None else self._first_rx
                self._last_rx = rx
                if packet.imu_first_byte_rx_us is not None:
                    device_time = self._mcu_timer.extend(packet.imu_first_byte_rx_us) / 1_000_000.0
                    if self._mcu_to_host_offset is None:
                        self._mcu_to_host_offset = rx - device_time
                    sample_time = device_time + self._mcu_to_host_offset
                else:
                    sample_time = rx
                sample = ImuSample(
                    ts=sample_time, rx_time=rx, counter=packet.counter,
                    gx=packet.gx, gy=packet.gy, gz=packet.gz,
                    ax=packet.ax, ay=packet.ay, az=packet.az,
                    temp=packet.temperature_c,
                )
                if self._warmup_remaining:
                    self._warmup_remaining -= 1
                elif self.on_sample:
                    self.on_sample(sample)
        self.frames_bad = self._decoder.crc_or_checksum_errors
        self.resyncs = self._decoder.discarded_bytes

    def stats(self) -> dict:
        duration = (self._last_rx - self._first_rx) if self._first_rx is not None and self._last_rx else 0.0
        dt_ms = [value * 1000.0 for value in self.recent_dt]
        return {
            "frames_ok": self.frames_ok,
            "frames_bad": self._decoder.crc_or_checksum_errors,
            "resyncs": self._decoder.discarded_bytes,
            "dropped_frames": self.dropped_frames,
            "sequence_gaps": self.sequence_gaps,
            "counter_resets": self.counter_resets,
            "counter_stalls": self.counter_stalls,
            "invalid_imu_flags": self.invalid_imu_flags,
            "queue_overflow_flags": self.queue_overflow_flags,
            "serial_errors": self.serial_errors,
            "serial_reconnects": self.serial_reconnects,
            "serial_connected": self._ser is not None,
            "rate_hz": (self.frames_ok - 1) / duration if duration > 0 else 0.0,
            "dt_min_ms": min(dt_ms) if dt_ms else 0.0,
            "dt_max_ms": max(dt_ms) if dt_ms else 0.0,
            "dt_jitter_ms": max(dt_ms) - min(dt_ms) if dt_ms else 0.0,
        }


# The legacy script imports this exact name.
ImuReader = CompatibleImuReader
