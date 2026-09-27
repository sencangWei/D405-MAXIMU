import importlib.util
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "estimate_lighthouse_imu_time_offset.py"
)
SPEC = importlib.util.spec_from_file_location(
    "estimate_lighthouse_imu_time_offset", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_gyro_tracker_correlation_recovers_query_offset() -> None:
    imu_times = np.arange(0.0, 18.0, 1.0 / 400.0)
    angular_speed = (
        0.35
        + 0.22 * np.sin(0.9 * imu_times)
        + 0.16 * np.sin(2.7 * imu_times + 0.4)
    )
    angular_speed = np.maximum(angular_speed, 0.02)
    imu_gyro = np.column_stack(
        (np.zeros_like(angular_speed), np.zeros_like(angular_speed), angular_speed)
    )

    physical_tracker_times = np.arange(0.1, 17.9, 1.0 / 125.0)
    integrated_angle = np.r_[
        0.0,
        np.cumsum(
            0.5
            * (angular_speed[:-1] + angular_speed[1:])
            * np.diff(imu_times)
        ),
    ]
    angle = np.interp(
        physical_tracker_times,
        imu_times,
        integrated_angle,
    )
    tracker_quaternions = Rotation.from_euler("z", angle).as_quat()
    # Lighthouse can emit isolated orientation spikes during brief occlusion.
    # They must not pull the time estimate away from the physical gyro signal.
    spike_indices = np.linspace(300, len(tracker_quaternions) - 300, 12, dtype=int)
    tracker_quaternions[spike_indices] = Rotation.from_euler(
        "x", np.full(len(spike_indices), 120.0), degrees=True
    ).as_quat()
    expected_offset_ms = 6.5
    tracker_reported_times = physical_tracker_times + expected_offset_ms / 1000.0

    result = MODULE.estimate_offset(
        imu_times,
        imu_gyro,
        tracker_reported_times,
        tracker_quaternions,
        search_ms=30.0,
    )

    assert abs(result["tracker_query_offset_ms"] - expected_offset_ms) < 1.0
    assert result["correlation"] > 0.98
    assert result["overlap_duration_s"] > 17.0
