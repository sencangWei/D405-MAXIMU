"""Shared IMU-axis transform for offline replay and live VINS publishing."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml


DEFAULT_VINS_IMU_ROTATION = np.array(
    [
        [0.99980212, -0.01423891, -0.01389161],
        [-0.01423891, -0.02458715, -0.99959628],
        [0.01389161, 0.99959628, -0.02478503],
    ],
    dtype=np.float64,
)


def validate_vins_imu_rotation(rotation: np.ndarray) -> np.ndarray:
    value = np.asarray(rotation, dtype=np.float64)
    if value.shape != (3, 3) or not np.all(np.isfinite(value)):
        raise ValueError("R_level_from_imu必须是有限的3x3矩阵")
    if not np.allclose(value @ value.T, np.eye(3), atol=1e-6):
        raise ValueError("R_level_from_imu不是正交矩阵")
    if not np.isclose(np.linalg.det(value), 1.0, atol=1e-6):
        raise ValueError("R_level_from_imu必须是行列式为+1的旋转矩阵")
    return value


def load_vins_imu_rotation(path: str | Path | None = None) -> np.ndarray:
    if not path:
        return DEFAULT_VINS_IMU_ROTATION.copy()
    calibration_path = Path(path)
    document = yaml.safe_load(calibration_path.read_text(encoding="utf-8")) or {}
    if document.get("result") != "PASS":
        raise ValueError(f"调平标定未通过门禁: {calibration_path}")
    if document.get("activation", {}).get("automatically_enabled", False):
        raise ValueError("候选调平标定不应自行声明已自动启用")
    return validate_vins_imu_rotation(document["R_level_from_imu"])
