"""Arbitrary-pose accelerometer ellipsoid fit with an untouched validation split."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


def load_pose_means(path: Path) -> tuple[np.ndarray, np.ndarray]:
    grouped: dict[tuple[str, str], list[list[float]]] = defaultdict(list)
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"pose_id", "split", "ax", "ay", "az"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("CSV必须包含pose_id,split,ax,ay,az")
        for row in reader:
            split = row["split"].strip().lower()
            if split not in {"fit", "validation"}:
                raise ValueError("split只能是fit或validation")
            grouped[(split, row["pose_id"].strip())].append(
                [float(row["ax"]), float(row["ay"]), float(row["az"])]
            )
    means = {
        key: np.mean(np.asarray(values, dtype=float), axis=0)
        for key, values in grouped.items()
    }
    fit_rows = [value for (split, _), value in means.items() if split == "fit"]
    validation_rows = [
        value for (split, _), value in means.items() if split == "validation"
    ]
    fit = np.asarray(fit_rows, dtype=float).reshape((-1, 3))
    validation = np.asarray(validation_rows, dtype=float).reshape((-1, 3))
    return fit, validation


def _coverage_octants(vectors: np.ndarray) -> int:
    signs = np.signbit(vectors).astype(int)
    return len({tuple(row.tolist()) for row in signs})


def fit_and_validate(
    fit_samples: np.ndarray,
    validation_samples: np.ndarray,
    *,
    gravity: float = 9.80665,
    min_fit_poses: int = 20,
    min_validation_poses: int = 10,
    min_octants: int = 7,
    max_rmse_g: float = 0.01,
    max_error_g: float = 0.03,
    max_condition: float = 3.0,
) -> dict[str, Any]:
    fit_samples = np.asarray(fit_samples, dtype=float)
    validation_samples = np.asarray(validation_samples, dtype=float)
    if fit_samples.ndim != 2 or fit_samples.shape[1:] != (3,):
        raise ValueError("fit_samples必须是Nx3")
    if validation_samples.ndim != 2 or validation_samples.shape[1:] != (3,):
        raise ValueError("validation_samples必须是Nx3")
    if len(fit_samples) < 9:
        raise ValueError("椭球求解至少需要9个非退化姿态")

    x = fit_samples / gravity
    design = np.column_stack(
        (
            x[:, 0] ** 2,
            x[:, 1] ** 2,
            x[:, 2] ** 2,
            2 * x[:, 0] * x[:, 1],
            2 * x[:, 0] * x[:, 2],
            2 * x[:, 1] * x[:, 2],
            x[:, 0],
            x[:, 1],
            x[:, 2],
        )
    )
    if np.linalg.matrix_rank(design) < 9:
        raise ValueError("拟合姿态退化；需要覆盖球面而不是同一平面或少数方向")
    parameters, *_ = np.linalg.lstsq(design, np.ones(len(x)), rcond=None)
    shape = np.array(
        [
            [parameters[0], parameters[3], parameters[4]],
            [parameters[3], parameters[1], parameters[5]],
            [parameters[4], parameters[5], parameters[2]],
        ]
    )
    linear = parameters[6:9]
    eigenvalues = np.linalg.eigvalsh(shape)
    if np.any(eigenvalues <= 0):
        raise ValueError("拟合椭球不是正定；姿态覆盖不足或数据异常")
    center = -0.5 * np.linalg.solve(shape, linear)
    denominator = 1.0 + float(center @ shape @ center)
    normalized_shape = shape / denominator
    eigenvalues, eigenvectors = np.linalg.eigh(normalized_shape)
    if np.any(eigenvalues <= 0):
        raise ValueError("归一化椭球不是正定")
    correction = eigenvectors @ np.diag(np.sqrt(eigenvalues)) @ eigenvectors.T
    bias = center * gravity

    corrected_fit = (correction @ (fit_samples - bias).T).T / gravity
    corrected_validation = (
        correction @ (validation_samples - bias).T
    ).T / gravity
    validation_errors = np.abs(np.linalg.norm(corrected_validation, axis=1) - 1.0)
    rmse = float(np.sqrt(np.mean(validation_errors**2))) if len(validation_errors) else None
    maximum = float(np.max(validation_errors)) if len(validation_errors) else None
    octants = _coverage_octants(corrected_fit)
    condition = float(np.linalg.cond(correction))
    checks = {
        "fit_pose_count": len(fit_samples) >= min_fit_poses,
        "validation_pose_count": len(validation_samples) >= min_validation_poses,
        "fit_octant_coverage": octants >= min_octants,
        "matrix_condition": condition <= max_condition,
        "validation_rmse": rmse is not None and rmse <= max_rmse_g,
        "validation_max_error": maximum is not None and maximum <= max_error_g,
    }
    return {
        "format_version": 1,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "arbitrary_pose_accelerometer_ellipsoid_with_heldout_validation",
        "gravity_m_s2": gravity,
        "fit_pose_count": len(fit_samples),
        "validation_pose_count": len(validation_samples),
        "bias_m_s2": bias.tolist(),
        "correction_matrix": correction.tolist(),
        "metrics": {
            "fit_octants": octants,
            "matrix_condition": condition,
            "validation_norm_rmse_g": rmse,
            "validation_norm_max_error_g": maximum,
        },
        "thresholds": {
            "min_fit_poses": min_fit_poses,
            "min_validation_poses": min_validation_poses,
            "min_octants": min_octants,
            "max_condition": max_condition,
            "max_rmse_g": max_rmse_g,
            "max_error_g": max_error_g,
        },
        "checks": checks,
        "warning": "只校正加速度计；陀螺比例/非正交仍需转台或视觉辅助动态标定。",
    }
