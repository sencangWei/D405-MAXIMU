"""Timing must remain observable when most of a calibration take is static."""
import importlib.util
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/estimate_lighthouse_imu_time_offset.py"
SPEC = importlib.util.spec_from_file_location("hold_timing", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_long_static_holds_do_not_saturate_real_motion():
    times = np.arange(0.0, 40.0, 1 / 400)
    speed = 0.001 + 0.00015 * np.sin(times * 71)
    moving = (times > 8) & (times < 20)
    speed[moving] += 0.25 + 0.18 * np.sin(times[moving] * 2.1)
    gyro = np.column_stack((np.zeros_like(speed), np.zeros_like(speed), speed))
    angle = np.r_[0, np.cumsum((speed[:-1] + speed[1:]) * np.diff(times) / 2)]
    physical = np.arange(0.1, 39.9, 1 / 120)
    tracker_angle = np.interp(physical, times, angle)
    # Different quiet-state noise floors must not flatten the true motion.
    tracker_angle += np.random.default_rng(7).normal(0, 0.0001, len(physical))
    quaternions = Rotation.from_euler("z", tracker_angle).as_quat()
    result = MODULE.estimate_offset(times, gyro, physical + 0.0065, quaternions)
    assert result["correlation"] > 0.98
    assert abs(result["tracker_query_offset_ms"] - 6.5) < 1.0
