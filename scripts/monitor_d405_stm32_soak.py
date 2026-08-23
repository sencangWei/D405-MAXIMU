#!/usr/bin/env python3
"""Monitor D405 and STM32 continuity without recording raw sensor frames."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_CAMERA_RATE_HZ = 29.5
MAX_CAMERA_RATE_HZ = 30.5
MIN_IMU_RATE_HZ = 399.0
MAX_IMU_RATE_HZ = 401.0
CAMERA_BOUNDARY_ALLOWANCE_FRAMES = 2
MAX_CAMERA_BOUNDARY_AGE_S = CAMERA_BOUNDARY_ALLOWANCE_FRAMES / MIN_CAMERA_RATE_HZ
MAX_CAMERA_INTERARRIVAL_S = MAX_CAMERA_BOUNDARY_AGE_S
MAX_IMU_BOUNDARY_AGE_S = 0.01
MAX_IMU_INTERARRIVAL_S = 0.025
COMPLETION_TOLERANCE_S = 0.01
CAMERA_STREAMS = ("depth", "infrared_left", "infrared_right")
IMU_ZERO_ERROR_KEYS = (
    "frames_bad",
    "resyncs",
    "dropped_frames",
    "counter_resets",
    "counter_stalls",
    "sequence_gaps",
    "invalid_imu_flags",
    "queue_overflow_flags",
    "serial_errors",
    "serial_reconnects",
)


def evaluate_soak(
    camera_streams: dict[str, dict],
    imu_stats: dict,
    duration_s: float,
    requested_duration_s: float,
    capture_error: str | None,
) -> dict:
    """Apply strict zero-drop gates to a monitor-only formal window."""
    failures: list[str] = []
    if duration_s + COMPLETION_TOLERANCE_S < requested_duration_s:
        failures.append(
            f"duration {duration_s:.3f}s < requested {requested_duration_s:.3f}s"
        )
    if capture_error:
        failures.append(f"capture_error: {capture_error}")

    for name in CAMERA_STREAMS:
        stream = camera_streams.get(name)
        if not isinstance(stream, dict):
            failures.append(f"camera.{name}=missing")
            continue
        if int(stream.get("received", 0)) < 2:
            failures.append(f"camera.{name}.received={stream.get('received', 0)}")
        rate_hz = float(stream.get("rate_hz", 0.0))
        if not MIN_CAMERA_RATE_HZ <= rate_hz <= MAX_CAMERA_RATE_HZ:
            failures.append(f"camera.{name}.rate_hz={stream.get('rate_hz', 0.0)}")
        expected_min = max(
            2,
            math.floor(duration_s * MIN_CAMERA_RATE_HZ)
            - CAMERA_BOUNDARY_ALLOWANCE_FRAMES,
        )
        if int(stream.get("received", 0)) < expected_min:
            failures.append(
                f"camera.{name}.received={stream.get('received', 0)}"
                f" < expected_min={expected_min}"
            )
        for key in ("first_arrival_delay_s", "last_arrival_age_s"):
            value = stream.get(key)
            if value is None or not math.isfinite(float(value)):
                failures.append(f"camera.{name}.{key}=missing")
            elif float(value) > MAX_CAMERA_BOUNDARY_AGE_S:
                failures.append(f"camera.{name}.{key}={float(value):.6f}")
        max_interarrival = stream.get("max_host_interarrival_s")
        if max_interarrival is None or not math.isfinite(float(max_interarrival)):
            failures.append(f"camera.{name}.max_host_interarrival_s=missing")
        elif float(max_interarrival) > MAX_CAMERA_INTERARRIVAL_S:
            failures.append(
                f"camera.{name}.max_host_interarrival_s="
                f"{float(max_interarrival):.6f}"
            )
        for key in (
            "skipped_frames",
            "gap_events",
            "repeated_frames",
            "frame_number_resets",
            "timestamp_regressions",
        ):
            value = int(stream.get(key, -1))
            if value != 0:
                failures.append(f"camera.{name}.{key}={value}")

    imu_frames = int(imu_stats.get("frames_ok", 0))
    imu_rate_hz = imu_frames / duration_s if duration_s > 0.0 else 0.0
    protocol = str(imu_stats.get("protocol", "unknown"))
    if protocol != "stm32_combined_v1":
        failures.append(f"imu.protocol={protocol}")
    if not MIN_IMU_RATE_HZ <= imu_rate_hz <= MAX_IMU_RATE_HZ:
        failures.append(f"imu.rate_hz={imu_rate_hz:.6f}")
    for key in IMU_ZERO_ERROR_KEYS:
        value = int(imu_stats.get(key, -1))
        if value != 0:
            failures.append(f"imu.{key}={value}")
    imu_arrival = imu_stats.get("arrival", {})
    for key in ("first_arrival_delay_s", "last_arrival_age_s"):
        value = imu_arrival.get(key)
        if value is None or not math.isfinite(float(value)):
            failures.append(f"imu.arrival.{key}=missing")
        elif float(value) > MAX_IMU_BOUNDARY_AGE_S:
            failures.append(f"imu.arrival.{key}={float(value):.6f}")
    imu_max_interarrival = imu_arrival.get("max_host_interarrival_s")
    if imu_max_interarrival is None or not math.isfinite(float(imu_max_interarrival)):
        failures.append("imu.arrival.max_host_interarrival_s=missing")
    elif float(imu_max_interarrival) > MAX_IMU_INTERARRIVAL_S:
        failures.append(
            "imu.arrival.max_host_interarrival_s="
            f"{float(imu_max_interarrival):.6f}"
        )

    camera_failures = [item for item in failures if item.startswith("camera.")]
    imu_failures = [item for item in failures if item.startswith("imu.")]
    return {
        "result": "PASS" if not failures else "FAIL",
        "storage_mode": "monitor_only",
        "raw_frames_written": 0,
        "duration_s": duration_s,
        "requested_duration_s": requested_duration_s,
        "capture_error": capture_error,
        "thresholds": {
            "camera_min_rate_hz": MIN_CAMERA_RATE_HZ,
            "camera_max_rate_hz": MAX_CAMERA_RATE_HZ,
            "camera_max_boundary_age_s": MAX_CAMERA_BOUNDARY_AGE_S,
            "camera_max_host_interarrival_s": MAX_CAMERA_INTERARRIVAL_S,
            "camera_boundary_allowance_frames": CAMERA_BOUNDARY_ALLOWANCE_FRAMES,
            "camera_max_skipped_frames": 0,
            "camera_max_gap_events": 0,
            "imu_rate_hz": [MIN_IMU_RATE_HZ, MAX_IMU_RATE_HZ],
            "imu_max_boundary_age_s": MAX_IMU_BOUNDARY_AGE_S,
            "imu_max_host_interarrival_s": MAX_IMU_INTERARRIVAL_S,
            "imu_transport_error_counts": 0,
        },
        "camera": {
            "result": "PASS" if not camera_failures else "FAIL",
            "streams": camera_streams,
        },
        "imu": {
            "result": "PASS" if not imu_failures else "FAIL",
            "rate_hz": imu_rate_hz,
            "formal_window_stats": imu_stats,
        },
        "failures": failures,
    }


def running_checkpoint(health: dict) -> dict:
    """Mark a partial report as non-final while retaining health-to-date."""
    checkpoint = dict(health)
    checkpoint["current_health"] = health["result"]
    checkpoint["result"] = "IN_PROGRESS"
    checkpoint["state"] = "RUNNING"
    return checkpoint


class ArrivalTracker:
    """Thread-safe host-monotonic coverage and pause tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._samples = 0
            self._first: float | None = None
            self._last: float | None = None
            self._max_gap = 0.0

    def add(self, timestamp: float) -> None:
        timestamp = float(timestamp)
        with self._lock:
            if self._first is None:
                self._first = timestamp
            if self._last is not None:
                self._max_gap = max(self._max_gap, timestamp - self._last)
            self._last = timestamp
            self._samples += 1

    @staticmethod
    def empty_report() -> dict:
        return {
            "arrival_samples": 0,
            "first_arrival_delay_s": None,
            "last_arrival_age_s": None,
            "max_host_interarrival_s": None,
        }

    def report(
        self,
        formal_start: float | None,
        formal_stop: float | None,
    ) -> dict:
        with self._lock:
            if self._first is None or self._last is None:
                return self.empty_report()
            return {
                "arrival_samples": self._samples,
                "first_arrival_delay_s": (
                    max(self._first - formal_start, 0.0)
                    if formal_start is not None
                    else None
                ),
                "last_arrival_age_s": (
                    max(formal_stop - self._last, 0.0)
                    if formal_stop is not None
                    else None
                ),
                "max_host_interarrival_s": self._max_gap,
            }


def camera_reports(
    stream_stats: dict[str, object],
    arrival_trackers: dict[str, ArrivalTracker],
    formal_start: float | None,
    formal_stop: float | None,
) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    for name in CAMERA_STREAMS:
        stats = stream_stats.get(name)
        report = stats.report() if stats is not None else {}
        tracker = arrival_trackers.get(name)
        report.update(
            tracker.report(formal_start, formal_stop)
            if tracker is not None
            else ArrivalTracker.empty_report()
        )
        reports[name] = report
    return reports


class StopRequested(RuntimeError):
    """Raised by a termination signal so an early-stop FAIL is persisted."""


def raise_stop_requested(signum: int, _frame: object) -> None:
    """Convert a service termination signal into a finalizable exception."""
    raise StopRequested(signal.Signals(signum).name)


def atomic_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="D405 Depth+双IR与STM32 IMU monitor-only连续性压力测试"
    )
    parser.add_argument("--serial", default="260322273737")
    parser.add_argument(
        "--imu-port",
        default=(
            "/dev/serial/by-id/"
            "usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_"
            "f6a5f836b505f011ae3b8c1272aab386-if00-port0"
        ),
    )
    parser.add_argument("--imu-baud", type=int, default=921600)
    parser.add_argument("--duration", type=float, default=10_800.0)
    parser.add_argument("--checkpoint-interval", type=float, default=60.0)
    parser.add_argument("--warmup-frames", type=int, default=30)
    parser.add_argument("--ir-auto-exposure-limit-us", type=float, default=8000.0)
    parser.add_argument("--ir-auto-gain-limit", type=float, default=248.0)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / ".planning/depth_plane_height_factor_20260823/soak",
    )
    return parser.parse_args()


def counter_delta(start: dict, end: dict, keys: tuple[str, ...]) -> dict:
    result = dict(end)
    for key in keys:
        result[key] = int(end.get(key, 0)) - int(start.get(key, 0))
    return result


def main() -> int:
    args = parse_args()
    if args.duration <= 0.0:
        raise ValueError("duration must be positive")
    if args.checkpoint_interval <= 0.0:
        raise ValueError("checkpoint interval must be positive")

    # Hardware imports stay out of module import so pure acceptance tests do
    # not require a camera, serial device, or a particular librealsense build.
    import pyrealsense2 as rs

    from ego_vio.imu.imu_reader import ImuReader, TRANSPORT_COUNTER_KEYS
    import capture_d405_720p_rgb_stereo_ir as capture

    capture.STREAMS = capture.capture_streams_for_mode("depth_stereo_ir")
    capture.STREAM_KEYS = tuple(name for name, *_ in capture.STREAMS)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = args.output_root.resolve() / f"d405_stm32_monitor_only_{stamp}"
    session.mkdir(parents=True, exist_ok=False)
    checkpoint_path = session / "checkpoint.json"
    report_path = session / "acceptance.json"

    stream_stats = {
        name: capture.StreamContinuity() for name in capture.STREAM_KEYS
    }
    camera_arrivals = {
        name: ArrivalTracker() for name in capture.STREAM_KEYS
    }
    imu_arrivals = ArrivalTracker()
    warmup_counts = {name: 0 for name in capture.STREAM_KEYS}
    warmup_cutoff = {name: -1 for name in capture.STREAM_KEYS}

    sensor = None
    frame_queue = None
    imu = None
    sensor_opened = False
    sensor_started = False
    imu_started = False
    formal_start = None
    formal_stop = None
    imu_start: dict = {}
    imu_end: dict = {}
    imu_arrival_end: dict = {}
    capture_error = None
    ir_configuration = None
    firmware = None
    usb_type = None
    started_wall = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    previous_signal_handlers: dict[int, object] = {}

    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous_signal_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, raise_stop_requested)

    try:
        context = rs.context()
        device = next(
            (
                item
                for item in context.query_devices()
                if item.get_info(rs.camera_info.serial_number) == args.serial
            ),
            None,
        )
        if device is None:
            raise RuntimeError(f"找不到 D405(serial={args.serial})")
        firmware = device.get_info(rs.camera_info.firmware_version)
        usb_type = device.get_info(rs.camera_info.usb_type_descriptor)
        sensor = device.first_depth_sensor()
        profiles = capture.select_profiles(sensor)
        frame_queue = rs.frame_queue(
            capture.MONITOR_QUEUE_CAPACITY,
            keep_frames=False,
        )
        imu = ImuReader(
            args.imu_port,
            baud=args.imu_baud,
            warmup_frames=capture.IMU_WARMUP_FRAMES,
            on_sample=lambda sample: imu_arrivals.add(sample.rx_time),
            name="d405_stm32_soak",
        )
        if not imu.start():
            raise RuntimeError(f"无法打开IMU串口: {args.imu_port}")
        imu_started = True
        ir_configuration = capture.configure_ir_auto_exposure(
            sensor,
            exposure_limit_us=args.ir_auto_exposure_limit_us,
            gain_limit=args.ir_auto_gain_limit,
        )
        sensor.open(profiles)
        sensor_opened = True
        sensor.start(frame_queue)
        sensor_started = True

        print(f"[3h监测] 证据目录: {session}", flush=True)
        print(
            "[3h监测] monitor-only：Depth Z16 + 左IR + 右IR @30Hz，"
            "STM32 IMU @400Hz；不保存原始帧",
            flush=True,
        )
        warmup_deadline = time.monotonic() + 20.0
        while True:
            frame = frame_queue.wait_for_frame(timeout_ms=2000)
            key = capture.stream_key(frame)
            if key is not None:
                warmup_counts[key] += 1
                warmup_cutoff[key] = int(frame.get_frame_number())
            camera_ready = all(
                count >= max(0, args.warmup_frames)
                for count in warmup_counts.values()
            )
            if camera_ready and imu.warmup_stats():
                break
            if time.monotonic() >= warmup_deadline:
                raise RuntimeError(
                    f"预热超时: camera={warmup_counts}, imu={imu.frames_ok}"
                )

        while True:
            frame = frame_queue.poll_for_frame()
            if not frame:
                break
            key = capture.stream_key(frame)
            if key is not None:
                warmup_cutoff[key] = int(frame.get_frame_number())

        # Use lifetime counters, not stats_since_warmup().  ImuReader creates a
        # new warm-up boundary after reconnect; differencing those scoped
        # counters could otherwise hide the very serial error/reconnect this
        # soak test must reject.
        imu_start = {
            key: int(imu.stats().get(key, 0))
            for key in TRANSPORT_COUNTER_KEYS
        }
        imu_arrivals.reset()
        formal_start = time.monotonic()
        next_checkpoint = formal_start + args.checkpoint_interval
        print(
            f"[3h监测] 正式窗口 {args.duration:.1f}s，"
            f"每 {args.checkpoint_interval:.1f}s 更新 checkpoint.json",
            flush=True,
        )

        while True:
            frame = frame_queue.wait_for_frame(timeout_ms=2000)
            now = time.monotonic()
            if now - formal_start >= args.duration:
                break
            key = capture.stream_key(frame)
            if key is None:
                continue
            number = int(frame.get_frame_number())
            if number <= warmup_cutoff[key]:
                continue
            stream_stats[key].add(number, float(frame.get_timestamp()))
            camera_arrivals[key].add(now)

            if now >= next_checkpoint:
                elapsed = now - formal_start
                current_imu = counter_delta(
                    imu_start,
                    imu.stats(),
                    TRANSPORT_COUNTER_KEYS,
                )
                current_imu["arrival"] = imu_arrivals.report(formal_start, now)
                checkpoint_health = evaluate_soak(
                    camera_streams=camera_reports(
                        stream_stats,
                        camera_arrivals,
                        formal_start,
                        now,
                    ),
                    imu_stats=current_imu,
                    duration_s=elapsed,
                    requested_duration_s=elapsed,
                    capture_error=None,
                )
                checkpoint = running_checkpoint(checkpoint_health)
                checkpoint.update(
                    {
                        "session": str(session),
                        "updated_wall": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    }
                )
                atomic_write_json(checkpoint_path, checkpoint)
                print(
                    f"[3h监测] {elapsed / 3600.0:.2f}h "
                    f"camera={checkpoint_health['camera']['result']} "
                    f"imu={checkpoint_health['imu']['result']}",
                    flush=True,
                )
                while next_checkpoint <= now:
                    next_checkpoint += args.checkpoint_interval
        formal_stop = time.monotonic()
        imu_arrival_end = imu_arrivals.report(formal_start, formal_stop)
    except KeyboardInterrupt:
        capture_error = "KeyboardInterrupt: monitor stopped before requested duration"
    except StopRequested as exc:
        capture_error = f"{exc}: monitor stopped before requested duration"
    except Exception as exc:
        capture_error = f"{type(exc).__name__}: {exc}"
        print(f"[3h监测] ERROR: {capture_error}", flush=True)
    finally:
        if formal_start is not None and formal_stop is None:
            formal_stop = time.monotonic()
        if formal_start is not None and not imu_arrival_end:
            imu_arrival_end = imu_arrivals.report(formal_start, formal_stop)
        if imu_started and imu is not None:
            imu_end = imu.stats()
        if sensor_started and sensor is not None:
            try:
                sensor.stop()
            except Exception:
                pass
        if sensor_opened and sensor is not None:
            try:
                sensor.close()
            except Exception:
                pass
        if imu_started and imu is not None:
            imu.stop()
        for signum, handler in previous_signal_handlers.items():
            signal.signal(signum, handler)

    duration_s = (
        max(formal_stop - formal_start, 0.0)
        if formal_start is not None and formal_stop is not None
        else 0.0
    )
    formal_imu = (
        counter_delta(imu_start, imu_end, TRANSPORT_COUNTER_KEYS)
        if imu_start and imu_end
        else {}
    )
    formal_imu["arrival"] = (
        imu_arrival_end
        if imu_arrival_end
        else ArrivalTracker.empty_report()
    )
    report = evaluate_soak(
        camera_streams=camera_reports(
            stream_stats,
            camera_arrivals,
            formal_start,
            formal_stop,
        ),
        imu_stats=formal_imu,
        duration_s=duration_s,
        requested_duration_s=args.duration,
        capture_error=capture_error,
    )
    report.update(
        {
            "schema_version": 1,
            "state": "COMPLETE",
            "session": str(session),
            "started_wall": started_wall,
            "finished_wall": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "camera_serial": args.serial,
            "camera_firmware": firmware,
            "usb_type": usb_type,
            "capture_mode": "depth_stereo_ir",
            "resolution": [1280, 720],
            "camera_rate_requested_hz": 30,
            "imu_rate_requested_hz": 400,
            "warmup_frames": max(0, args.warmup_frames),
            "imu_warmup_frames": capture.IMU_WARMUP_FRAMES,
            "ir_auto_exposure_configuration": ir_configuration,
            "realsense_python_module": str(Path(rs.__file__).resolve()),
        }
    )
    atomic_write_json(report_path, report)
    atomic_write_json(checkpoint_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
