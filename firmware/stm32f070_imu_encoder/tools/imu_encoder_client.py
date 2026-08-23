from __future__ import annotations

import dataclasses
import queue
import threading
from collections.abc import Callable
from typing import Any

try:
    from .imu_encoder_protocol import CombinedSample, PacketParser
except ImportError:
    from imu_encoder_protocol import CombinedSample, PacketParser


DEFAULT_BAUDRATE = 921600
DEFAULT_QUEUE_SIZE = 2048
_CLOSED = object()


class ImuEncoderClientError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class ClientStats:
    frames_received: int
    consumer_queue_drops: int
    crc_errors: int
    discarded_bytes: int
    last_error: str | None


def _open_pyserial(**kwargs: Any) -> object:
    try:
        import serial
    except ImportError as exc:
        raise ImuEncoderClientError(
            "pyserial is required: pip install pyserial"
        ) from exc
    return serial.Serial(**kwargs)


class ImuEncoderClient:
    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        serial_factory: Callable[..., object] | None = None,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self.port = port
        self.baudrate = baudrate
        self._serial_factory = serial_factory or _open_pyserial
        self._samples: queue.Queue[CombinedSample | object] = queue.Queue(
            maxsize=queue_size
        )
        self._parser = PacketParser()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._serial: object | None = None
        self._thread: threading.Thread | None = None
        self._latest: CombinedSample | None = None
        self._frames_received = 0
        self._consumer_queue_drops = 0
        self._receive_error: Exception | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if self._serial is not None:
            self.close()
        self._reset_session()
        self._stop.clear()
        try:
            self._serial = self._serial_factory(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=0.1,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
        except ImuEncoderClientError:
            raise
        except Exception as exc:
            raise ImuEncoderClientError(
                f"cannot open serial port {self.port}: {exc}"
            ) from exc
        self._thread = threading.Thread(
            target=self._receive_loop,
            name=f"imu-encoder-{self.port}",
            daemon=True,
        )
        self._thread.start()

    def read(self, timeout: float | None = None) -> CombinedSample | None:
        if self._samples.empty():
            self._raise_receive_error()
            if self._stop.is_set():
                return None
        try:
            item = self._samples.get(timeout=timeout)
        except queue.Empty:
            self._raise_receive_error()
            return None
        if item is _CLOSED:
            self._raise_receive_error()
            return None
        return item

    def latest(self) -> CombinedSample | None:
        with self._lock:
            return self._latest

    @property
    def stats(self) -> ClientStats:
        with self._lock:
            return ClientStats(
                frames_received=self._frames_received,
                consumer_queue_drops=self._consumer_queue_drops,
                crc_errors=self._parser.crc_errors,
                discarded_bytes=self._parser.discarded_bytes,
                last_error=str(self._receive_error) if self._receive_error else None,
            )

    def close(self) -> None:
        self._stop.set()
        serial_port = self._serial
        self._serial = None
        if serial_port is not None:
            try:
                serial_port.close()
            except Exception:
                pass
        self._wake_reader()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def __enter__(self) -> ImuEncoderClient:
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def _receive_loop(self) -> None:
        try:
            while not self._stop.is_set():
                serial_port = self._serial
                if serial_port is None:
                    break
                waiting = int(getattr(serial_port, "in_waiting", 0) or 0)
                chunk = serial_port.read(min(max(waiting, 1), 4096))
                if not chunk:
                    continue
                for sample in self._parser.feed(chunk):
                    self._publish(sample)
        except Exception as exc:
            if not self._stop.is_set():
                with self._lock:
                    self._receive_error = exc
                self._wake_reader()
        finally:
            self._stop.set()

    def _publish(self, sample: CombinedSample) -> None:
        with self._lock:
            self._latest = sample
            self._frames_received += 1
        try:
            self._samples.put_nowait(sample)
            return
        except queue.Full:
            pass
        try:
            self._samples.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            self._consumer_queue_drops += 1
        self._samples.put_nowait(sample)

    def _wake_reader(self) -> None:
        try:
            self._samples.put_nowait(_CLOSED)
        except queue.Full:
            pass

    def _raise_receive_error(self) -> None:
        with self._lock:
            error = self._receive_error
        if error is not None:
            raise ImuEncoderClientError(f"serial receive failed: {error}") from error

    def _reset_session(self) -> None:
        while True:
            try:
                self._samples.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._parser = PacketParser()
            self._latest = None
            self._frames_received = 0
            self._consumer_queue_drops = 0
            self._receive_error = None
