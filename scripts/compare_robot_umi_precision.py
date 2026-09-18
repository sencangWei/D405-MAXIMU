#!/usr/bin/env python3
"""Compare a full corrected UMI trajectory with the follower TCP feedback.

This evaluator is scoring-only.  It never feeds robot data back into SLAM and
does not use an operator supplied endpoint.  The default UMI source is the
full-rate ``vio_corrected_stream.csv`` produced by the replay pipeline.

The robot log contains three different signals:

* ``leader_deg``: master/operator input;
* ``target_deg``: mapped command sent to the follower;
* ``actual_deg``: follower motor feedback.

The physical TCP reference is therefore ``actual_deg``.  Target-versus-actual
tracking quality is reported separately and is not mixed into SLAM error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


DEFAULT_UMI_NAME = "vio_corrected_stream.csv"
DEFAULT_MAX_PAIR_ERROR_MS = 10.0
DEFAULT_MAX_BRACKET_GAP_MS = 60.0
DEFAULT_HORIZONS_S = (0.1, 0.5, 1.0)
DEFAULT_BODY_TO_CAMERA_PATH = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/"
    "docker2_release/formal_runtime_calibration/vins_config.yaml"
)


def _finite_rows(rows: list[dict], required: set[str], path: Path) -> list[dict]:
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid trajectory schema: {path}")
    return rows


def load_umi(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load t_sec,x,y,z,qw,qx,qy,qz, accepting nanosecond timestamps."""
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    _finite_rows(rows, {"t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"}, path)
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    if np.nanmedian(times) > 1.0e12:
        times /= 1.0e9
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows],
        dtype=float,
    )
    quaternions = np.asarray(
        [
            [
                float(row["qx"]),
                float(row["qy"]),
                float(row["qz"]),
                float(row["qw"]),
            ]
            for row in rows
        ],
        dtype=float,
    )
    if not np.all(np.isfinite(times)) or not np.all(np.isfinite(positions)):
        raise ValueError(f"non-finite UMI trajectory: {path}")
    order = np.argsort(times, kind="stable")
    times, positions, quaternions = times[order], positions[order], quaternions[order]
    keep = np.r_[True, np.diff(times) > 0.0]
    times, positions, quaternions = times[keep], positions[keep], quaternions[keep]
    if len(times) < 2:
        raise ValueError(f"UMI trajectory has fewer than two samples: {path}")
    norms = np.linalg.norm(quaternions, axis=1)
    if np.any(norms < 1.0e-9):
        raise ValueError(f"zero quaternion in UMI trajectory: {path}")
    quaternions /= norms[:, None]
    return times, positions, quaternions


_FLOAT_TOKEN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _validate_transform(matrix: np.ndarray, path: Path, key: str) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
        raise ValueError(f"{path}缺少有限的4x4变换矩阵{key}")
    if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1.0e-8):
        raise ValueError(f"{path}:{key}不是齐次变换矩阵")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1.0e-5) or not np.isclose(
        np.linalg.det(rotation), 1.0, atol=1.0e-5
    ):
        raise ValueError(f"{path}:{key}的旋转部分不是合法SO(3)")
    return matrix


def load_transform(path: Path, key: str) -> np.ndarray:
    """Load a 4x4 JSON transform or an OpenCV-matrix YAML transform.

    OpenCV YAML is intentionally parsed without PyYAML: the ``!!opencv-matrix``
    tag is not accepted by safe loaders and the evaluator only needs this
    small, auditable subset.
    """
    path = path.resolve()
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if key not in value:
            raise ValueError(f"{path}缺少矩阵{key}")
        return _validate_transform(np.asarray(value[key], dtype=float), path, key)
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"{re.escape(key)}:\s*!!opencv-matrix.*?data:\s*\[(.*?)\]",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        raise ValueError(f"{path}缺少OpenCV矩阵{key}")
    values = [float(token) for token in re.findall(_FLOAT_TOKEN, match.group(1))]
    if len(values) != 16:
        raise ValueError(f"{path}:{key}应包含16个数，实际为{len(values)}")
    return _validate_transform(np.asarray(values, dtype=float).reshape(4, 4), path, key)


def load_gripper_tcp_offset(path: Path) -> np.ndarray:
    """Load a measured gripper-end -> physical TCP offset.

    The explicit ``measured`` flag is required so the zero-valued example
    cannot accidentally be used as a claimed left-jaw calibration.
    """
    path = path.resolve()
    if path.suffix.lower() != ".json":
        raise ValueError(f"左夹爪TCP偏移必须使用JSON文件: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "umi_gripper_tcp_offset_v1":
        raise ValueError(f"{path}不是 umi_gripper_tcp_offset_v1 文件")
    if value.get("measured") is not True:
        raise ValueError(f"{path}尚未标记 measured: true；请先实测夹爪TCP偏移")
    return load_transform(path, "T_gripper_tcp")


def load_gripper_tcp_frame(path: Path) -> str:
    """Return the physical TCP frame label stored alongside its transform."""
    value = json.loads(path.resolve().read_text(encoding="utf-8"))
    frame = value.get("frame_child")
    if not isinstance(frame, str) or not frame.strip():
        raise ValueError(f"{path}缺少有效的 frame_child")
    return frame.strip()


def load_robot_clock_offset_ms(path: Path) -> tuple[float, str]:
    """Load a fixed robot-query clock offset from a validated JSON artifact.

    The offset is an evaluator-level mapping ``robot(umi_t + offset)``.  It
    must not be confused with the camera--IMU ``td`` baked into VINS.
    """
    path = path.resolve()
    if path.suffix.lower() != ".json":
        raise ValueError(f"设备时间偏移必须使用JSON文件: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    schema = value.get("schema")
    if schema not in {
        "robot_umi_clock_offset_report_v1",
        "robot_umi_clock_offset_calibration_v1",
    }:
        raise ValueError(f"{path}不是受支持的设备时间偏移文件")
    try:
        offset_ms = float(value["robot_query_offset_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}缺少有效的 robot_query_offset_ms") from exc
    if not np.isfinite(offset_ms) or abs(offset_ms) > 5000.0:
        raise ValueError(f"{path}的时间偏移超出合理范围: {offset_ms} ms")
    return offset_ms, str(path)


def _pose_matrices(positions: np.ndarray, rotations: Rotation) -> np.ndarray:
    positions = np.asarray(positions, dtype=float)
    matrices = np.tile(np.eye(4), (len(positions), 1, 1))
    matrices[:, :3, :3] = rotations.as_matrix()
    matrices[:, :3, 3] = positions
    return matrices


def transform_body_poses_to_tcp(
    positions: np.ndarray,
    quaternions_xyzw: np.ndarray,
    body_to_camera: np.ndarray,
    gripper_camera: np.ndarray,
) -> np.ndarray:
    """Map VINS body poses to the physical TCP.

    VINS publishes ``T_world_body``.  The product calibration stores
    ``body_T_cam0`` (camera -> body), while the hand-eye solver stores
    ``T_gripper_camera`` (camera -> gripper).  Therefore the physical chain is
    ``T_world_tcp = T_world_body @ body_T_cam0 @ inv(T_gripper_camera)``.
    """
    body_to_camera = _validate_transform(
        body_to_camera, Path("body_to_camera"), "body_T_cam0"
    )
    gripper_camera = _validate_transform(
        gripper_camera, Path("gripper_camera"), "T_gripper_camera"
    )
    body = _pose_matrices(
        np.asarray(positions, dtype=float),
        Rotation.from_quat(np.asarray(quaternions_xyzw, dtype=float)),
    )
    return body @ body_to_camera @ np.linalg.inv(gripper_camera)


def apply_gripper_tcp_offset(
    world_gripper: np.ndarray,
    gripper_tcp: np.ndarray,
) -> np.ndarray:
    """Apply a fixed TCP frame expressed from the FK ``gripper_end`` frame.

    ``world_gripper`` maps coordinates in the robot FK frame (the URDF
    ``gripper_end`` origin) into the world frame.  ``gripper_tcp`` maps the
    desired physical TCP coordinates into that FK frame.  Keeping this as a
    separate composition makes the measured left-jaw lever arm explicit and
    prevents silently treating the hand-eye origin as the jaw TCP.
    """
    world_gripper = np.asarray(world_gripper, dtype=float)
    if world_gripper.ndim != 3 or world_gripper.shape[1:] != (4, 4):
        raise ValueError("world_gripper must have shape (N, 4, 4)")
    gripper_tcp = _validate_transform(
        gripper_tcp, Path("gripper_tcp"), "T_gripper_tcp"
    )
    return world_gripper @ gripper_tcp


def load_robot(path: Path):
    """Load robot poses using one timestamp domain and report its provenance.

    New recorder rows carry the SocketCAN kernel receive event for the
    feedback frames that produced ``actual_deg``.  Legacy rows only contain
    the file-writer observation time.  Never mix those domains silently: if a
    log contains both, retain only event-timed rows and report the discarded
    legacy rows to the caller.
    """
    import sys

    fk_root = Path("/home/robot/vla_test/scripts")
    if str(fk_root) not in sys.path:
        sys.path.insert(0, str(fk_root))
    from rebot_rs_urdf_kinematics import gripper_transform

    timed_rows: list[tuple[float, dict]] = []
    legacy_rows: list[tuple[float, dict]] = []
    poses: dict[str, list[np.ndarray]] = {
        "leader": [],
        "target": [],
        "actual": [],
    }
    joints: dict[str, list[np.ndarray]] = {name: [] for name in poses}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not all(
            name + "_deg" in row and len(row[name + "_deg"]) >= 6
            for name in poses
        ):
            continue
        try:
            event_source = row.get("feedback_timestamp_source")
            event_timestamp = row.get("feedback_frame_rx_wall_epoch")
            timestamp = float(event_timestamp) if event_timestamp not in (None, "") else float("nan")
            is_event_timed = (
                event_source == "can_kernel_rx_timestamp"
                and math.isfinite(timestamp)
            )
            if not is_event_timed:
                timestamp = float(row.get("host_wall_epoch_s", "nan"))
            transforms = {
                name: gripper_transform(np.radians(np.asarray(row[name + "_deg"][:6], dtype=float)))
                for name in poses
            }
        except (TypeError, ValueError):
            continue
        if not math.isfinite(timestamp):
            continue
        (timed_rows if is_event_timed else legacy_rows).append((timestamp, (transforms, row)))
    if timed_rows:
        selected_rows = timed_rows
        timestamp_source = "can_kernel_rx_timestamp"
        discarded_legacy_rows = len(legacy_rows)
    else:
        selected_rows = legacy_rows
        timestamp_source = "host_wall_epoch_s_legacy"
        discarded_legacy_rows = 0
    if len(selected_rows) < 2:
        raise ValueError(f"robot log has fewer than two timestamped samples: {path}")
    times: list[float] = []
    for timestamp, (transforms, row) in selected_rows:
        times.append(timestamp)
        for name, transform in transforms.items():
            poses[name].append(transform)
            joints[name].append(np.asarray(row[name + "_deg"][:6], dtype=float))
    order = np.argsort(times, kind="stable")
    source_times = np.asarray(times, dtype=float)[order]
    keep = np.r_[True, np.diff(source_times) > 0.0]
    source_times = source_times[keep]
    result = {name: np.asarray([poses[name][i] for i in order], dtype=float)[keep] for name in poses}
    joint_result = {name: np.asarray([joints[name][i] for i in order], dtype=float)[keep] for name in poses}
    if np.any(np.diff(source_times) <= 0.0):
        raise ValueError(f"robot timestamps are not strictly increasing: {path}")
    return source_times, result, joint_result, {
        "timestamp_source": timestamp_source,
        "uses_event_time": timestamp_source == "can_kernel_rx_timestamp",
        "event_rows": len(timed_rows),
        "legacy_rows": len(legacy_rows),
        "discarded_legacy_rows": discarded_legacy_rows,
        "semantics": (
            "SocketCAN kernel CLOCK_REALTIME receive event for feedback frames; "
            "converted with one startup realtime-minus-monotonic offset"
            if timestamp_source == "can_kernel_rx_timestamp"
            else "legacy host wall-clock timestamp written after feedback polling"
        ),
    }


def _brackets(query: np.ndarray, source: np.ndarray, max_gap_s: float):
    right = np.searchsorted(source, query, side="right")
    inside = (query >= source[0]) & (query <= source[-1])
    right = np.clip(right, 1, len(source) - 1)
    left = right - 1
    gap = source[right] - source[left]
    valid = inside & (gap <= max_gap_s)
    return left, right, gap, valid


def interpolate_vector(
    query: np.ndarray,
    source_times: np.ndarray,
    source_values: np.ndarray,
    max_gap_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    left, right, gap, valid = _brackets(query, source_times, max_gap_s)
    alpha = np.divide(
        query - source_times[left],
        gap,
        out=np.zeros_like(query, dtype=float),
        where=gap > 0.0,
    )
    values = (1.0 - alpha[:, None]) * source_values[left] + alpha[:, None] * source_values[right]
    nearest_delta = np.minimum(np.abs(query - source_times[left]), np.abs(source_times[right] - query))
    return values, nearest_delta, valid


def interpolate_rotation(
    query: np.ndarray,
    source_times: np.ndarray,
    source_rotations: Rotation,
    max_gap_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    _, _, gap, valid = _brackets(query, source_times, max_gap_s)
    inside = (query >= source_times[0]) & (query <= source_times[-1])
    valid &= inside
    clipped = np.clip(query, source_times[0], source_times[-1])
    values = Slerp(source_times, source_rotations)(clipped).as_quat()
    return values, valid


def kabsch(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0.0:
        vt[-1] *= -1.0
        rotation = vt.T @ u.T
    translation = target_center - rotation @ source_center
    return rotation, translation


def _summary(values: np.ndarray, scale: float = 1.0) -> dict:
    values = np.asarray(values, dtype=float)
    return {
        "count": int(values.size),
        "rmse": float(np.sqrt(np.mean(values * values)) * scale),
        "mean": float(np.mean(values) * scale),
        "median": float(np.median(values) * scale),
        "p95": float(np.percentile(values, 95) * scale),
        "max": float(np.max(values) * scale),
    }


def _nearest_indices(query: np.ndarray, source: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    right = np.searchsorted(source, query, side="right")
    right = np.clip(right, 1, len(source) - 1)
    left = right - 1
    choose_right = np.abs(source[right] - query) < np.abs(source[left] - query)
    indices = np.where(choose_right, right, left)
    return indices, np.abs(source[indices] - query)


def _interpolate_at(
    query: np.ndarray,
    source_times: np.ndarray,
    source_values: np.ndarray,
    max_gap_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    values, nearest_delta, valid = interpolate_vector(query, source_times, source_values, max_gap_s)
    return values, valid & np.isfinite(nearest_delta)


def _relative_metrics(
    times: np.ndarray,
    umi_positions: np.ndarray,
    umi_rotations: Rotation,
    robot_positions: np.ndarray,
    robot_rotations: Rotation,
    all_umi_times: np.ndarray,
    all_umi_positions: np.ndarray,
    all_umi_rotations: Rotation,
    robot_times: np.ndarray,
    all_robot_positions: np.ndarray,
    all_robot_rotations: Rotation,
    max_gap_s: float,
    aligned_rotation: np.ndarray,
    robot_time_offset_s: float = 0.0,
) -> dict:
    result: dict[str, object] = {}
    for horizon in DEFAULT_HORIZONS_S:
        endpoint_t = times + horizon
        u2, u2_valid = _interpolate_at(endpoint_t, all_umi_times, all_umi_positions, max_gap_s)
        # ``times`` is expressed in the UMI clock.  The robot endpoint must
        # use the same fixed query offset as the paired start pose; omitting
        # it here silently biases every relative-motion metric.
        robot_endpoint_t = endpoint_t + robot_time_offset_s
        r2, r2_valid = _interpolate_at(robot_endpoint_t, robot_times, all_robot_positions, max_gap_s)
        q2, q2_valid = interpolate_rotation(robot_endpoint_t, robot_times, all_robot_rotations, max_gap_s)
        q_u2, q_u2_valid = interpolate_rotation(endpoint_t, all_umi_times, all_umi_rotations, max_gap_s)
        valid = u2_valid & r2_valid & q2_valid & q_u2_valid
        if not np.any(valid):
            result[f"{horizon:.1f}s"] = {"samples": 0, "result": "NO_VALID_PAIRS"}
            continue
        du = u2[valid] - umi_positions[valid]
        dr = r2[valid] - robot_positions[valid]
        du_aligned = (aligned_rotation @ du.T).T
        length_error = np.abs(np.linalg.norm(du, axis=1) - np.linalg.norm(dr, axis=1))
        vector_error = np.linalg.norm(du_aligned - dr, axis=1)
        denom = np.linalg.norm(du_aligned, axis=1) * np.linalg.norm(dr, axis=1)
        cosine = np.divide(
            np.sum(du_aligned * dr, axis=1), denom,
            out=np.ones_like(denom), where=denom > 1.0e-9,
        )
        direction_error = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
        # Direction is ill-conditioned for nearly stationary samples. Keep
        # those samples in length/vector/angle statistics, but do not let a
        # sub-millimetre jitter become a meaningless 90-degree direction hit.
        moving = (np.linalg.norm(du_aligned, axis=1) >= 1.0e-3) & (
            np.linalg.norm(dr, axis=1) >= 1.0e-3
        )
        base_u = umi_rotations[valid]
        base_r = robot_rotations[valid]
        end_u = Rotation.from_quat(q_u2[valid])
        end_r = Rotation.from_quat(q2[valid])
        relative_u = base_u.inv() * end_u
        relative_r = base_r.inv() * end_r
        angle_error = np.degrees((relative_r.inv() * relative_u).magnitude())
        direction_summary = _summary(direction_error[moving]) if np.any(moving) else None
        result[f"{horizon:.1f}s"] = {
            "samples": int(valid.sum()),
            "displacement_length_error_mm": _summary(length_error, 1000.0),
            "aligned_vector_error_mm": _summary(vector_error, 1000.0),
            "direction_error_deg": direction_summary,
            "direction_samples": int(moving.sum()),
            "direction_min_displacement_mm": 1.0,
            "angle_increment_error_deg": _summary(angle_error),
        }
    return result


def _path_length(times: np.ndarray, positions: np.ndarray, max_segment_s: float) -> float:
    dt = np.diff(times)
    step = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    return float(step[dt <= max_segment_s].sum())


def _path_length_in_interval(
    times: np.ndarray,
    positions: np.ndarray,
    start_s: float,
    end_s: float,
    max_segment_s: float,
) -> float:
    """Measure path length only over the common UMI/robot time interval."""
    keep = (times >= start_s) & (times <= end_s)
    if keep.sum() < 2:
        return 0.0
    return _path_length(times[keep], positions[keep], max_segment_s)


def _tracking_report(
    robot_times: np.ndarray,
    transforms: dict[str, np.ndarray],
    joints: dict[str, np.ndarray],
) -> dict:
    actual = transforms["actual"]
    target = transforms["target"]
    tcp_error = np.linalg.norm(target[:, :3, 3] - actual[:, :3, 3], axis=1)
    rotation_error = np.degrees(
        (Rotation.from_matrix(actual[:, :3, :3]).inv() * Rotation.from_matrix(target[:, :3, :3])).magnitude()
    )
    leader_target_joint_error = np.linalg.norm(joints["leader"] - joints["target"], axis=1)
    target_actual_joint_error = np.linalg.norm(joints["target"] - joints["actual"], axis=1)
    return {
        "samples": int(len(robot_times)),
        "duration_s": float(robot_times[-1] - robot_times[0]),
        "target_vs_actual_tcp_error_mm": _summary(tcp_error, 1000.0),
        "target_vs_actual_orientation_error_deg": _summary(rotation_error),
        "leader_vs_target_joint_error_deg": _summary(leader_target_joint_error),
        "target_vs_actual_joint_error_deg": _summary(target_actual_joint_error),
        "joint_error_axes": ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_yaw", "wrist_roll"],
        "leader_and_target_are_not_ground_truth": True,
        "reference_signal": "actual_deg -> follower TCP FK; leader_deg/target_deg are command-side diagnostics",
    }


def _write_tum(path: Path, times: np.ndarray, positions: np.ndarray, rotations: Rotation) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for timestamp, position, quaternion in zip(times, positions, rotations.as_quat()):
            stream.write(
                f"{timestamp:.9f} {position[0]:.9f} {position[1]:.9f} {position[2]:.9f} "
                f"{quaternion[0]:.9f} {quaternion[1]:.9f} {quaternion[2]:.9f} {quaternion[3]:.9f}\n"
            )


def _run_evo(
    output_dir: Path,
    reference_tum: Path,
    estimate_tum: Path,
    *,
    align: bool = True,
) -> dict:
    """Run EVO when installed, preserving stdout/stderr as audit evidence."""
    evo_dir = output_dir / "evo"
    evo_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    user_site = "/home/robot/.local/lib/python3.10/site-packages"
    env["PYTHONPATH"] = user_site + (
        ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    ape = ["evo_ape", "tum", str(reference_tum), str(estimate_tum)]
    rpe_1s = ["evo_rpe", "tum", str(reference_tum), str(estimate_tum)]
    if align:
        ape.append("--align")
        rpe_1s.append("--align")
    commands = {
        "ape": ape + ["--save_results", str(evo_dir / "evo_ape.zip")],
        "rpe_1s": rpe_1s
        + [
            # EVO RPE supports frame/degree/radian/metre deltas, not seconds;
            # the corrected stream is 30 fps, so 30 frames is the 1 s window.
            "--delta", "30", "--delta_unit", "f",
            "--save_results", str(evo_dir / "evo_rpe_1s.zip"),
        ],
    }
    result: dict[str, object] = {}
    for name, command in commands.items():
        text_path = evo_dir / f"evo_{name}.txt"
        try:
            completed = subprocess.run(
                command, check=False, capture_output=True, text=True, env=env
            )
        except OSError as exc:
            text_path.write_text(f"command unavailable: {exc}\n", encoding="utf-8")
            result[name] = {"status": "UNAVAILABLE", "error": str(exc)}
            continue
        text_path.write_text(
            completed.stdout
            + ("\n[stderr]\n" + completed.stderr if completed.stderr else ""),
            encoding="utf-8",
        )
        result[name] = {
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "returncode": int(completed.returncode),
            "stdout_file": str(text_path),
            "result_file": str(evo_dir / f"evo_{name}.zip"),
        }
    return result


def _plot_comparison(
    path: Path,
    robot_positions: np.ndarray,
    umi_positions: np.ndarray,
    title: str,
    ape_rmse_mm: float,
    handeye_umi_positions: np.ndarray | None = None,
    mapped_robot_positions: np.ndarray | None = None,
    handeye_ape_rmse_mm: float | None = None,
) -> str:
    """Save one or two static 3D comparisons (no 2D projections)."""
    # The host carries pip Matplotlib 3.10 with NumPy 2 and an Ubuntu
    # Matplotlib 3.5 built against NumPy 1.x.  Plot in a clean system-python
    # subprocess so the report does not depend on whichever package wins
    # ``sys.path`` ordering in the caller.
    data_path = path.with_suffix(".plot.npz")
    payload = {"robot": robot_positions, "umi": umi_positions}
    if handeye_umi_positions is not None and mapped_robot_positions is not None:
        payload["robot_mapped"] = mapped_robot_positions
        payload["umi_tcp"] = handeye_umi_positions
    np.savez(data_path, **payload)
    plot_code = r'''
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "AR PL UMing CN", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

data = np.load(sys.argv[1])
robot = data["robot"]
umi = data["umi"]
robot_mapped = data["robot_mapped"] if "robot_mapped" in data.files else robot
umi_tcp = data["umi_tcp"] if "umi_tcp" in data.files else umi
output = sys.argv[2]
title = sys.argv[3]
rmse = float(sys.argv[4])
tcp_rmse = None if sys.argv[5] == "none" else float(sys.argv[5])
has_mapped = "robot_mapped" in data.files and "umi_tcp" in data.files
fig, axes = plt.subplots(
    1, 2 if has_mapped else 1, subplot_kw={"projection": "3d"},
    figsize=(16 if has_mapped else 8, 7), squeeze=False,
)
flat_axes = axes.ravel()
panels = [
    (flat_axes[0], robot, umi, "base_link中未映射：机械臂 gripper_end vs UMI（SE(3)形状对齐）", "UMI（形状对齐）", "#1479d1"),
]
if has_mapped:
    panels.append(
        (flat_axes[1], robot_mapped, umi_tcp, "已映射：机械臂 UMI右爪尖 TCP vs UMI→TCP", "UMI→TCP（手眼映射）", "#e67e22")
    )
for panel, robot_line, umi_line, panel_title, umi_label, umi_color in panels:
    panel.plot(robot_line[:, 0], robot_line[:, 1], robot_line[:, 2], "k-", linewidth=1.5, label="机械臂 TCP (FK)")
    panel.plot(umi_line[:, 0], umi_line[:, 1], umi_line[:, 2], color=umi_color, linewidth=1.1, label=umi_label)
    panel.scatter(*robot_line[0], c="green", s=35, label="起点")
    panel.scatter(*robot_line[-1], c="red", marker="x", s=45, label="终点")
    panel.set_xlabel("X (m)"); panel.set_ylabel("Y (m)"); panel.set_zlabel("Z (m)")
    panel.set_title(panel_title); panel.legend(fontsize=8)
suffix = f" | 手眼TCP RMS {tcp_rmse:.3f} mm" if tcp_rmse is not None else ""
fig.suptitle(f"{title}\n有效配对 {len(robot)} 点 | Kabsch APE RMS {rmse:.3f} mm{suffix}")
fig.tight_layout(); fig.savefig(output, dpi=180); plt.close(fig)
'''
    try:
        completed = subprocess.run(
            ["/usr/bin/python3", "-s", "-c", plot_code,
             str(data_path), str(path), title, f"{ape_rmse_mm:.9f}",
             "none" if handeye_ape_rmse_mm is None else f"{handeye_ape_rmse_mm:.9f}"],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        )
    except OSError as exc:
        return f"UNAVAILABLE: {exc}"
    finally:
        data_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return f"UNAVAILABLE: {detail[-1] if detail else 'plot subprocess failed'}"
    return "PASS"


def _write_interactive_comparison(
    path: Path,
    robot_positions: np.ndarray,
    umi_positions: np.ndarray,
    title: str,
    handeye_umi_positions: np.ndarray | None = None,
    mapped_robot_positions: np.ndarray | None = None,
) -> str:
    """Write a dependency-free HTML viewer with mouse-rotatable 3D scenes."""
    scenes = [
        {
            "title": "base_link中未映射：机械臂 gripper_end vs UMI（SE(3)形状对齐）",
            "robot": np.asarray(robot_positions, dtype=float).tolist(),
            "umi": np.asarray(umi_positions, dtype=float).tolist(),
            "color": "#1479d1",
        },
    ]
    if handeye_umi_positions is not None and mapped_robot_positions is not None:
        scenes.append(
            {
                "title": "已映射：机械臂 UMI右爪尖 TCP vs UMI→TCP",
                "robot": np.asarray(mapped_robot_positions, dtype=float).tolist(),
                "umi": np.asarray(handeye_umi_positions, dtype=float).tolist(),
                "color": "#e67e22",
            }
        )
    html = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>body{margin:0;background:#17191d;color:#e8eaed;font:14px system-ui,sans-serif}header{padding:14px 20px 8px}h1{font-size:20px;margin:0 0 6px}p{margin:0;color:#b9c0c8}.toolbar{padding:8px 20px}.grid{display:grid;grid-template-columns:repeat(2,minmax(360px,1fr));gap:12px;padding:0 12px 16px}.panel{background:#202329;border:1px solid #3b424b;border-radius:6px;overflow:hidden}.panel h2{font-size:15px;font-weight:500;padding:10px 12px;margin:0}canvas{display:block;width:100%;height:620px;background:#101216;cursor:grab}canvas:active{cursor:grabbing}.legend{padding:8px 12px 10px;color:#c8cdd3}.dot{display:inline-block;width:22px;border-top:3px solid currentColor;vertical-align:middle;margin:0 5px 0 14px}@media(max-width:900px){.grid{grid-template-columns:1fr}canvas{height:520px}}</style></head>
<body><header><h1>__TITLE__ · 三维轨迹对比</h1><p>拖动鼠标旋转，滚轮缩放；绿色圆点为起点，红色叉为终点。</p></header>
<div class="toolbar"><button id="reset">重置视角</button></div><main class="grid">
<section class="panel"><h2 id="h0"></h2><canvas id="c0"></canvas><div class="legend"><span style="color:#e8eaed"><i class="dot"></i>机械臂 TCP</span><span style="color:#1479d1"><i class="dot"></i>UMI（形状对齐）</span></div></section>
<section class="panel" id="p1"><h2 id="h1"></h2><canvas id="c1"></canvas><div class="legend"><span style="color:#e8eaed"><i class="dot"></i>机械臂 UMI右爪尖 TCP</span><span style="color:#e67e22"><i class="dot"></i>UMI→TCP（手眼映射）</span></div></section></main>
<script>const S=__SCENES__,V=[];function scene(canvas,d){const x=canvas.getContext('2d'),a=d.robot.concat(d.umi),lo=[0,1,2].map(k=>Math.min(...a.map(p=>p[k]))),hi=[0,1,2].map(k=>Math.max(...a.map(p=>p[k]))),c=lo.map((v,k)=>(v+hi[k])/2),r=Math.max(...hi.map((v,k)=>v-lo[k]),1e-6)*.55;let yaw=-.65,pitch=.42,zoom=1,drag=0,last=[0,0];function rot(p){let X=p[0]-c[0],Y=p[1]-c[1],Z=p[2]-c[2],cy=Math.cos(yaw),sy=Math.sin(yaw),x1=cy*X+sy*Z,z1=-sy*X+cy*Z,cp=Math.cos(pitch),sp=Math.sin(pitch);return[x1,cp*Y-sp*z1,sp*Y+cp*z1]}function proj(p,w,h){let q=rot(p),sc=Math.min(w,h)*.82*zoom/r,de=Math.max(.65,1+q[2]/r*.18);return[w/2+q[0]*sc/de,h/2-q[1]*sc/de]}function line(P,col,w,h){x.strokeStyle=col;x.lineWidth=1.8;x.beginPath();P.forEach((p,i)=>{let q=proj(p,w,h);i?x.lineTo(q[0],q[1]):x.moveTo(q[0],q[1])});x.stroke()}function mark(p,col,cross,w,h){let q=proj(p,w,h);x.strokeStyle=x.fillStyle=col;x.lineWidth=3;if(cross){x.beginPath();x.moveTo(q[0]-7,q[1]-7);x.lineTo(q[0]+7,q[1]+7);x.moveTo(q[0]+7,q[1]-7);x.lineTo(q[0]-7,q[1]+7);x.stroke()}else{x.beginPath();x.arc(q[0],q[1],7,0,7);x.fill()}}function draw(){let w=canvas.clientWidth,h=canvas.clientHeight;x.clearRect(0,0,w,h);x.strokeStyle='#343a43';x.lineWidth=1;for(let i=-4;i<=4;i++){let p=proj([c[0]+r*i/4,c[1]-r,c[2]],w,h),q=proj([c[0]+r*i/4,c[1]+r,c[2]],w,h);x.beginPath();x.moveTo(p[0],p[1]);x.lineTo(q[0],q[1]);x.stroke()}line(d.robot,'#e8eaed',w,h);line(d.umi,d.color,w,h);mark(d.robot[0],'#16a34a',0,w,h);mark(d.robot[d.robot.length-1],'#ef4444',1,w,h)}function resize(){let z=devicePixelRatio||1;canvas.width=canvas.clientWidth*z;canvas.height=canvas.clientHeight*z;x.setTransform(z,0,0,z,0,0);draw()}canvas.onpointerdown=e=>{drag=1;last=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(!drag)return;let dx=e.clientX-last[0],dy=e.clientY-last[1];last=[e.clientX,e.clientY];yaw+=dx*.01;pitch=Math.max(-1.45,Math.min(1.45,pitch+dy*.01));draw()};canvas.onpointerup=e=>{drag=0;canvas.releasePointerCapture(e.pointerId)};canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(.25,Math.min(5,zoom*Math.exp(-e.deltaY*.001)));draw()};addEventListener('resize',resize);resize();return()=>{yaw=-.65;pitch=.42;zoom=1;draw()}}document.getElementById('p1').style.display=S.length>1?'block':'none';S.forEach((d,i)=>{document.getElementById('h'+i).textContent=d.title;V.push(scene(document.getElementById('c'+i),d))});document.getElementById('reset').onclick=()=>V.forEach(f=>f());</script></body></html>'''.replace('__TITLE__', title).replace('__SCENES__', json.dumps(scenes, ensure_ascii=False, separators=(',', ':')))
    try:
        path.write_text(html, encoding="utf-8")
    except OSError as exc:
        return f"UNAVAILABLE: {exc}"
    return "PASS"


def resolve_umi_path(slam_dir: Path, umi_path: Path | None) -> tuple[Path, str]:
    if umi_path is not None:
        return umi_path.resolve(), "explicit"
    candidate = slam_dir / DEFAULT_UMI_NAME
    if not candidate.is_file():
        raise FileNotFoundError(
            f"完整30fps轨迹不存在: {candidate}; 不自动回退到 loop_output/vio_loop.csv"
        )
    return candidate.resolve(), "full_corrected_stream"


def process(
    *,
    slam_dir: Path,
    robot_path: Path,
    output_dir: Path,
    umi_path: Path | None = None,
    clock_offset_ms: float = 0.0,
    clock_offset_file: Path | None = None,
    max_pair_error_ms: float = DEFAULT_MAX_PAIR_ERROR_MS,
    max_bracket_gap_ms: float = DEFAULT_MAX_BRACKET_GAP_MS,
    handeye_path: Path | None = None,
    body_to_camera_path: Path | None = None,
    gripper_tcp_offset_path: Path | None = None,
) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    if max_pair_error_ms <= 0.0 or max_bracket_gap_ms <= 0.0:
        raise ValueError("time gates must be positive")
    if clock_offset_file is not None:
        if abs(clock_offset_ms) > 1.0e-12:
            raise ValueError("--clock-offset-ms 与 --clock-offset-file 不能同时提供")
        clock_offset_ms, clock_offset_source = load_robot_clock_offset_ms(
            clock_offset_file
        )
    else:
        clock_offset_source = "cli"
    source_umi, source_kind = resolve_umi_path(slam_dir.resolve(), umi_path)
    umi_t, umi_p, umi_q = load_umi(source_umi)

    # Resolve all fixed frame transforms before building the robot reference.
    # When a physical TCP offset is requested, both trajectories must describe
    # the same point.  Applying it only to the UMI side changes the apparent
    # turn radius and makes rotation-heavy runs look much worse.
    handeye_path = handeye_path.resolve() if handeye_path is not None else None
    body_path = (body_to_camera_path or DEFAULT_BODY_TO_CAMERA_PATH).resolve()
    gripper_camera = None
    body_to_camera = None
    tcp_offset = None
    tcp_frame = "gripper_end"
    if handeye_path is not None:
        if not handeye_path.is_file():
            raise FileNotFoundError(f"手眼矩阵不存在: {handeye_path}")
        if not body_path.is_file():
            raise FileNotFoundError(f"VINS body_T_cam0配置不存在: {body_path}")
        gripper_camera = load_transform(handeye_path, "T_gripper_camera")
        body_to_camera = load_transform(body_path, "body_T_cam0")
        if gripper_tcp_offset_path is not None:
            gripper_tcp_offset_path = gripper_tcp_offset_path.resolve()
            tcp_offset = load_gripper_tcp_offset(gripper_tcp_offset_path)
            tcp_frame = load_gripper_tcp_frame(gripper_tcp_offset_path)
    elif gripper_tcp_offset_path is not None:
        raise ValueError("--gripper-tcp-offset 必须与 --handeye 一起提供")

    robot_t, robot_T, robot_joints, robot_time = load_robot(robot_path.resolve())
    raw_actual_T = robot_T["actual"]
    requested_clock_offset_ms = float(clock_offset_ms)
    if robot_time["uses_event_time"]:
        # Event timestamps are already in the same host monotonic/epoch
        # contract as the UMI stream.  Applying the historical fixed offset
        # again would double-shift the trajectory.
        offset_s = 0.0
        effective_clock_offset_ms = 0.0
        effective_clock_offset_source = "robot_can_kernel_event_timestamp"
    else:
        offset_s = clock_offset_ms / 1000.0
        effective_clock_offset_ms = float(clock_offset_ms)
        effective_clock_offset_source = clock_offset_source
    query_t = umi_t + offset_s
    bracket_gap_s = max_bracket_gap_ms / 1000.0
    # Shape and relative-motion diagnostics stay on the URDF gripper_end
    # origin.  A raw VINS body trajectory cannot be compared directly to a
    # physical jaw TCP without first applying the hand-eye chain.
    actual_p = raw_actual_T[:, :3, 3]
    actual_q = Rotation.from_matrix(raw_actual_T[:, :3, :3])
    actual_interp, nearest_delta, valid = interpolate_vector(query_t, robot_t, actual_p, bracket_gap_s)
    _, actual_q_valid = interpolate_rotation(query_t, robot_t, actual_q, bracket_gap_s)
    # The robot stream is sampled independently from the camera (both are
    # nominally 30 Hz but their phases and rates are not identical).  Once the
    # robot pose is linearly interpolated at the UMI timestamp, the distance to
    # the nearest raw robot sample is sampling quantisation, not clock error.
    # Gating on it drops roughly half of an otherwise valid 30 Hz overlap when
    # a small rate mismatch accumulates. Keep it as a diagnostic only; the
    # interpolation bracket and quaternion domain remain the validity gates.
    valid &= actual_q_valid
    if valid.sum() < 20:
        raise ValueError(
            f"时间配对有效样本不足: {valid.sum()} (interpolation bracket={max_bracket_gap_ms:.3f}ms)"
        )
    paired_t = umi_t[valid]
    paired_umi_p = umi_p[valid]
    paired_umi_q = umi_q[valid]
    paired_robot_p = actual_interp[valid]
    paired_robot_q = Rotation.from_quat(
        interpolate_rotation(query_t[valid], robot_t, actual_q, bracket_gap_s)[0]
    )
    align_R, align_t = kabsch(paired_umi_p, paired_robot_p)
    aligned_umi_p = (align_R @ paired_umi_p.T).T + align_t
    shape_error = np.linalg.norm(aligned_umi_p - paired_robot_p, axis=1)
    all_umi_R = Rotation.from_quat(umi_q)
    relative = _relative_metrics(
        paired_t,
        paired_umi_p,
        Rotation.from_quat(paired_umi_q),
        paired_robot_p,
        paired_robot_q,
        umi_t,
        umi_p,
        all_umi_R,
        robot_t,
        actual_p,
        actual_q,
        bracket_gap_s,
        align_R,
        offset_s,
    )
    # Keep actuator tracking in the URDF gripper_end frame.  It diagnoses
    # follower control independently and must not be mixed with TCP mapping.
    tracking = _tracking_report(robot_t, robot_T, robot_joints)

    handeye_metric: dict[str, object]
    mapped_paired_umi_p: np.ndarray | None = None
    paired_tcp_p: np.ndarray | None = None
    handeye_evo: dict[str, object] | None = None
    if handeye_path is None:
        handeye_metric = {
            "status": "NOT_REQUESTED",
            "reason": "仅做Kabsch形状诊断；显式提供--handeye后才计算UMI传感器点到TCP的映射。",
        }
    else:
        assert gripper_camera is not None
        assert body_to_camera is not None
        mapped_gripper_all = transform_body_poses_to_tcp(
            umi_p, umi_q, body_to_camera, gripper_camera
        )
        tcp_reference_T = (
            apply_gripper_tcp_offset(raw_actual_T, tcp_offset)
            if tcp_offset is not None
            else raw_actual_T
        )
        tcp_reference_p = tcp_reference_T[:, :3, 3]
        tcp_reference_q = Rotation.from_matrix(tcp_reference_T[:, :3, :3])
        tcp_reference_interp, _, tcp_reference_valid = interpolate_vector(
            query_t, robot_t, tcp_reference_p, bracket_gap_s
        )
        _, tcp_reference_q_valid = interpolate_rotation(
            query_t, robot_t, tcp_reference_q, bracket_gap_s
        )
        handeye_valid = valid & tcp_reference_valid & tcp_reference_q_valid
        if handeye_valid.sum() < 20:
            raise ValueError(
                f"TCP时间配对有效样本不足: {handeye_valid.sum()}"
            )
        if tcp_offset is not None:
            mapped_all = apply_gripper_tcp_offset(mapped_gripper_all, tcp_offset)
        else:
            mapped_all = mapped_gripper_all
        mapped_paired = mapped_all[handeye_valid]
        paired_tcp_p = tcp_reference_interp[handeye_valid]
        paired_tcp_q = Rotation.from_quat(
            interpolate_rotation(query_t[handeye_valid], robot_t, tcp_reference_q, bracket_gap_s)[0]
        )
        paired_robot_T = _pose_matrices(paired_tcp_p, paired_tcp_q)
        # VINS world and robot base are independent gauges.  Use exactly one
        # first-valid-pose anchor; never fit Kabsch over the trajectory and
        # never use the operator-provided endpoint as a constraint.
        base_from_world = paired_robot_T[0] @ np.linalg.inv(mapped_paired[0])
        mapped_anchored = base_from_world @ mapped_paired
        mapped_paired_umi_p = mapped_anchored[:, :3, 3]
        mapped_umi_q = Rotation.from_matrix(mapped_anchored[:, :3, :3])
        tcp_position_error = np.linalg.norm(
            mapped_paired_umi_p - paired_tcp_p, axis=1
        )
        tcp_rotation_error = np.degrees(
            (
                paired_tcp_q.inv() * mapped_umi_q
            ).magnitude()
        )
        handeye_metric = {
            "status": "COMPUTED_DIAGNOSTIC",
            "mapping": (
                "T_world_tcp = T_world_body @ body_T_cam0 @ "
                "inv(T_gripper_camera) @ T_gripper_tcp"
                if tcp_offset is not None
                else "T_world_gripper_end = T_world_body @ body_T_cam0 @ inv(T_gripper_camera)"
            ),
            "handeye_source": str(handeye_path),
            "body_to_camera_source": str(body_path),
            "tcp_frame": tcp_frame,
            "tcp_offset_source": str(gripper_tcp_offset_path.resolve())
            if gripper_tcp_offset_path is not None
            else None,
            "tcp_offset_T_gripper_tcp": tcp_offset.tolist() if tcp_offset is not None else None,
            "anchor_policy": "single_first_valid_pose; no_endpoint_constraint; no_trajectory_Kabsch",
            "anchor_base_from_world": base_from_world.tolist(),
            "lever_arm_camera_to_gripper_m": np.linalg.inv(gripper_camera)[:3, 3].tolist(),
            "robot_reference_transform": (
                "T_world_tcp = T_world_gripper_end @ T_gripper_tcp"
                if tcp_offset is not None
                else "T_world_gripper_end"
            ),
            "robot_reference_frame": tcp_frame,
            "translation_error_mm": _summary(tcp_position_error, 1000.0),
            "rotation_error_deg": _summary(tcp_rotation_error),
            "handeye_solver_residual_note": "该指标仍包含手眼矩阵自身残差；当前矩阵的求解残差需在手眼JSON中单独查看。",
        }
    overlap = (paired_t[-1] - paired_t[0]) if len(paired_t) > 1 else 0.0
    overlap_start = float(paired_t[0])
    overlap_end = float(paired_t[-1])
    umi_path_overlap = _path_length_in_interval(
        umi_t, umi_p, overlap_start, overlap_end, 0.2
    )
    robot_path_overlap = _path_length_in_interval(
        robot_t, actual_p, overlap_start + offset_s, overlap_end + offset_s, 0.2
    )
    report = {
        "schema": "umi_robot_precision_report_v2",
        "result": "PASS" if valid.sum() >= 20 else "FAIL",
        "umi_source": str(source_umi),
        "umi_source_kind": source_kind,
        "robot_source": str(robot_path.resolve()),
        "robot_fk_coordinate_frame": "base_link",
        "robot_reference_frame": "gripper_end",
        "robot_reference_transform": "T_base_link_gripper_end",
        "absolute_tcp_reference_frame": tcp_frame if handeye_path is not None else None,
        "clock": {
            "robot_query_offset_ms": effective_clock_offset_ms,
            "requested_legacy_offset_ms": requested_clock_offset_ms,
            "offset_policy": (
                "zero additional offset; robot CAN kernel event timestamp is already "
                "in the UMI host epoch domain"
                if robot_time["uses_event_time"]
                else "fixed device-level file or CLI value; no per-run optimization"
            ),
            "offset_source": effective_clock_offset_source,
            "robot_timestamp_source": robot_time["timestamp_source"],
            "robot_timestamp_semantics": robot_time["semantics"],
            "discarded_legacy_robot_rows": robot_time["discarded_legacy_rows"],
            "max_association_error_ms": float(max_pair_error_ms),
            "max_interpolation_bracket_gap_ms": float(max_bracket_gap_ms),
            "paired_time_definition": (
                "robot pose linearly interpolated at umi_t in the shared host epoch"
                if robot_time["uses_event_time"]
                else "robot pose linearly interpolated at umi_t + robot_query_offset_ms"
            ),
            "sample_delta_gate_policy": "diagnostic_only_with_linear_interpolation; not a validity gate",
        },
        "sample_counts": {
            "umi_full_corrected": int(len(umi_t)),
            "robot": int(len(robot_t)),
            "paired_after_gate": int(valid.sum()),
            "association_coverage": float(valid.mean()),
            "nearest_sample_over_gate": int(np.count_nonzero(valid & (nearest_delta > max_pair_error_ms / 1000.0))),
        },
        "time_overlap_s": float(overlap),
        "association_delta_ms": _summary(nearest_delta[valid], 1000.0),
        "shape_se3_kabsch": {
            "alignment": "diagnostic_only_SE3_Kabsch_no_scale_no_endpoint_constraint",
            "ape_translation_mm": _summary(shape_error, 1000.0),
            "rotation_matrix": align_R.tolist(),
            "translation_m": align_t.tolist(),
        },
        "relative_motion": {
            "coordinate_invariant_length_and_direction_metrics": True,
            "path_length_interval_definition": "common paired UMI time interval; source samples outside overlap excluded",
            "path_length_umi_m": umi_path_overlap,
            "path_length_robot_actual_m": robot_path_overlap,
            "path_length_difference_m": umi_path_overlap - robot_path_overlap,
            "horizons": relative,
        },
        "follower_tracking": tracking,
        "absolute_tcp_metric": handeye_metric,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    aligned_umi_rotations = Rotation.from_matrix(align_R) * Rotation.from_quat(paired_umi_q)
    reference_tum = output_dir / "robot_actual_matched.tum"
    estimate_tum = output_dir / "umi_aligned.tum"
    _write_tum(reference_tum, paired_t, paired_robot_p, paired_robot_q)
    _write_tum(estimate_tum, paired_t, aligned_umi_p, aligned_umi_rotations)
    evo_status = _run_evo(output_dir, reference_tum, estimate_tum)
    if mapped_paired_umi_p is not None:
        tcp_dir = output_dir / "tcp_handeye"
        tcp_dir.mkdir(parents=True, exist_ok=True)
        tcp_reference_tum = tcp_dir / "robot_actual_matched.tum"
        tcp_estimate_tum = tcp_dir / "umi_tcp_handeye_anchored.tum"
        # The TCP comparison must use the same physical point on both sides.
        # ``paired_robot_p`` is the URDF ``gripper_end`` origin; when a
        # gripper TCP offset is requested, the robot reference is
        # ``paired_tcp_p`` instead.  Using the former here silently re-added
        # the lever arm on only the UMI side in the exported artifacts.
        _write_tum(tcp_reference_tum, paired_t, paired_tcp_p, paired_tcp_q)
        _write_tum(tcp_estimate_tum, paired_t, mapped_paired_umi_p, mapped_umi_q)
        handeye_evo = _run_evo(
            tcp_dir, tcp_reference_tum, tcp_estimate_tum, align=False
        )
        handeye_metric["evo"] = handeye_evo
        handeye_metric["artifacts"] = {
            "reference_tum": str(tcp_reference_tum),
            "estimate_tum": str(tcp_estimate_tum),
        }
    plot_path = output_dir / "robot_tcp_vs_umi_3d.png"
    plot_status = _plot_comparison(
        plot_path,
        paired_robot_p,
        aligned_umi_p,
        output_dir.name,
        report["shape_se3_kabsch"]["ape_translation_mm"]["rmse"],
        mapped_paired_umi_p,
        paired_tcp_p,
        None
        if mapped_paired_umi_p is None
        else report["absolute_tcp_metric"]["translation_error_mm"]["rmse"],
    )
    interactive_plot_path = output_dir / "robot_vs_umi_two_3d.html"
    interactive_plot_status = _write_interactive_comparison(
        interactive_plot_path,
        paired_robot_p,
        aligned_umi_p,
        output_dir.name,
        mapped_paired_umi_p,
        paired_tcp_p,
    )
    report["artifacts"] = {
        "reference_tum": str(reference_tum),
        "estimate_tum": str(estimate_tum),
        "evo": evo_status,
        "trajectory_plot": {
            "path": str(plot_path),
            "status": plot_status,
            "views": 2 if mapped_paired_umi_p is not None else 1,
            "description": "静态三维视图，无二维投影",
        },
        "trajectory_plot_interactive": {
            "path": str(interactive_plot_path),
            "status": interactive_plot_status,
            "views": 2 if mapped_paired_umi_p is not None else 1,
            "description": "浏览器鼠标旋转/滚轮缩放；无外部依赖",
        },
    }
    if mapped_paired_umi_p is not None:
        report["artifacts"]["tcp_handeye"] = {
            "evo": handeye_evo,
            "trajectory_csv": str(output_dir / "matched_tcp_handeye.csv"),
        }
    shutil.copy2(source_umi, output_dir / "umi_full_corrected.csv")
    with (output_dir / "robot_reference_actual.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z"])
        writer.writerows([[f"{t:.9f}", *[f"{x:.9f}" for x in p]] for t, p in zip(robot_t, actual_p)])
    # Keep the historical artifact name for downstream tools.  The report and
    # the canonical file above identify whether this is gripper_end or TCP.
    shutil.copy2(output_dir / "robot_reference_actual.csv", output_dir / "robot_tcp_actual.csv")
    with (output_dir / "matched_relative.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "t_sec", "association_delta_ms",
                "umi_x", "umi_y", "umi_z",
                "robot_actual_x", "robot_actual_y", "robot_actual_z",
                "aligned_umi_x", "aligned_umi_y", "aligned_umi_z",
                "shape_error_mm",
            ]
        )
        writer.writerows(
            [
                [
                    f"{t:.9f}", f"{d * 1000.0:.6f}",
                    *[f"{x:.9f}" for x in up],
                    *[f"{x:.9f}" for x in rp],
                    *[f"{x:.9f}" for x in ap],
                    f"{err * 1000.0:.6f}",
                ]
                for t, d, up, rp, ap, err in zip(
                    paired_t, nearest_delta[valid], paired_umi_p, paired_robot_p, aligned_umi_p, shape_error
                )
            ]
        )
    if mapped_paired_umi_p is not None:
        # ``paired_robot_p`` is the raw gripper_end origin.  The exported TCP
        # error must be computed against the interpolated physical TCP point,
        # otherwise every sample includes the full lever arm as a false error.
        tcp_error = np.linalg.norm(mapped_paired_umi_p - paired_tcp_p, axis=1)
        with (output_dir / "matched_tcp_handeye.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "t_sec", "association_delta_ms",
                    "robot_tcp_x", "robot_tcp_y", "robot_tcp_z",
                    "umi_tcp_x", "umi_tcp_y", "umi_tcp_z",
                    "tcp_error_mm",
                ]
            )
            writer.writerows(
                [
                    [
                        f"{t:.9f}", f"{d * 1000.0:.6f}",
                        *[f"{x:.9f}" for x in tcp_rp],
                        *[f"{x:.9f}" for x in up],
                        f"{err * 1000.0:.6f}",
                    ]
                    for t, d, tcp_rp, up, err in zip(
                        paired_t, nearest_delta[valid], paired_tcp_p,
                        mapped_paired_umi_p, tcp_error
                    )
                ]
            )
    (output_dir / "trajectory_metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "precision_report.md").write_text(_markdown_report(report), encoding="utf-8")
    return report


def _markdown_report(report: dict) -> str:
    shape = report["shape_se3_kabsch"]["ape_translation_mm"]
    assoc = report["association_delta_ms"]
    tracking = report["follower_tracking"]
    lines = [
        "# UMI 全速率轨迹 / 从臂 TCP 评估",
        "",
        f"- UMI 输入：`{report['umi_source']}`（{report['umi_source_kind']}）",
        f"- 机械臂参考：从臂 `actual_deg` 经 B601-RS URDF FK 得到 `{report['robot_reference_frame']}`，位于 `{report['robot_fk_coordinate_frame']}` 坐标系",
        f"- 参考点变换：`{report['robot_reference_transform']}`；绝对 TCP 小节另行使用同一杆臂变换",
        f"- 机械臂时间源：`{report['clock']['robot_timestamp_source']}`（{report['clock']['robot_timestamp_semantics']}）",
        f"- 额外时间偏移：{report['clock']['robot_query_offset_ms']:.3f} ms；事件时间源不会叠加历史固定偏移",
        f"- 时间配对：在统一主机时间轴上对从臂 TCP 做线性插值；插值括区 ≤ {report['clock']['max_interpolation_bracket_gap_ms']:.3f} ms",
        f"- 配对：{report['sample_counts']['paired_after_gate']}/{report['sample_counts']['umi_full_corrected']}（覆盖率 {report['sample_counts']['association_coverage'] * 100.0:.2f}%）",
        f"- 共同有效区间：{report['time_overlap_s']:.3f} s；UMI/机械臂累计距离：{report['relative_motion']['path_length_umi_m']:.4f}/{report['relative_motion']['path_length_robot_actual_m']:.4f} m（差 {report['relative_motion']['path_length_difference_m']:.4f} m）",
        "",
        "## 时间配对",
        "",
        f"最近原始机械臂样本距离（仅诊断）P50/P95/最大：{assoc['median']:.3f}/{assoc['p95']:.3f}/{assoc['max']:.3f} ms。",
        f"其中 {report['sample_counts']['nearest_sample_over_gate']} 个样本超过 {report['clock']['max_association_error_ms']:.3f} ms；这些样本仍使用线性插值，不作为时钟误差剔除。",
        "",
        "## B：一次刚体对齐的形状诊断",
        "",
        f"APE 平移 RMS/P95/最大：{shape['rmse']:.3f}/{shape['p95']:.3f}/{shape['max']:.3f} mm。",
        "该项只用于形状诊断，不使用终点答案、不强制闭环，也不是绝对 TCP 精度。",
        "",
        "## A：相对运动指标",
        "",
        "| 时间窗 | 位移长度误差 RMS | 对齐位移向量误差 RMS | 方向误差 RMS | 角度增量误差 RMS |",
        "|---:|---:|---:|---:|---:|",
    ]
    for horizon, values in report["relative_motion"]["horizons"].items():
        if values.get("samples", 0) == 0:
            lines.append(f"| {horizon} | 无有效样本 | - | - | - |")
            continue
        direction = values["direction_error_deg"]
        direction_text = f"{direction['rmse']:.3f}°" if direction else "无足够位移"
        lines.append(
            f"| {horizon} | {values['displacement_length_error_mm']['rmse']:.3f} mm | "
            f"{values['aligned_vector_error_mm']['rmse']:.3f} mm | "
            f"{direction_text} | "
            f"{values['angle_increment_error_deg']['rmse']:.3f}° |"
        )
    lines.extend(
        [
            "",
            "## 从臂跟随误差（独立，不计入 SLAM 误差）",
            "",
            f"目标 TCP → 从臂实际 TCP RMS/P95/最大：{tracking['target_vs_actual_tcp_error_mm']['rmse']:.3f}/{tracking['target_vs_actual_tcp_error_mm']['p95']:.3f}/{tracking['target_vs_actual_tcp_error_mm']['max']:.3f} mm。",
            f"主臂 `leader_deg` → 映射目标 `target_deg` 关节误差 RMS：{tracking['leader_vs_target_joint_error_deg']['rmse']:.3f}°；目标 → 从臂实际关节误差 RMS：{tracking['target_vs_actual_joint_error_deg']['rmse']:.3f}°。",
            "UMI 固定在从臂上，因此 SLAM 对比使用的是 `actual_deg`；主臂和目标只作为独立跟随诊断，不混入 SLAM 误差。",
            "",
            "",
            "累计距离只统计 UMI 与机械臂的共同有效时间区间；不把录制前后的机械臂运动计入 SLAM 误差。",
            "",
            "EVO 原始输出见 `evo/evo_ape.txt` 与 `evo/evo_rpe_1s.txt`；两组三维对比见 `robot_tcp_vs_umi_3d.png`，可旋转 HTML 见 `robot_vs_umi_two_3d.html`。",
        ]
    )
    tcp = report["absolute_tcp_metric"]
    if tcp.get("status") == "COMPUTED_DIAGNOSTIC":
        tcp_error = tcp["translation_error_mm"]
        tcp_rot = tcp["rotation_error_deg"]
        lines.extend(
            [
                "",
                "## C：UMI→TCP 手眼映射（转弯半径诊断）",
                "",
                "本节才使用手眼矩阵，把 VINS 的传感器原点映射到实际夹爪 TCP。",
                f"变换链：`{tcp['mapping']}`。",
                f"平移误差 RMS/P95/最大：{tcp_error['rmse']:.3f}/{tcp_error['p95']:.3f}/{tcp_error['max']:.3f} mm。",
                f"姿态误差 RMS/P95/最大：{tcp_rot['rmse']:.3f}/{tcp_rot['p95']:.3f}/{tcp_rot['max']:.3f}°。",
                "世界坐标只用第一个有效配对姿态做一次固定锚定；没有使用终点答案，也没有再次对整段轨迹做Kabsch。",
                f"手眼矩阵：`{tcp['handeye_source']}`；VINS外参：`{tcp['body_to_camera_source']}`。",
                f"评估坐标点：`{tcp['tcp_frame']}`。",
                (
                    f"TCP固定偏移（`{tcp['tcp_frame']}`）：`{tcp['tcp_offset_source']}`。"
                    if tcp.get("tcp_offset_source")
                    else "未提供夹爪TCP偏移，因此本节的点是 URDF `gripper_end` 原点。"
                ),
                "该结果是带手眼残差的 TCP 映射诊断；对应无对齐 EVO 结果和 CSV 位于 `tcp_handeye/` 与 `matched_tcp_handeye.csv`。",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## C：UMI→TCP 手眼映射",
                "",
                "本次未提供 `--handeye`，因此没有做 TCP 映射；当前 Kabsch 结果只代表传感器轨迹形状诊断。",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="完整 corrected UMI 轨迹与从臂 TCP 的时间插值/相对误差评估")
    parser.add_argument("--slam-dir", type=Path, required=True)
    parser.add_argument("--robot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--umi", type=Path, help="可选；默认使用 --slam-dir/vio_corrected_stream.csv")
    parser.add_argument("--clock-offset-ms", type=float, default=0.0)
    parser.add_argument(
        "--clock-offset-file",
        type=Path,
        help=(
            "可选；读取设备级 robot_query_offset_ms JSON。与 --clock-offset-ms 互斥；"
            "不改变 VINS 的相机-IMU td"
        ),
    )
    parser.add_argument("--max-pair-error-ms", type=float, default=DEFAULT_MAX_PAIR_ERROR_MS)
    parser.add_argument("--max-bracket-gap-ms", type=float, default=DEFAULT_MAX_BRACKET_GAP_MS)
    parser.add_argument(
        "--handeye",
        type=Path,
        help="可选；包含 T_gripper_camera 的手眼 JSON。提供后额外计算 UMI→TCP 映射诊断",
    )
    parser.add_argument(
        "--body-to-camera",
        type=Path,
        default=DEFAULT_BODY_TO_CAMERA_PATH,
        help="可选；包含 body_T_cam0 的 VINS OpenCV YAML",
    )
    parser.add_argument(
        "--gripper-tcp-offset",
        type=Path,
        help=(
            "可选；包含 T_gripper_tcp 的4x4 JSON。该变换必须由 gripper_end "
            "坐标系指向实测的 UMI 夹爪 TCP；不提供时只评估 gripper_end 原点"
        ),
    )
    args = parser.parse_args()
    report = process(
        slam_dir=args.slam_dir,
        umi_path=args.umi,
        robot_path=args.robot,
        output_dir=args.out,
        clock_offset_ms=args.clock_offset_ms,
        clock_offset_file=args.clock_offset_file,
        max_pair_error_ms=args.max_pair_error_ms,
        max_bracket_gap_ms=args.max_bracket_gap_ms,
        handeye_path=args.handeye,
        body_to_camera_path=args.body_to_camera,
        gripper_tcp_offset_path=args.gripper_tcp_offset,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
