#!/usr/bin/env python3
"""Calibrate a rigid Vive Tracker-to-UMI transform and build scoring ground truth.

The Lighthouse trajectory is used only as an external reference.  It is never
fed into VINS/SLAM.  Calibrate on a dedicated multi-axis motion sequence, freeze
the resulting JSON, then apply that JSON to separate evaluation recordings.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares, minimize_scalar
from scipy.spatial.transform import Rotation, Slerp


MAX_TRANSLATION_OBSERVABILITY_CONDITION = 8.0
MIN_TRANSLATION_NORMALIZED_SINGULAR_VALUE = 0.02
MIN_ROTATION_AXIS_SINGULAR_RATIO = 0.15


def summary(values: np.ndarray, scale: float = 1.0) -> dict[str, float]:
    values = np.asarray(values, dtype=float) * scale
    return {
        "median": float(np.median(values)),
        "rmse": float(np.sqrt(np.mean(values * values))),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }


def load_vio(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    required = {"t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid VIO trajectory schema: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows])
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.asarray(
        [
            [float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"])]
            for row in rows
        ]
    )
    return clean_poses(times, positions, quaternions, path)


def load_tracker(
    path: Path, time_source: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    rows = [
        row
        for row in csv.DictReader(path.open(newline="", encoding="utf-8"))
        if row.get("name") == "WM0"
    ]
    required = {"device_time_s", "px_m", "py_m", "pz_m", "qw", "qx", "qy", "qz"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid Lighthouse trajectory schema: {path}")
    if time_source == "device":
        times = np.asarray([float(row["device_time_s"]) for row in rows])
    elif time_source == "host_realtime":
        if not all(row.get("host_realtime_ns") for row in rows):
            raise ValueError("host_realtime_ns is absent; use a dual-clock capture")
        times = np.asarray([int(row["host_realtime_ns"]) * 1e-9 for row in rows])
    elif time_source == "host_monotonic":
        if not all(row.get("host_monotonic_ns") for row in rows):
            raise ValueError("host_monotonic_ns is absent from Tracker capture")
        times = np.asarray([int(row["host_monotonic_ns"]) * 1e-9 for row in rows])
    else:
        raise ValueError(f"unsupported Tracker time source: {time_source}")
    positions = np.asarray(
        [[float(row[key]) for key in ("px_m", "py_m", "pz_m")] for row in rows]
    )
    quaternions = np.asarray(
        [
            [float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"])]
            for row in rows
        ]
    )
    serials = sorted({row["serial"] for row in rows})
    if len(serials) != 1:
        raise ValueError(f"expected one WM0 serial, got {serials}")
    times, positions, quaternions = clean_poses(times, positions, quaternions, path)
    return times, positions, quaternions, serials[0]


def map_vio_times(
    vio_times: np.ndarray, time_source: str, d405_frames_path: Path | None
) -> tuple[np.ndarray, dict[str, object]]:
    if time_source != "host_monotonic":
        return vio_times, {
            "vio_input_domain": "unix_epoch_s",
            "alignment_domain": time_source,
            "d405_frames": None,
        }
    if d405_frames_path is None:
        raise ValueError("--d405-frames is required for host_monotonic alignment")
    rows = list(csv.DictReader(d405_frames_path.open(newline="", encoding="utf-8")))
    offsets = []
    for row in rows:
        wall = row.get("sensor_event_wall") or row.get("arrival_wall")
        monotonic = row.get("sensor_event_mono") or row.get("arrival_mono")
        if wall and monotonic:
            offsets.append(float(wall) - float(monotonic))
    if len(offsets) < 10:
        raise ValueError(f"too few D405 wall/monotonic clock pairs: {len(offsets)}")
    offsets_array = np.asarray(offsets)
    epoch_minus_monotonic_s = float(np.median(offsets_array))
    span_us = float(np.ptp(offsets_array) * 1.0e6)
    if span_us > 1000.0:
        raise ValueError(f"D405 wall/monotonic mapping changed by {span_us:.3f} us")
    return vio_times - epoch_minus_monotonic_s, {
        "vio_input_domain": "d405_global_time_unix_epoch_s",
        "alignment_domain": "host_monotonic_s",
        "d405_frames": str(d405_frames_path.resolve()),
        "epoch_minus_monotonic_s": epoch_minus_monotonic_s,
        "mapping_samples": len(offsets),
        "mapping_span_us": span_us,
    }


def clean_poses(
    times: np.ndarray, positions: np.ndarray, quaternions: np.ndarray, path: Path
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    finite = (
        np.isfinite(times)
        & np.all(np.isfinite(positions), axis=1)
        & np.all(np.isfinite(quaternions), axis=1)
    )
    times, positions, quaternions = times[finite], positions[finite], quaternions[finite]
    order = np.argsort(times, kind="stable")
    times, positions, quaternions = times[order], positions[order], quaternions[order]
    keep = np.r_[True, np.diff(times) > 0.0]
    times, positions, quaternions = times[keep], positions[keep], quaternions[keep]
    if len(times) < 30:
        raise ValueError(f"too few valid poses in {path}: {len(times)}")
    norms = np.linalg.norm(quaternions, axis=1)
    if np.any(norms < 1.0e-8):
        raise ValueError(f"zero quaternion in {path}")
    return times, positions, quaternions / norms[:, None]


def pose_matrices(positions: np.ndarray, quaternions: np.ndarray) -> np.ndarray:
    matrices = np.tile(np.eye(4), (len(positions), 1, 1))
    matrices[:, :3, :3] = Rotation.from_quat(quaternions).as_matrix()
    matrices[:, :3, 3] = positions
    return matrices


def interpolate_tracker(
    query_times: np.ndarray,
    tracker_times: np.ndarray,
    tracker_positions: np.ndarray,
    tracker_quaternions: np.ndarray,
    max_gap_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    inside = (query_times >= tracker_times[0]) & (query_times <= tracker_times[-1])
    selected = query_times[inside]
    right = np.searchsorted(tracker_times, selected, side="right")
    right = np.clip(right, 1, len(tracker_times) - 1)
    left = right - 1
    gaps = tracker_times[right] - tracker_times[left]
    valid_selected = gaps <= max_gap_s
    valid = np.zeros(len(query_times), dtype=bool)
    valid[np.flatnonzero(inside)[valid_selected]] = True
    selected = selected[valid_selected]
    left, right, gaps = left[valid_selected], right[valid_selected], gaps[valid_selected]
    alpha = (selected - tracker_times[left]) / gaps
    positions = (
        (1.0 - alpha[:, None]) * tracker_positions[left]
        + alpha[:, None] * tracker_positions[right]
    )
    quaternions = Slerp(
        tracker_times, Rotation.from_quat(tracker_quaternions)
    )(selected).as_quat()
    return pose_matrices(positions, quaternions), valid


def relative_pairs(
    tracker_poses: np.ndarray, body_poses: np.ndarray, sample_rate_hz: float
) -> tuple[list[tuple[np.ndarray, np.ndarray]], dict[str, float]]:
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    translation = []
    rotation = []
    for horizon_s in (0.20, 0.50, 1.00, 1.50):
        lag = max(1, int(round(horizon_s * sample_rate_hz)))
        stride = max(1, lag // 3)
        for index in range(0, len(body_poses) - lag, stride):
            next_index = index + lag
            a = np.linalg.inv(tracker_poses[index]) @ tracker_poses[next_index]
            b = np.linalg.inv(body_poses[index]) @ body_poses[next_index]
            distance = float(np.linalg.norm(b[:3, 3]))
            angle = float(Rotation.from_matrix(b[:3, :3]).magnitude())
            if distance < 0.002 and angle < math.radians(0.5):
                continue
            pairs.append((a, b))
            translation.append(distance)
            rotation.append(angle)
    if len(pairs) < 40:
        raise ValueError(f"insufficient excited relative motions: {len(pairs)}")
    return pairs, {
        "relative_pairs": len(pairs),
        "translation_p95_m": float(np.percentile(translation, 95)),
        "rotation_p95_deg": float(np.degrees(np.percentile(rotation, 95))),
        "observability": motion_observability(pairs),
    }


def motion_observability(
    pairs: list[tuple[np.ndarray, np.ndarray]],
) -> dict[str, object]:
    translation_matrix = np.vstack(
        [tracker_motion[:3, :3] - np.eye(3) for tracker_motion, _ in pairs]
    )
    translation_singular_values = np.linalg.svd(
        translation_matrix, compute_uv=False
    )
    smallest = float(translation_singular_values[-1])
    largest = float(translation_singular_values[0])
    normalized_smallest = smallest / math.sqrt(len(pairs))
    condition = largest / smallest if smallest > 1.0e-12 else math.inf

    rotation_axes = []
    for _, body_motion in pairs:
        rotation_vector = Rotation.from_matrix(body_motion[:3, :3]).as_rotvec()
        magnitude = float(np.linalg.norm(rotation_vector))
        if magnitude >= math.radians(1.0):
            rotation_axes.append(rotation_vector / magnitude)
    if len(rotation_axes) >= 3:
        axis_singular_values = np.linalg.svd(
            np.asarray(rotation_axes), compute_uv=False
        )
        axis_ratio = float(axis_singular_values[-1] / axis_singular_values[0])
    else:
        axis_singular_values = np.zeros(3)
        axis_ratio = 0.0
    passed = (
        condition <= MAX_TRANSLATION_OBSERVABILITY_CONDITION
        and normalized_smallest >= MIN_TRANSLATION_NORMALIZED_SINGULAR_VALUE
        and axis_ratio >= MIN_ROTATION_AXIS_SINGULAR_RATIO
    )
    return {
        "result": "PASS" if passed else "FAIL",
        "translation_singular_values": [
            float(value) for value in translation_singular_values
        ],
        "translation_normalized_min_singular": normalized_smallest,
        "translation_condition": condition,
        "rotation_axis_singular_values": [
            float(value) for value in axis_singular_values
        ],
        "rotation_axis_singular_ratio": axis_ratio,
        "thresholds": {
            "maximum_translation_condition": MAX_TRANSLATION_OBSERVABILITY_CONDITION,
            "minimum_translation_normalized_singular": (
                MIN_TRANSLATION_NORMALIZED_SINGULAR_VALUE
            ),
            "minimum_rotation_axis_singular_ratio": (
                MIN_ROTATION_AXIS_SINGULAR_RATIO
            ),
        },
    }


def active_motion_bounds(
    body_poses: np.ndarray, sample_rate_hz: float
) -> tuple[int, int]:
    lag = max(1, int(round(0.20 * sample_rate_hz)))
    active_indices = []
    for index in range(len(body_poses) - lag):
        motion = np.linalg.inv(body_poses[index]) @ body_poses[index + lag]
        distance = float(np.linalg.norm(motion[:3, 3]))
        angle = float(Rotation.from_matrix(motion[:3, :3]).magnitude())
        if distance >= 0.002 or angle >= math.radians(0.5):
            active_indices.append(index)
    if not active_indices:
        raise ValueError("no active motion interval for stability validation")
    return active_indices[0], min(len(body_poses), active_indices[-1] + lag + 1)


def initial_transform(pairs: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    tracker_vectors = []
    body_vectors = []
    for tracker_motion, body_motion in pairs:
        tracker_vector = Rotation.from_matrix(tracker_motion[:3, :3]).as_rotvec()
        body_vector = Rotation.from_matrix(body_motion[:3, :3]).as_rotvec()
        if np.linalg.norm(body_vector) >= math.radians(1.0):
            tracker_vectors.append(tracker_vector)
            body_vectors.append(body_vector)
    if len(tracker_vectors) < 3:
        raise ValueError("rotation excitation is insufficient for hand-eye calibration")
    rotation, _ = Rotation.align_vectors(
        np.asarray(tracker_vectors), np.asarray(body_vectors)
    )
    rotation_matrix = rotation.as_matrix()
    lhs = []
    rhs = []
    for tracker_motion, body_motion in pairs:
        lhs.append(tracker_motion[:3, :3] - np.eye(3))
        rhs.append(rotation_matrix @ body_motion[:3, 3] - tracker_motion[:3, 3])
    translation, *_ = np.linalg.lstsq(np.vstack(lhs), np.concatenate(rhs), rcond=None)
    transform = np.eye(4)
    transform[:3, :3] = rotation_matrix
    transform[:3, 3] = translation
    return transform


def residual_vector(parameters: np.ndarray, pairs: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    rotation = Rotation.from_rotvec(parameters[:3]).as_matrix()
    translation = parameters[3:]
    residuals = []
    for tracker_motion, body_motion in pairs:
        rotation_error = Rotation.from_matrix(
            tracker_motion[:3, :3]
            @ rotation
            @ body_motion[:3, :3].T
            @ rotation.T
        ).as_rotvec()
        translation_error = (
            tracker_motion[:3, :3] @ translation
            + tracker_motion[:3, 3]
            - rotation @ body_motion[:3, 3]
            - translation
        )
        residuals.append(np.r_[0.10 * rotation_error, translation_error])
    return np.concatenate(residuals)


def fit_transform(pairs: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, dict]:
    initial = initial_transform(pairs)
    parameters = np.r_[
        Rotation.from_matrix(initial[:3, :3]).as_rotvec(), initial[:3, 3]
    ]
    fitted = least_squares(
        residual_vector,
        parameters,
        args=(pairs,),
        loss="huber",
        f_scale=0.003,
        max_nfev=200,
    )
    transform = np.eye(4)
    transform[:3, :3] = Rotation.from_rotvec(fitted.x[:3]).as_matrix()
    transform[:3, 3] = fitted.x[3:]
    raw = residual_vector(fitted.x, pairs).reshape((-1, 6))
    rotation_error = np.linalg.norm(raw[:, :3], axis=1) / 0.10
    translation_error = np.linalg.norm(raw[:, 3:], axis=1)
    combined = np.sqrt(translation_error**2 + (0.10 * rotation_error) ** 2)
    return transform, {
        "solver_success": bool(fitted.success),
        "translation_residual_mm": summary(translation_error, 1000.0),
        "rotation_residual_deg": summary(np.degrees(rotation_error)),
        "combined_residual_mm": summary(combined, 1000.0),
    }


def evaluate_offset(
    offset_s: float,
    vio_times: np.ndarray,
    vio_poses: np.ndarray,
    tracker_times: np.ndarray,
    tracker_positions: np.ndarray,
    tracker_quaternions: np.ndarray,
    max_gap_s: float,
) -> tuple[float, np.ndarray, dict]:
    tracker_poses, valid = interpolate_tracker(
        vio_times + offset_s,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    selected_vio = vio_poses[valid]
    if len(selected_vio) < 100:
        raise ValueError("fewer than 100 timestamp-overlapped poses")
    selected_times = vio_times[valid]
    sample_rate = (len(selected_times) - 1) / (selected_times[-1] - selected_times[0])
    pairs, excitation = relative_pairs(tracker_poses, selected_vio, sample_rate)
    transform, residuals = fit_transform(pairs)
    score = residuals["combined_residual_mm"]["p95"]
    return score, transform, {
        "overlap_samples": int(valid.sum()),
        "overlap_ratio": float(valid.mean()),
        "sample_rate_hz": float(sample_rate),
        "excitation": excitation,
        "residuals": residuals,
    }


def split_half_stability(
    offset_s: float,
    vio_times: np.ndarray,
    vio_poses: np.ndarray,
    tracker_times: np.ndarray,
    tracker_positions: np.ndarray,
    tracker_quaternions: np.ndarray,
    max_gap_s: float,
) -> dict[str, object]:
    tracker_poses, valid = interpolate_tracker(
        vio_times + offset_s,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    selected_vio = vio_poses[valid]
    selected_times = vio_times[valid]
    full_sample_rate = (len(selected_times) - 1) / (
        selected_times[-1] - selected_times[0]
    )
    active_start, active_end = active_motion_bounds(selected_vio, full_sample_rate)
    selected_times = selected_times[active_start:active_end]
    tracker_poses = tracker_poses[active_start:active_end]
    selected_vio = selected_vio[active_start:active_end]
    midpoint = len(selected_vio) // 2
    halves = []
    transforms = []
    for name, section in (
        ("first", slice(0, midpoint)),
        ("second", slice(midpoint, len(selected_vio))),
    ):
        half_times = selected_times[section]
        half_tracker = tracker_poses[section]
        half_vio = selected_vio[section]
        if len(half_times) < 100:
            raise ValueError(f"{name} calibration half has fewer than 100 poses")
        sample_rate = (len(half_times) - 1) / (half_times[-1] - half_times[0])
        pairs, excitation = relative_pairs(half_tracker, half_vio, sample_rate)
        transform, residuals = fit_transform(pairs)
        transforms.append(transform)
        halves.append(
            {
                "name": name,
                "samples": len(half_times),
                "excitation": excitation,
                "residuals": residuals,
            }
        )
    translation_difference_mm = float(
        np.linalg.norm(transforms[0][:3, 3] - transforms[1][:3, 3]) * 1000.0
    )
    rotation_difference_deg = float(
        np.degrees(
            Rotation.from_matrix(
                transforms[0][:3, :3] @ transforms[1][:3, :3].T
            ).magnitude()
        )
    )
    sufficiently_excited = all(
        half["excitation"]["translation_p95_m"] >= 0.02
        and half["excitation"]["rotation_p95_deg"] >= 8.0
        and half["excitation"]["observability"]["result"] == "PASS"
        for half in halves
    )
    passed = (
        sufficiently_excited
        and translation_difference_mm <= 10.0
        and rotation_difference_deg <= 2.0
    )
    return {
        "result": "PASS" if passed else "FAIL",
        "active_motion_window": {
            "start_sample": active_start,
            "end_sample_exclusive": active_end,
            "samples": active_end - active_start,
            "leading_excluded_s": float(
                vio_times[valid][active_start] - vio_times[valid][0]
            ),
            "trailing_excluded_s": float(
                vio_times[valid][-1] - vio_times[valid][active_end - 1]
            ),
        },
        "halves": halves,
        "translation_difference_mm": translation_difference_mm,
        "rotation_difference_deg": rotation_difference_deg,
        "thresholds": {
            "minimum_translation_p95_m_per_half": 0.02,
            "minimum_rotation_p95_deg_per_half": 8.0,
            "maximum_translation_difference_mm": 10.0,
            "maximum_rotation_difference_deg": 2.0,
        },
    }


def calibrate(
    vio_path: Path,
    tracker_path: Path,
    time_source: str,
    d405_frames_path: Path | None,
    search_ms: float,
    max_gap_s: float,
) -> dict:
    vio_times, vio_positions, vio_quaternions = load_vio(vio_path)
    aligned_vio_times, clock_mapping = map_vio_times(
        vio_times, time_source, d405_frames_path
    )
    tracker_times, tracker_positions, tracker_quaternions, serial = load_tracker(
        tracker_path, time_source
    )
    vio_poses = pose_matrices(vio_positions, vio_quaternions)
    candidates = []
    for offset_ms in np.arange(-search_ms, search_ms + 2.5, 5.0):
        try:
            score, transform, profile = evaluate_offset(
                offset_ms / 1000.0,
                aligned_vio_times,
                vio_poses,
                tracker_times,
                tracker_positions,
                tracker_quaternions,
                max_gap_s,
            )
            candidates.append((score, float(offset_ms), transform, profile))
        except ValueError:
            continue
    if not candidates:
        raise ValueError("no valid clock-offset candidate")
    candidates.sort(key=lambda item: item[0])
    best_ms = candidates[0][1]
    coarse_candidates = [
        {"offset_ms": item[1], "combined_residual_p95_mm": item[0]}
        for item in candidates[:5]
    ]

    def objective(offset_ms: float) -> float:
        try:
            return evaluate_offset(
                offset_ms / 1000.0,
                aligned_vio_times,
                vio_poses,
                tracker_times,
                tracker_positions,
                tracker_quaternions,
                max_gap_s,
            )[0]
        except ValueError:
            return 1.0e6

    refined = minimize_scalar(
        objective,
        bounds=(max(-search_ms, best_ms - 7.5), min(search_ms, best_ms + 7.5)),
        method="bounded",
        options={"xatol": 0.01},
    )
    offset_ms = float(refined.x)
    _, transform, profile = evaluate_offset(
        offset_ms / 1000.0,
        aligned_vio_times,
        vio_poses,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    excitation = profile["excitation"]
    residuals = profile["residuals"]
    stability = split_half_stability(
        offset_ms / 1000.0,
        aligned_vio_times,
        vio_poses,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    offset_boundary_margin_ms = search_ms - abs(offset_ms)
    passed = (
        profile["overlap_samples"] >= 100
        and excitation["translation_p95_m"] >= 0.03
        and excitation["rotation_p95_deg"] >= 10.0
        and excitation["observability"]["result"] == "PASS"
        and residuals["combined_residual_mm"]["p95"] <= 10.0
        and residuals["rotation_residual_deg"]["p95"] <= 3.0
        and stability["result"] == "PASS"
        and offset_boundary_margin_ms >= 5.0
    )
    return {
        "schema": "lighthouse_umi_rigid_calibration_v1",
        "result": "PASS_CANDIDATE" if passed else "DIAGNOSTIC_CANDIDATE",
        "auto_apply": False,
        "tracker_serial": serial,
        "tracker_time_source": time_source,
        "clock_mapping": clock_mapping,
        "tracker_query_offset_ms": offset_ms,
        "offset_semantics": "interpolate tracker at vio_t + tracker_query_offset_ms",
        "offset_search": {
            "range_ms": [-search_ms, search_ms],
            "boundary_margin_ms": offset_boundary_margin_ms,
            "minimum_boundary_margin_ms": 5.0,
            "coarse_best_candidates": coarse_candidates,
        },
        "tracker_T_body": transform.tolist(),
        "translation_norm_mm": float(np.linalg.norm(transform[:3, 3]) * 1000.0),
        "rotation_angle_deg": float(
            np.degrees(Rotation.from_matrix(transform[:3, :3]).magnitude())
        ),
        "profile": profile,
        "split_half_stability": stability,
        "inputs": {
            "vio": str(vio_path.resolve()),
            "tracker": str(tracker_path.resolve()),
            "d405_frames": (
                str(d405_frames_path.resolve()) if d405_frames_path is not None else None
            ),
        },
        "separation_rule": "freeze this calibration; score only separate test recordings",
        "slam_supervision": False,
    }


def write_ground_truth(
    vio_path: Path,
    tracker_path: Path,
    calibration: dict,
    output: Path,
    max_gap_s: float,
    d405_frames_path: Path | None,
) -> int:
    vio_times, _, _ = load_vio(vio_path)
    aligned_vio_times, _ = map_vio_times(
        vio_times, calibration["tracker_time_source"], d405_frames_path
    )
    tracker_times, tracker_positions, tracker_quaternions, serial = load_tracker(
        tracker_path, calibration["tracker_time_source"]
    )
    if serial != calibration["tracker_serial"]:
        raise ValueError(f"tracker serial mismatch: {serial} != {calibration['tracker_serial']}")
    offset_s = float(calibration["tracker_query_offset_ms"]) / 1000.0
    tracker_poses, valid = interpolate_tracker(
        aligned_vio_times + offset_s,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    tracker_T_body = np.asarray(calibration["tracker_T_body"], dtype=float)
    if tracker_T_body.shape != (4, 4):
        raise ValueError("tracker_T_body must be 4x4")
    body_poses = tracker_poses @ tracker_T_body
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        for timestamp, pose in zip(vio_times[valid], body_poses):
            quaternion = Rotation.from_matrix(pose[:3, :3]).as_quat()
            writer.writerow(
                [
                    f"{timestamp:.9f}",
                    *[f"{value:.9f}" for value in pose[:3, 3]],
                    f"{quaternion[3]:.9f}",
                    f"{quaternion[0]:.9f}",
                    f"{quaternion[1]:.9f}",
                    f"{quaternion[2]:.9f}",
                ]
            )
    return int(valid.sum())


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate_parser = subparsers.add_parser("calibrate")
    calibrate_parser.add_argument("--vio", type=Path, required=True)
    calibrate_parser.add_argument("--tracker", type=Path, required=True)
    calibrate_parser.add_argument("--output", type=Path, required=True)
    calibrate_parser.add_argument(
        "--d405-frames",
        type=Path,
        help="采集会话 d405_frames.csv；默认单调时钟对齐时必填",
    )
    calibrate_parser.add_argument(
        "--tracker-time-source",
        choices=("host_monotonic", "device", "host_realtime"),
        default="host_monotonic",
    )
    calibrate_parser.add_argument("--search-ms", type=float, default=50.0)
    calibrate_parser.add_argument("--max-gap-ms", type=float, default=30.0)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--vio", type=Path, required=True)
    apply_parser.add_argument("--tracker", type=Path, required=True)
    apply_parser.add_argument("--calibration", type=Path, required=True)
    apply_parser.add_argument("--output", type=Path, required=True)
    apply_parser.add_argument(
        "--d405-frames",
        type=Path,
        help="当前测试会话的 d405_frames.csv；单调时钟对齐时必填",
    )
    apply_parser.add_argument("--max-gap-ms", type=float, default=30.0)

    args = parser.parse_args()
    if args.command == "calibrate":
        result = calibrate(
            args.vio,
            args.tracker,
            args.tracker_time_source,
            args.d405_frames,
            args.search_ms,
            args.max_gap_ms / 1000.0,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["result"] == "PASS_CANDIDATE" else 2

    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    if calibration.get("result") != "PASS_CANDIDATE":
        raise ValueError("refusing to apply a calibration that did not pass")
    samples = write_ground_truth(
        args.vio,
        args.tracker,
        calibration,
        args.output,
        args.max_gap_ms / 1000.0,
        args.d405_frames,
    )
    print(f"wrote {samples} body ground-truth poses to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
