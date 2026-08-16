import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from slam_runtime_watchdog import SlamRuntimeWatchdog, write_json_atomic


def add_pose(
    monitor: SlamRuntimeWatchdog,
    stream: str,
    index: int,
    point: tuple[float, float, float],
) -> None:
    monitor.ingest(
        stream,
        timestamp_s=index / 30.0,
        point=point,
        arrival_monotonic_s=index / 30.0,
    )


def healthy_monitor() -> SlamRuntimeWatchdog:
    monitor = SlamRuntimeWatchdog(start_monotonic_s=0.0)
    for index in range(12):
        point = (index * 0.005, 0.0, 0.0)
        add_pose(monitor, "raw", index, point)
        add_pose(monitor, "corrected", index, point)
    return monitor


def test_runtime_watchdog_becomes_healthy_after_both_streams_are_warm():
    snapshot = healthy_monitor().snapshot(0.4)

    assert snapshot["state"] == "SLAM_HEALTHY"
    assert snapshot["product_usable"] is True
    assert snapshot["failures"] == []


def test_runtime_watchdog_distinguishes_missing_topics_from_slam_failure():
    monitor = SlamRuntimeWatchdog(start_monotonic_s=0.0)

    starting = monitor.snapshot(2.9)
    snapshot = monitor.snapshot(3.1)

    assert starting["state"] == "STARTING"
    assert starting["failures"] == []
    assert snapshot["state"] == "INFRASTRUCTURE_BLOCKED"
    assert snapshot["failures"] == [
        "raw_stream_missing",
        "corrected_stream_missing",
    ]


def test_runtime_watchdog_latches_timestamp_and_map_jump_faults():
    monitor = healthy_monitor()
    monitor.ingest(
        "raw", timestamp_s=0.5, point=(0.06, 0.0, 0.0), arrival_monotonic_s=0.5
    )
    monitor.ingest(
        "corrected",
        timestamp_s=0.5,
        point=(0.2, 0.0, 0.0),
        arrival_monotonic_s=0.5,
    )
    monitor.ingest(
        "raw", timestamp_s=0.4, point=(0.06, 0.0, 0.0), arrival_monotonic_s=0.6
    )

    snapshot = monitor.completion_snapshot()

    assert snapshot["state"] == "SLAM_FAILED"
    assert set(snapshot["failures"]) == {
        "corrected_trajectory_jump",
        "raw_timestamp_not_increasing",
    }


def test_runtime_watchdog_rejects_shared_raw_and_corrected_pose_jump():
    monitor = healthy_monitor()
    monitor.ingest(
        "raw", timestamp_s=0.5, point=(0.2, 0.0, 0.0), arrival_monotonic_s=0.5
    )
    monitor.ingest(
        "corrected",
        timestamp_s=0.5,
        point=(0.2, 0.0, 0.0),
        arrival_monotonic_s=0.5,
    )

    snapshot = monitor.completion_snapshot()

    assert snapshot["state"] == "SLAM_FAILED"
    assert snapshot["product_usable"] is False
    assert snapshot["max_raw_step_m"] > snapshot["max_raw_step_limit_m"]
    assert snapshot["failures"] == ["raw_trajectory_jump"]


def test_runtime_watchdog_detects_stale_and_skewed_streams():
    monitor = healthy_monitor()
    add_pose(monitor, "raw", 20, (0.1, 0.0, 0.0))

    skewed = monitor.snapshot(0.68)
    stale = monitor.snapshot(1.3)

    assert skewed["state"] == "SLAM_FAILED"
    assert "raw_corrected_timestamp_skew" in skewed["failures"]
    assert stale["state"] == "SLAM_FAILED"
    assert "raw_stream_stale" in stale["failures"]
    assert "corrected_stream_stale" in stale["failures"]


def test_runtime_watchdog_waits_for_matching_raw_motion_before_jump_decision():
    monitor = healthy_monitor()
    monitor.ingest(
        "corrected",
        timestamp_s=0.5,
        point=(0.2, 0.0, 0.0),
        arrival_monotonic_s=0.5,
    )

    before_raw_catches_up = monitor.snapshot(0.5)
    monitor.ingest(
        "raw", timestamp_s=0.59, point=(0.2, 0.0, 0.0), arrival_monotonic_s=0.59
    )
    after_raw_catches_up = monitor.snapshot(0.59)

    assert "corrected_trajectory_jump" not in before_raw_catches_up["failures"]
    assert "corrected_trajectory_jump" not in after_raw_catches_up["failures"]


def test_runtime_health_snapshot_is_atomically_replaceable(tmp_path):
    output = tmp_path / "health.json"
    payload = healthy_monitor().snapshot(0.4)

    write_json_atomic(output, payload)

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.iterdir()) == [output]


def test_runtime_watchdog_records_ros_setup_failure_as_infrastructure():
    monitor = SlamRuntimeWatchdog(start_monotonic_s=0.0)
    monitor.mark_infrastructure_failure("ros_setup_error:RuntimeError")

    snapshot = monitor.snapshot(0.1)

    assert snapshot["state"] == "INFRASTRUCTURE_BLOCKED"
    assert snapshot["failures"] == [
        "ros_setup_error:RuntimeError",
        "raw_stream_missing",
        "corrected_stream_missing",
    ]
