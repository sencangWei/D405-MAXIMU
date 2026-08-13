#!/usr/bin/env python3
"""Publish truth-free runtime health for local and loop-corrected SLAM poses."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


SCHEMA_VERSION = 1


@dataclass
class StreamState:
    samples: int = 0
    first_arrival_s: float | None = None
    last_arrival_s: float | None = None
    last_timestamp_s: float | None = None
    last_point: tuple[float, float, float] | None = None
    recent_steps_m: deque[float] = field(default_factory=lambda: deque(maxlen=90))
    recent_timed_steps: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=180)
    )


class SlamRuntimeWatchdog:
    """Latch observable SLAM output faults without using external ground truth."""

    def __init__(
        self,
        *,
        start_monotonic_s: float,
        startup_grace_s: float = 3.0,
        stale_timeout_s: float = 0.5,
        max_stream_skew_s: float = 0.1,
        min_samples_healthy: int = 10,
        min_absolute_jump_m: float = 0.03,
        raw_step_multiplier: float = 3.5,
    ) -> None:
        if startup_grace_s <= 0 or stale_timeout_s <= 0 or max_stream_skew_s <= 0:
            raise ValueError("time thresholds must be positive")
        if min_samples_healthy <= 1:
            raise ValueError("min_samples_healthy must be greater than one")
        if min_absolute_jump_m <= 0 or raw_step_multiplier <= 0:
            raise ValueError("jump thresholds must be positive")
        self.start_monotonic_s = start_monotonic_s
        self.startup_grace_s = startup_grace_s
        self.stale_timeout_s = stale_timeout_s
        self.max_stream_skew_s = max_stream_skew_s
        self.min_samples_healthy = min_samples_healthy
        self.min_absolute_jump_m = min_absolute_jump_m
        self.raw_step_multiplier = raw_step_multiplier
        self.streams = {"raw": StreamState(), "corrected": StreamState()}
        self.latched_failures: list[str] = []
        self.infrastructure_failures: list[str] = []
        self.max_corrected_step_m = 0.0
        self.max_allowed_corrected_step_m = min_absolute_jump_m
        self.pending_corrected_steps: deque[tuple[float, float]] = deque()

    def _latch(self, reason: str) -> None:
        if reason not in self.latched_failures:
            self.latched_failures.append(reason)

    def mark_infrastructure_failure(self, reason: str) -> None:
        if reason not in self.infrastructure_failures:
            self.infrastructure_failures.append(reason)

    def _evaluate_pending_corrected_steps(self, *, force: bool) -> None:
        raw_state = self.streams["raw"]
        while self.pending_corrected_steps:
            timestamp_s, corrected_step_m = self.pending_corrected_steps[0]
            if (
                not force
                and (
                    raw_state.last_timestamp_s is None
                    or raw_state.last_timestamp_s
                    < timestamp_s + self.max_stream_skew_s
                )
            ):
                break
            raw_candidates = [
                step_m
                for raw_timestamp_s, step_m in raw_state.recent_timed_steps
                if abs(raw_timestamp_s - timestamp_s) <= self.max_stream_skew_s
            ]
            raw_reference = max(raw_candidates, default=0.0)
            allowed = max(
                self.min_absolute_jump_m,
                self.raw_step_multiplier * raw_reference,
            )
            self.max_corrected_step_m = max(
                self.max_corrected_step_m, corrected_step_m
            )
            self.max_allowed_corrected_step_m = allowed
            if corrected_step_m > allowed:
                self._latch("corrected_trajectory_jump")
            self.pending_corrected_steps.popleft()

    def ingest(
        self,
        stream: str,
        *,
        timestamp_s: float,
        point: tuple[float, float, float],
        arrival_monotonic_s: float,
    ) -> None:
        if stream not in self.streams:
            raise ValueError(f"unknown stream: {stream}")
        state = self.streams[stream]
        values = (timestamp_s, arrival_monotonic_s, *point)
        if not all(math.isfinite(value) for value in values):
            self._latch(f"{stream}_non_finite_pose")
            return
        if (
            state.last_timestamp_s is not None
            and timestamp_s <= state.last_timestamp_s
        ):
            self._latch(f"{stream}_timestamp_not_increasing")
            return
        if state.last_point is not None:
            step = math.dist(state.last_point, point)
            state.recent_steps_m.append(step)
            state.recent_timed_steps.append((timestamp_s, step))
            if stream == "corrected":
                self.pending_corrected_steps.append((timestamp_s, step))
        state.samples += 1
        state.first_arrival_s = (
            arrival_monotonic_s
            if state.first_arrival_s is None
            else state.first_arrival_s
        )
        state.last_arrival_s = arrival_monotonic_s
        state.last_timestamp_s = timestamp_s
        state.last_point = point
        self._evaluate_pending_corrected_steps(force=False)

    def completion_snapshot(self) -> dict:
        """Seal an offline run at its last received sample, not later drain silence."""
        arrivals = [
            state.last_arrival_s
            for state in self.streams.values()
            if state.last_arrival_s is not None
        ]
        now = max(arrivals) if len(arrivals) == len(self.streams) else time.monotonic()
        self._evaluate_pending_corrected_steps(force=True)
        return self.snapshot(now)

    def snapshot(self, now_monotonic_s: float) -> dict:
        elapsed_s = max(0.0, now_monotonic_s - self.start_monotonic_s)
        runtime_failures = list(self.latched_failures)
        missing_streams = [
            name for name, state in self.streams.items() if state.samples == 0
        ]
        if self.infrastructure_failures:
            state_name = "INFRASTRUCTURE_BLOCKED"
            runtime_failures.extend(self.infrastructure_failures)
            runtime_failures.extend(
                f"{stream}_stream_missing" for stream in missing_streams
            )
        elif missing_streams and elapsed_s < self.startup_grace_s:
            state_name = "SLAM_FAILED" if runtime_failures else "STARTING"
        elif elapsed_s >= self.startup_grace_s and missing_streams:
            state_name = "INFRASTRUCTURE_BLOCKED"
            runtime_failures.extend(
                f"{stream}_stream_missing" for stream in missing_streams
            )
        else:
            for name, state in self.streams.items():
                if (
                    state.last_arrival_s is not None
                    and now_monotonic_s - state.last_arrival_s > self.stale_timeout_s
                ):
                    runtime_failures.append(f"{name}_stream_stale")
            timestamps = [
                state.last_timestamp_s for state in self.streams.values()
            ]
            if all(timestamp is not None for timestamp in timestamps):
                stream_skew_s = abs(float(timestamps[0]) - float(timestamps[1]))
                if stream_skew_s > self.max_stream_skew_s:
                    runtime_failures.append("raw_corrected_timestamp_skew")
            else:
                stream_skew_s = None

            if runtime_failures:
                state_name = "SLAM_FAILED"
            elif all(
                state.samples >= self.min_samples_healthy
                for state in self.streams.values()
            ):
                state_name = "SLAM_HEALTHY"
            else:
                state_name = "STARTING"

        failures = list(dict.fromkeys(runtime_failures))
        raw_timestamp = self.streams["raw"].last_timestamp_s
        corrected_timestamp = self.streams["corrected"].last_timestamp_s
        stream_skew_s = (
            abs(raw_timestamp - corrected_timestamp)
            if raw_timestamp is not None and corrected_timestamp is not None
            else None
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "state": state_name,
            "product_usable": state_name == "SLAM_HEALTHY",
            "elapsed_s": elapsed_s,
            "streams": {
                name: {
                    "samples": state.samples,
                    "last_timestamp_s": state.last_timestamp_s,
                    "age_s": (
                        now_monotonic_s - state.last_arrival_s
                        if state.last_arrival_s is not None
                        else None
                    ),
                }
                for name, state in self.streams.items()
            },
            "raw_corrected_timestamp_skew_s": stream_skew_s,
            "max_corrected_step_m": self.max_corrected_step_m,
            "last_allowed_corrected_step_m": self.max_allowed_corrected_step_m,
            "thresholds": {
                "startup_grace_s": self.startup_grace_s,
                "stale_timeout_s": self.stale_timeout_s,
                "max_stream_skew_s": self.max_stream_skew_s,
                "min_samples_healthy": self.min_samples_healthy,
                "min_absolute_jump_m": self.min_absolute_jump_m,
                "raw_step_multiplier": self.raw_step_multiplier,
            },
            "failures": failures,
        }


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description="VINS运行中失效检测与ROS诊断发布")
    parser.add_argument("--raw-topic", default="/odometry")
    parser.add_argument("--corrected-topic", default="/odometry_rect")
    parser.add_argument("--diagnostics-topic", default="/slam/diagnostics")
    parser.add_argument("--json-topic", default="/slam/health")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--startup-grace-s", type=float, default=3.0)
    parser.add_argument("--stale-timeout-s", type=float, default=0.5)
    parser.add_argument("--max-stream-skew-s", type=float, default=0.1)
    parser.add_argument("--publish-hz", type=float, default=5.0)
    args = parser.parse_args()
    if args.publish_hz <= 0:
        parser.error("--publish-hz must be positive")

    monitor = SlamRuntimeWatchdog(
        start_monotonic_s=time.monotonic(),
        startup_grace_s=args.startup_grace_s,
        stale_timeout_s=args.stale_timeout_s,
        max_stream_skew_s=args.max_stream_skew_s,
    )
    node = None
    ros_initialized = False
    setup_complete = False
    final: dict | None = None

    try:
        import rclpy
        from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
        from nav_msgs.msg import Odometry
        from rclpy.node import Node
        from std_msgs.msg import String

        rclpy.init()
        ros_initialized = True
        node = Node("slam_runtime_watchdog")
        diagnostics_publisher = node.create_publisher(
            DiagnosticArray, args.diagnostics_topic, 10
        )
        json_publisher = node.create_publisher(String, args.json_topic, 10)

        def receive(stream: str, message: Odometry) -> None:
            stamp = message.header.stamp
            position = message.pose.pose.position
            monitor.ingest(
                stream,
                timestamp_s=float(stamp.sec) + float(stamp.nanosec) * 1e-9,
                point=(float(position.x), float(position.y), float(position.z)),
                arrival_monotonic_s=time.monotonic(),
            )

        node.create_subscription(
            Odometry, args.raw_topic, lambda message: receive("raw", message), 100
        )
        node.create_subscription(
            Odometry,
            args.corrected_topic,
            lambda message: receive("corrected", message),
            100,
        )

        def publish() -> None:
            payload = monitor.snapshot(time.monotonic())
            json_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            json_publisher.publish(String(data=json_text))
            status = DiagnosticStatus()
            status.name = "ego_vio/slam_runtime"
            status.hardware_id = "D405+KT-EX9-2"
            status.message = payload["state"]
            if payload["state"] == "SLAM_HEALTHY":
                status.level = DiagnosticStatus.OK
            elif payload["state"] == "STARTING":
                status.level = DiagnosticStatus.STALE
            else:
                status.level = DiagnosticStatus.ERROR
            status.values = [
                KeyValue(key="state", value=payload["state"]),
                KeyValue(
                    key="product_usable",
                    value=str(payload["product_usable"]).lower(),
                ),
                KeyValue(key="failures", value=",".join(payload["failures"])),
                KeyValue(
                    key="raw_samples",
                    value=str(payload["streams"]["raw"]["samples"]),
                ),
                KeyValue(
                    key="corrected_samples",
                    value=str(payload["streams"]["corrected"]["samples"]),
                ),
            ]
            diagnostics = DiagnosticArray()
            diagnostics.header.stamp = node.get_clock().now().to_msg()
            diagnostics.status = [status]
            diagnostics_publisher.publish(diagnostics)
            if args.output_json:
                write_json_atomic(args.output_json, payload)

        node.create_timer(1.0 / args.publish_hz, publish)
        setup_complete = True
        rclpy.spin(node)
    except KeyboardInterrupt:
        if not setup_complete:
            monitor.mark_infrastructure_failure("ros_setup_interrupted")
    except Exception as exc:
        monitor.mark_infrastructure_failure(f"ros_setup_error:{type(exc).__name__}")
    finally:
        final = monitor.snapshot(time.monotonic())
        if args.output_json:
            write_json_atomic(args.output_json, final)
        if node is not None:
            node.destroy_node()
        if ros_initialized and rclpy.ok():
            rclpy.shutdown()
    assert final is not None
    if final["state"] == "SLAM_HEALTHY":
        return 0
    return 4 if final["state"] == "INFRASTRUCTURE_BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
