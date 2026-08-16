#!/usr/bin/env python3

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "calibrate_imu_planar_axes.py"
SPEC = importlib.util.spec_from_file_location("imu_planar_axes", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def synthetic_data(motion=0.08, count=5000):
    rng = np.random.default_rng(5)
    ax, ay = math.radians(2.5), math.radians(-1.25)
    rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    expected = rx @ ry @ MODULE.R_LEVEL_FROM_IMU_NOMINAL
    x_direction = expected.T @ [1.0, 0.0, 0.0]
    z_direction = expected.T @ [0.0, -1.0, 0.0]
    gravity = expected.T @ [0.0, 0.0, -1.0]
    excitation = motion * np.sin(np.linspace(0.0, 24.0 * math.pi, count))
    static = np.tile(gravity, (count, 1)) + rng.normal(0.0, 0.0005, (count, 3))
    gyro = rng.normal(0.0, 0.04, (count, 3))
    return expected, static, static + excitation[:, None] * x_direction, static + excitation[:, None] * z_direction, gyro


def test_recovers_planar_axis_rotation():
    expected, static, x_data, z_data, gyro = synthetic_data()
    result = MODULE.solve_planar_axes(static, x_data, z_data, gyro, gyro, gyro)
    np.testing.assert_allclose(result["rotation_level_from_imu"], expected, atol=4e-3)
    assert result["vertical_disagreement_deg"] < 0.2
    assert abs(np.linalg.det(result["rotation_level_from_imu"]) - 1.0) < 1e-12


def test_quality_accepts_clean_excitation():
    _, static, x_data, z_data, gyro = synthetic_data()
    result = MODULE.solve_planar_axes(static, x_data, z_data, gyro, gyro, gyro)
    metrics = {
        name: {"samples": 5000, "duration_s": 12.5, "rate_hz": 400.0}
        for name in ("static", "x_motion", "z_motion")
    }
    quality = MODULE.assess_quality(
        result, metrics, {"dropped_frames": 0, "counter_resets": 0, "serial_errors": 0}, MODULE.Thresholds()
    )
    assert quality["result"] == "PASS", quality


def test_quality_rejects_weak_motion_and_rotation():
    _, static, x_data, z_data, gyro = synthetic_data(motion=0.001)
    moving_gyro = gyro.copy()
    moving_gyro[:3000] += [0.0, 0.0, 12.0]
    result = MODULE.solve_planar_axes(static, x_data, z_data, gyro, moving_gyro, moving_gyro)
    metrics = {
        name: {"samples": 5000, "duration_s": 12.5, "rate_hz": 400.0}
        for name in ("static", "x_motion", "z_motion")
    }
    quality = MODULE.assess_quality(
        result, metrics, {"dropped_frames": 0, "counter_resets": 0, "serial_errors": 0}, MODULE.Thresholds()
    )
    assert quality["result"] == "FAIL"
    assert "x_motion_rms_g" in quality["failed_checks"]
    assert "z_motion_rms_g" in quality["failed_checks"]
    assert "x_gyro_gated_fraction" in quality["failed_checks"]
    assert "z_gyro_gated_fraction" in quality["failed_checks"]


def test_horizontal_push_direction_does_not_redefine_yaw():
    expected, static, x_data, z_data, gyro = synthetic_data()
    gravity = np.mean(static, axis=0)
    x_motion = x_data - gravity
    z_motion = z_data - gravity
    skewed_x = static + x_motion + 0.20 * z_motion
    skewed_z = static - 0.10 * x_motion + z_motion
    result = MODULE.solve_planar_axes(static, skewed_x, skewed_z, gyro, gyro, gyro)
    np.testing.assert_allclose(result["rotation_level_from_imu"], expected, atol=4e-3)
