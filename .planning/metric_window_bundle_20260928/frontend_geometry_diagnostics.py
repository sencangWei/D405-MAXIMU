#!/usr/bin/env python3
"""Pure diagnostic geometry summaries for sampled frontend correspondences.

No optimization, ground truth, pose writes, thresholds, or cross-case selection
are performed here.  The returned numbers are JSON-compatible diagnostics only.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation


MIN_COMMON_METRIC = 20


def _array(capture: dict[str, Any], key: str, shape_tail: tuple[int, ...] | None = None) -> np.ndarray:
    if key not in capture:
        raise ValueError(f"missing {key}")
    arr = np.asarray(capture[key])
    if shape_tail is not None and (arr.ndim != len(shape_tail) + 1 or arr.shape[1:] != shape_tail):
        raise ValueError(f"{key} shape")
    return arr


def _finite_matrix(capture: dict[str, Any], key: str, shape: tuple[int, ...]) -> np.ndarray:
    arr = np.asarray(capture[key], dtype=float)
    if arr.shape != shape or not np.all(np.isfinite(arr)):
        raise ValueError(f"{key} shape/nonfinite")
    return arr


def _sim3(capture: dict[str, Any], key: str) -> tuple[np.ndarray, Rotation, float]:
    vec = _finite_matrix(capture, key, (8,))
    scale = float(vec[7])
    if scale <= 0.0:
        raise ValueError(f"{key} nonpositive scale")
    quat = vec[3:7]
    norm = float(np.linalg.norm(quat))
    if norm <= 0.0:
        raise ValueError(f"{key} zero quaternion")
    return vec[:3].astype(float), Rotation.from_quat(quat / norm), scale


def _quat(capture: dict[str, Any], key: str) -> Rotation:
    quat = _finite_matrix(capture, key, (4,))
    norm = float(np.linalg.norm(quat))
    if norm <= 0.0:
        raise ValueError(f"{key} zero quaternion")
    return Rotation.from_quat(quat / norm)


def _stats(values: np.ndarray) -> dict[str, Any]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"status": "UNKNOWN", "count": 0, "reason": "no_finite_values"}
    return {
        "status": "OK",
        "count": int(values.size),
        "min": float(np.min(values)),
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }


def _valid_mask(capture: dict[str, Any], n: int) -> np.ndarray:
    valid = _array(capture, "valid")
    if valid.shape != (n,) or valid.dtype != np.bool_:
        raise ValueError("valid must be a strict bool array")
    return valid


def _projection_options(capture: dict[str, Any]) -> tuple[tuple[int, int] | None, float, float]:
    image_shape = capture.get("image_shape")
    if image_shape is None:
        parsed_shape = None
    else:
        shape = np.asarray(image_shape)
        if shape.shape != (2,) or not np.issubdtype(shape.dtype, np.integer):
            raise ValueError("image_shape must be integer (H,W)")
        parsed_shape = (int(shape[0]), int(shape[1]))
        if parsed_shape[0] <= 0 or parsed_shape[1] <= 0:
            raise ValueError("image_shape nonpositive")
    pixel_border = float(capture.get("pixel_border", 0.0))
    depth_eps = float(capture.get("depth_eps", 0.0))
    if not np.isfinite(pixel_border) or pixel_border < 0.0:
        raise ValueError("pixel_border invalid")
    if not np.isfinite(depth_eps) or depth_eps < 0.0:
        raise ValueError("depth_eps invalid")
    return parsed_shape, pixel_border, depth_eps


def _project(K: np.ndarray, xyz: np.ndarray) -> np.ndarray:
    z = xyz[:, 2]
    uv = xyz[:, :2] / z[:, None]
    return np.column_stack((K[0, 0] * uv[:, 0] + K[0, 2], K[1, 1] * uv[:, 1] + K[1, 2]))


def _backproject(K: np.ndarray, pixels: np.ndarray, depth: np.ndarray) -> np.ndarray:
    x = (pixels[:, 0] - K[0, 2]) * depth / K[0, 0]
    y = (pixels[:, 1] - K[1, 2]) * depth / K[1, 1]
    return np.column_stack((x, y, depth))


def _relative_angle_deg(left: Rotation, right: Rotation) -> float:
    return float((left.inv() * right).magnitude() * 180.0 / np.pi)


def _visual_residuals(
    label: str,
    K: np.ndarray,
    Xf: np.ndarray,
    Xk: np.ndarray,
    pixel_keyframe: np.ndarray,
    transform: tuple[np.ndarray, Rotation, float],
    mask: np.ndarray,
    image_shape: tuple[int, int] | None,
    pixel_border: float,
    depth_eps: float,
) -> dict[str, Any]:
    t, R, scale = transform
    predicted = scale * R.apply(Xf) + t
    candidate = (
        mask
        & np.all(np.isfinite(predicted), axis=1)
        & np.all(np.isfinite(Xk), axis=1)
        & np.all(np.isfinite(pixel_keyframe), axis=1)
        & (predicted[:, 2] > depth_eps)
        & (Xk[:, 2] > depth_eps)
    )
    projected_all = np.full((len(Xf), 2), np.nan, dtype=float)
    if np.any(candidate):
        projected_all[candidate] = _project(K, predicted[candidate])
    finite = candidate & np.all(np.isfinite(projected_all), axis=1)
    if image_shape is not None:
        height, width = image_shape
        inside = (
            (projected_all[:, 0] >= pixel_border)
            & (projected_all[:, 0] < width - pixel_border)
            & (projected_all[:, 1] >= pixel_border)
            & (projected_all[:, 1] < height - pixel_border)
        )
        finite &= inside
    if not np.any(finite):
        return {
            "label": label,
            "status": "UNKNOWN",
            "count": 0,
            "candidate_count": int(np.count_nonzero(candidate)),
            "reason": "no_project_calib_active_projectable_points",
        }
    projected = projected_all[finite]
    pixel_error = projected - pixel_keyframe[finite]
    return {
        "label": label,
        "status": "OK",
        "count": int(np.count_nonzero(finite)),
        "candidate_count": int(np.count_nonzero(candidate)),
        "projection_filter": {
            "image_shape": None if image_shape is None else list(image_shape),
            "pixel_border": float(pixel_border),
            "depth_eps": float(depth_eps),
            "matches_active_project_calib": True,
        },
        "residual_sign": "predicted_minus_measured; descriptive raw diagnostic, not original whitened sqrt_info/Huber cost",
        "pixel_residual_norm_px": _stats(np.linalg.norm(pixel_error, axis=1)),
        "logdepth_residual": _stats(np.log(predicted[finite, 2] / Xk[finite, 2])),
    }


def _metric_common_geometry(
    K: np.ndarray,
    Xf: np.ndarray,
    Xk: np.ndarray,
    pixels_f: np.ndarray,
    pixels_k: np.ndarray,
    depth_f: np.ndarray,
    depth_k: np.ndarray,
    transforms: dict[str, tuple[np.ndarray, Rotation, float]],
    valid: np.ndarray,
) -> dict[str, Any]:
    common = (
        valid
        & np.all(np.isfinite(pixels_f), axis=1)
        & np.all(np.isfinite(pixels_k), axis=1)
        & np.isfinite(depth_f)
        & np.isfinite(depth_k)
        & (depth_f > 0.0)
        & (depth_k > 0.0)
        & np.all(np.isfinite(Xf), axis=1)
        & np.all(np.isfinite(Xk), axis=1)
        & (Xf[:, 2] > 0.0)
        & (Xk[:, 2] > 0.0)
    )
    count = int(np.count_nonzero(common))
    if count < MIN_COMMON_METRIC:
        return {
            "status": "UNKNOWN",
            "count": count,
            "reason": "fewer_than_20_valid_finite_positive_common_stereo_points",
        }

    Mf = _backproject(K, pixels_f[common], depth_f[common])
    Mk = _backproject(K, pixels_k[common], depth_k[common])
    alpha_current = float(np.median(depth_f[common] / Xf[common, 2]))
    alpha_keyframe = float(np.median(depth_k[common] / Xk[common, 2]))
    out: dict[str, Any] = {
        "status": "OK",
        "count": count,
        "scope": "independent stereo depth backprojection; learned XYZ is not treated as metres",
        "learned_to_metric_depth_scale": {
            "current": alpha_current,
            "keyframe": alpha_keyframe,
            "current_over_keyframe": float(alpha_current / alpha_keyframe),
        },
        "by_transform": {},
    }
    for label, (t, R, scale) in transforms.items():
        observations = Mk - R.apply(Mf)
        median_translation = np.median(observations, axis=0)
        learned_translation_metric = alpha_keyframe * t
        out["by_transform"][label] = {
            "fixed_rotation_translation_observation_median_m": median_translation.tolist(),
            "fixed_rotation_translation_residual_norm_m": _stats(np.linalg.norm(observations - median_translation, axis=1)),
            "learned_translation_keyframe_metric_m": learned_translation_metric.tolist(),
            "learned_minus_stereo_median_norm_m": float(np.linalg.norm(learned_translation_metric - median_translation)),
            "sim3_metric_scale_consistency": float(scale * alpha_keyframe / alpha_current),
            "semantic_note": "translation comparison is gauge-dependent diagnostic, not absolute trajectory accuracy",
        }
    return out


def summarize_geometry(capture: dict[str, Any]) -> dict[str, Any]:
    Xf = _array(capture, "Xf", (3,)).astype(float)
    Xk = _array(capture, "Xk", (3,)).astype(float)
    valid = _valid_mask(capture, len(Xf))
    pixels_f = _array(capture, "pixel_current", (2,)).astype(float)
    pixels_k = _array(capture, "pixel_keyframe", (2,)).astype(float)
    depth_f = np.asarray(_array(capture, "depth_current_m"), dtype=float)
    depth_k = np.asarray(_array(capture, "depth_keyframe_m"), dtype=float)
    K = _finite_matrix(capture, "K", (3, 3))
    if Xk.shape != Xf.shape or pixels_f.shape[:1] != Xf.shape[:1] or pixels_k.shape[:1] != Xf.shape[:1]:
        raise ValueError("point/pixel length mismatch")
    if valid.shape != (len(Xf),) or depth_f.shape != (len(Xf),) or depth_k.shape != (len(Xf),):
        raise ValueError("valid/depth length mismatch")
    if K[0, 0] <= 0.0 or K[1, 1] <= 0.0:
        raise ValueError("K nonpositive focal length")
    image_shape, pixel_border, depth_eps = _projection_options(capture)

    transforms = {"pre": _sim3(capture, "T_pre"), "post": _sim3(capture, "T_post")}
    if "T_final" in capture and capture["T_final"] is not None:
        transforms["final"] = _sim3(capture, "T_final")

    R_current = _quat(capture, "quat_current_xyzw")
    R_keyframe = _quat(capture, "quat_keyframe_xyzw")
    R_imu = R_keyframe.inv() * R_current

    visual_mask = valid & np.all(np.isfinite(Xf), axis=1)
    visual = {
        label: _visual_residuals(label, K, Xf, Xk, pixels_k, transform, visual_mask, image_shape, pixel_border, depth_eps)
        for label, transform in transforms.items()
    }
    relative_rotation = {
        label: {"visual_vs_imu_deg": _relative_angle_deg(transform[1], R_imu)}
        for label, transform in transforms.items()
    }
    summary: dict[str, Any] = {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "optimization_run": False,
        "pose_or_point_update_written": False,
        "valid_input_count": int(np.count_nonzero(valid)),
        "visual_residuals": visual,
        "relative_rotation_vs_imu": relative_rotation,
        "common_metric_geometry": _metric_common_geometry(
            K, Xf, Xk, pixels_f, pixels_k, depth_f, depth_k, transforms, valid
        ),
        "interpretation": [
            "residuals are diagnostics, not bias proof",
            "scale ratios and Sim3 consistency are separate semantics, not a scale-drift claim",
        ],
    }
    if "Xk_after" in capture and capture["Xk_after"] is not None:
        Xk_after = _array(capture, "Xk_after", (3,)).astype(float)
        if Xk_after.shape != Xk.shape:
            raise ValueError("Xk_after shape")
        mask = valid & np.isfinite(Xk[:, 2]) & np.isfinite(Xk_after[:, 2]) & (Xk[:, 2] > 0.0) & (Xk_after[:, 2] > 0.0)
        summary["learned_map_update"] = {
            "scope": "learned keyframe point-map depth update; not metre accuracy",
            "Xk_after_z_over_Xk_z": _stats(Xk_after[mask, 2] / Xk[mask, 2]),
            "relative_z_change": _stats((Xk_after[mask, 2] - Xk[mask, 2]) / Xk[mask, 2]),
        }
    return summary
