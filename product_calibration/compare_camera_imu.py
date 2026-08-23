"""Compare two independent Kalibr camera-IMU runs with the proven golden baseline."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if "cam0" not in document or "T_cam_imu" not in document["cam0"]:
        raise ValueError(f"不是Kalibr camera-IMU camchain: {path}")
    return document


def _transform(document: dict[str, Any], camera: str) -> np.ndarray:
    transform = np.asarray(document[camera]["T_cam_imu"], dtype=float)
    if transform.shape != (4, 4):
        raise ValueError(f"{camera}.T_cam_imu必须是4x4")
    return transform


def _rotation_delta_deg(first: np.ndarray, second: np.ndarray) -> float:
    relative = first[:3, :3].T @ second[:3, :3]
    cosine = float(np.clip((np.trace(relative) - 1.0) / 2.0, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _pair_metrics(first: dict[str, Any], second: dict[str, Any]) -> dict[str, float]:
    first_t = _transform(first, "cam0")
    second_t = _transform(second, "cam0")
    first_cam1_t = _transform(first, "cam1")
    second_cam1_t = _transform(second, "cam1")
    return {
        "rotation_deg": _rotation_delta_deg(first_t, second_t),
        "translation_mm": float(
            np.linalg.norm(first_t[:3, 3] - second_t[:3, 3]) * 1000.0
        ),
        "cam0_td_ms": abs(
            float(first["cam0"]["timeshift_cam_imu"])
            - float(second["cam0"]["timeshift_cam_imu"])
        )
        * 1000.0,
        "cam1_td_ms": abs(
            float(first["cam1"]["timeshift_cam_imu"])
            - float(second["cam1"]["timeshift_cam_imu"])
        )
        * 1000.0,
        "cam1_rotation_deg": _rotation_delta_deg(first_cam1_t, second_cam1_t),
        "cam1_translation_mm": float(
            np.linalg.norm(first_cam1_t[:3, 3] - second_cam1_t[:3, 3]) * 1000.0
        ),
    }


def _mean_transform(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Average two rigid transforms without averaging rotation entries directly."""
    rotation_sum = first[:3, :3] + second[:3, :3]
    u, _, vt = np.linalg.svd(rotation_sum)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0.0:
        u[:, -1] *= -1.0
        rotation = u @ vt
    transform = np.eye(4, dtype=float)
    transform[:3, :3] = rotation
    transform[:3, 3] = (first[:3, 3] + second[:3, 3]) / 2.0
    return transform


def candidate_consensus(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Return the isolated two-run candidate consumed by later product stages."""
    cam0 = _mean_transform(_transform(first, "cam0"), _transform(second, "cam0"))
    cam1 = _mean_transform(_transform(first, "cam1"), _transform(second, "cam1"))
    cam0_td = (
        float(first["cam0"]["timeshift_cam_imu"])
        + float(second["cam0"]["timeshift_cam_imu"])
    ) / 2.0
    cam1_td = (
        float(first["cam1"]["timeshift_cam_imu"])
        + float(second["cam1"]["timeshift_cam_imu"])
    ) / 2.0
    return {
        "selection": "two_run_chordal_rotation_and_arithmetic_translation_mean",
        "td_s": cam0_td,
        "cam0_td_s": cam0_td,
        "cam1_td_s": cam1_td,
        "T_cam0_imu": cam0.tolist(),
        "T_cam1_imu": cam1.tolist(),
        "activation": "CANDIDATE_ONLY_REQUIRES_WORLD_Z_AND_END_TO_END_SLAM_AB",
    }


def _golden_run(baseline: dict[str, Any], name: str) -> dict[str, Any]:
    raw = baseline["camera_imu"][name]
    return {
        "cam0": {
            "T_cam_imu": raw["T_cam0_imu"],
            "timeshift_cam_imu": raw["timeshift_cam_imu_s"]["cam0"],
        },
        "cam1": {
            "T_cam_imu": raw["T_cam1_imu"],
            "timeshift_cam_imu": raw["timeshift_cam_imu_s"]["cam1"],
        },
    }


def compare_runs(
    run1_path: Path,
    run2_path: Path,
    golden_baseline_path: Path,
    *,
    max_rotation_deg: float = 0.5,
    max_translation_mm: float = 5.0,
    max_td_ms: float = 3.0,
) -> dict[str, Any]:
    run1, run2 = _load(run1_path), _load(run2_path)
    baseline = yaml.safe_load(
        Path(golden_baseline_path).read_text(encoding="utf-8")
    ) or {}
    golden1, golden2 = _golden_run(baseline, "run1"), _golden_run(baseline, "run2")
    candidate = _pair_metrics(run1, run2)
    golden = _pair_metrics(golden1, golden2)
    candidate_to_golden = {
        "run1": _pair_metrics(golden1, run1),
        "run2": _pair_metrics(golden2, run2),
    }
    checks = {
        "rotation_repeatability": candidate["rotation_deg"] <= max_rotation_deg,
        "translation_repeatability": candidate["translation_mm"] <= max_translation_mm,
        "cam0_td_repeatability": candidate["cam0_td_ms"] <= max_td_ms,
        "cam1_td_repeatability": candidate["cam1_td_ms"] <= max_td_ms,
        "cam1_rotation_repeatability": candidate["cam1_rotation_deg"] <= max_rotation_deg,
        "cam1_translation_repeatability": candidate["cam1_translation_mm"] <= max_translation_mm,
    }
    return {
        "format_version": 1,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "two_independent_kalibr_runs_with_20260808_golden_ab",
        "candidate_repeatability": candidate,
        "candidate": candidate_consensus(run1, run2),
        "golden_20260808_repeatability": golden,
        "candidate_to_corresponding_golden": candidate_to_golden,
        "thresholds": {
            "max_rotation_deg": max_rotation_deg,
            "max_translation_mm": max_translation_mm,
            "max_td_ms": max_td_ms,
        },
        "checks": checks,
        "warning": (
            "PASS仅表示两次新标定重复性合格；仍须检查Kalibr残差并经过最终SLAM A/B，"
            "不得把黄金外参或td直接复制给拆装后的新总成。"
        ),
    }
