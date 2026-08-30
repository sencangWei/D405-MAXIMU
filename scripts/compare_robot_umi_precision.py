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


def load_robot(path: Path):
    """Load robot epoch timestamps and FK poses for leader/target/actual."""
    import sys

    fk_root = Path("/home/robot/vla_test/scripts")
    if str(fk_root) not in sys.path:
        sys.path.insert(0, str(fk_root))
    from rebot_rs_urdf_kinematics import gripper_transform

    times: list[float] = []
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
        if "host_wall_epoch_s" not in row:
            continue
        if not all(
            name + "_deg" in row and len(row[name + "_deg"]) >= 6
            for name in poses
        ):
            continue
        try:
            timestamp = float(row["host_wall_epoch_s"])
            transforms = {
                name: gripper_transform(np.radians(np.asarray(row[name + "_deg"][:6], dtype=float)))
                for name in poses
            }
        except (TypeError, ValueError):
            continue
        if not math.isfinite(timestamp):
            continue
        times.append(timestamp)
        for name, transform in transforms.items():
            poses[name].append(transform)
            joints[name].append(np.asarray(row[name + "_deg"][:6], dtype=float))
    if len(times) < 2:
        raise ValueError(f"robot log has fewer than two timestamped samples: {path}")
    order = np.argsort(times, kind="stable")
    source_times = np.asarray(times, dtype=float)[order]
    keep = np.r_[True, np.diff(source_times) > 0.0]
    source_times = source_times[keep]
    result = {name: np.asarray([poses[name][i] for i in order], dtype=float)[keep] for name in poses}
    joint_result = {name: np.asarray([joints[name][i] for i in order], dtype=float)[keep] for name in poses}
    if np.any(np.diff(source_times) <= 0.0):
        raise ValueError(f"robot timestamps are not strictly increasing: {path}")
    return source_times, result, joint_result


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
) -> dict:
    result: dict[str, object] = {}
    for horizon in DEFAULT_HORIZONS_S:
        endpoint_t = times + horizon
        u2, u2_valid = _interpolate_at(endpoint_t, all_umi_times, all_umi_positions, max_gap_s)
        r2, r2_valid = _interpolate_at(endpoint_t, robot_times, all_robot_positions, max_gap_s)
        q2, q2_valid = interpolate_rotation(endpoint_t, robot_times, all_robot_rotations, max_gap_s)
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
    handeye_ape_rmse_mm: float | None = None,
) -> str:
    """Save the customer-facing 3D plus XY/XZ/YZ comparison figure."""
    # The host carries pip Matplotlib 3.10 with NumPy 2 and an Ubuntu
    # Matplotlib 3.5 built against NumPy 1.x.  Plot in a clean system-python
    # subprocess so the report does not depend on whichever package wins
    # ``sys.path`` ordering in the caller.
    data_path = path.with_suffix(".plot.npz")
    payload = {"robot": robot_positions, "umi": umi_positions}
    if handeye_umi_positions is not None:
        payload["umi_tcp"] = handeye_umi_positions
    np.savez(data_path, **payload)
    plot_code = r'''
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

data = np.load(sys.argv[1])
robot = data["robot"]
umi = data["umi"]
umi_tcp = data["umi_tcp"] if "umi_tcp" in data.files else None
output = sys.argv[2]
title = sys.argv[3]
rmse = float(sys.argv[4])
tcp_rmse = None if sys.argv[5] == "none" else float(sys.argv[5])
fig = plt.figure(figsize=(15, 10))
ax = fig.add_subplot(221, projection="3d")
ax.plot(robot[:, 0], robot[:, 1], robot[:, 2], "k-", linewidth=1.5, label="机械臂 TCP (FK)")
ax.plot(umi[:, 0], umi[:, 1], umi[:, 2], color="#1479d1", linewidth=1.1, label="UMI（SE(3)对齐）")
if umi_tcp is not None:
    ax.plot(umi_tcp[:, 0], umi_tcp[:, 1], umi_tcp[:, 2], color="#e67e22", linewidth=1.1, label="UMI→TCP（手眼映射）")
ax.scatter(*robot[0], c="green", s=35, label="起点")
ax.scatter(*robot[-1], c="red", marker="x", s=45, label="终点")
ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)"); ax.set_zlabel("Z (m)")
ax.set_title("三维叠加"); ax.legend(fontsize=8)
for index, (axis_i, axis_j, panel_title) in enumerate(((0, 1, "XY 俯视"), (0, 2, "XZ 侧视"), (1, 2, "YZ 正视")), start=2):
    panel = fig.add_subplot(2, 2, index)
    panel.plot(robot[:, axis_i], robot[:, axis_j], "k-", linewidth=1.2, label="机械臂 TCP")
    panel.plot(umi[:, axis_i], umi[:, axis_j], color="#1479d1", linewidth=1.0, label="UMI")
    if umi_tcp is not None:
        panel.plot(umi_tcp[:, axis_i], umi_tcp[:, axis_j], color="#e67e22", linewidth=1.0, label="UMI→TCP（手眼）")
    panel.scatter(robot[0, axis_i], robot[0, axis_j], c="green", s=22)
    panel.scatter(robot[-1, axis_i], robot[-1, axis_j], c="red", marker="x", s=32)
    panel.set_xlabel("XYZ"[axis_i] + " (m)"); panel.set_ylabel("XYZ"[axis_j] + " (m)")
    panel.set_title(panel_title); panel.grid(True, alpha=0.3)
    if index == 2: panel.legend(fontsize=8)
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
    max_pair_error_ms: float = DEFAULT_MAX_PAIR_ERROR_MS,
    max_bracket_gap_ms: float = DEFAULT_MAX_BRACKET_GAP_MS,
    handeye_path: Path | None = None,
    body_to_camera_path: Path | None = None,
) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    if max_pair_error_ms <= 0.0 or max_bracket_gap_ms <= 0.0:
        raise ValueError("time gates must be positive")
    source_umi, source_kind = resolve_umi_path(slam_dir.resolve(), umi_path)
    umi_t, umi_p, umi_q = load_umi(source_umi)
    robot_t, robot_T, robot_joints = load_robot(robot_path.resolve())
    offset_s = clock_offset_ms / 1000.0
    query_t = umi_t + offset_s
    bracket_gap_s = max_bracket_gap_ms / 1000.0
    actual_p = robot_T["actual"][:, :3, 3]
    actual_q = Rotation.from_matrix(robot_T["actual"][:, :3, :3])
    actual_interp, nearest_delta, valid = interpolate_vector(query_t, robot_t, actual_p, bracket_gap_s)
    _, actual_q_valid = interpolate_rotation(query_t, robot_t, actual_q, bracket_gap_s)
    valid &= actual_q_valid & (nearest_delta <= max_pair_error_ms / 1000.0)
    if valid.sum() < 20:
        raise ValueError(
            f"时间配对有效样本不足: {valid.sum()} (gate={max_pair_error_ms:.3f}ms)"
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
    )
    tracking = _tracking_report(robot_t, robot_T, robot_joints)

    handeye_metric: dict[str, object]
    mapped_paired_umi_p: np.ndarray | None = None
    handeye_evo: dict[str, object] | None = None
    if handeye_path is None:
        handeye_metric = {
            "status": "NOT_REQUESTED",
            "reason": "仅做Kabsch形状诊断；显式提供--handeye后才计算UMI传感器点到TCP的映射。",
        }
    else:
        handeye_path = handeye_path.resolve()
        body_path = (body_to_camera_path or DEFAULT_BODY_TO_CAMERA_PATH).resolve()
        if not handeye_path.is_file():
            raise FileNotFoundError(f"手眼矩阵不存在: {handeye_path}")
        if not body_path.is_file():
            raise FileNotFoundError(f"VINS body_T_cam0配置不存在: {body_path}")
        gripper_camera = load_transform(handeye_path, "T_gripper_camera")
        body_to_camera = load_transform(body_path, "body_T_cam0")
        mapped_all = transform_body_poses_to_tcp(
            umi_p, umi_q, body_to_camera, gripper_camera
        )
        mapped_paired = mapped_all[valid]
        paired_robot_T = _pose_matrices(paired_robot_p, paired_robot_q)
        # VINS world and robot base are independent gauges.  Use exactly one
        # first-valid-pose anchor; never fit Kabsch over the trajectory and
        # never use the operator-provided endpoint as a constraint.
        base_from_world = paired_robot_T[0] @ np.linalg.inv(mapped_paired[0])
        mapped_anchored = base_from_world @ mapped_paired
        mapped_paired_umi_p = mapped_anchored[:, :3, 3]
        mapped_umi_q = Rotation.from_matrix(mapped_anchored[:, :3, :3])
        tcp_position_error = np.linalg.norm(
            mapped_paired_umi_p - paired_robot_p, axis=1
        )
        tcp_rotation_error = np.degrees(
            (
                paired_robot_q.inv() * mapped_umi_q
            ).magnitude()
        )
        handeye_metric = {
            "status": "COMPUTED_DIAGNOSTIC",
            "mapping": "T_world_tcp = T_world_body @ body_T_cam0 @ inv(T_gripper_camera)",
            "handeye_source": str(handeye_path),
            "body_to_camera_source": str(body_path),
            "anchor_policy": "single_first_valid_pose; no_endpoint_constraint; no_trajectory_Kabsch",
            "anchor_base_from_world": base_from_world.tolist(),
            "lever_arm_camera_to_gripper_m": np.linalg.inv(gripper_camera)[:3, 3].tolist(),
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
        "clock": {
            "robot_query_offset_ms": float(clock_offset_ms),
            "offset_policy": "fixed_device_level_cli_value; no per-run optimization",
            "max_association_error_ms": float(max_pair_error_ms),
            "max_interpolation_bracket_gap_ms": float(max_bracket_gap_ms),
            "paired_time_definition": "robot timestamp queried at umi_t + robot_query_offset_ms",
        },
        "sample_counts": {
            "umi_full_corrected": int(len(umi_t)),
            "robot": int(len(robot_t)),
            "paired_after_gate": int(valid.sum()),
            "association_coverage": float(valid.mean()),
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
        _write_tum(tcp_reference_tum, paired_t, paired_robot_p, paired_robot_q)
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
        None
        if mapped_paired_umi_p is None
        else report["absolute_tcp_metric"]["translation_error_mm"]["rmse"],
    )
    report["artifacts"] = {
        "reference_tum": str(reference_tum),
        "estimate_tum": str(estimate_tum),
        "evo": evo_status,
        "trajectory_plot": {"path": str(plot_path), "status": plot_status},
    }
    if mapped_paired_umi_p is not None:
        report["artifacts"]["tcp_handeye"] = {
            "evo": handeye_evo,
            "trajectory_csv": str(output_dir / "matched_tcp_handeye.csv"),
        }
    shutil.copy2(source_umi, output_dir / "umi_full_corrected.csv")
    with (output_dir / "robot_tcp_actual.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z"])
        writer.writerows([[f"{t:.9f}", *[f"{x:.9f}" for x in p]] for t, p in zip(robot_t, actual_p)])
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
        tcp_error = np.linalg.norm(mapped_paired_umi_p - paired_robot_p, axis=1)
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
                        *[f"{x:.9f}" for x in rp],
                        *[f"{x:.9f}" for x in up],
                        f"{err * 1000.0:.6f}",
                    ]
                    for t, d, rp, up, err in zip(
                        paired_t, nearest_delta[valid], paired_robot_p,
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
        f"- 机械臂参考：从臂 `actual_deg` 经 B601-RS URDF FK 得到 TCP",
        f"- 固定时间偏移：{report['clock']['robot_query_offset_ms']:.3f} ms；没有按本次数据自动优化",
        f"- 配对门限：最近样本时间差 ≤ {report['clock']['max_association_error_ms']:.3f} ms；插值括区 ≤ {report['clock']['max_interpolation_bracket_gap_ms']:.3f} ms",
        f"- 配对：{report['sample_counts']['paired_after_gate']}/{report['sample_counts']['umi_full_corrected']}（覆盖率 {report['sample_counts']['association_coverage'] * 100.0:.2f}%）",
        f"- 共同有效区间：{report['time_overlap_s']:.3f} s；UMI/机械臂累计距离：{report['relative_motion']['path_length_umi_m']:.4f}/{report['relative_motion']['path_length_robot_actual_m']:.4f} m（差 {report['relative_motion']['path_length_difference_m']:.4f} m）",
        "",
        "## 时间配对",
        "",
        f"P50/P95/最大：{assoc['median']:.3f}/{assoc['p95']:.3f}/{assoc['max']:.3f} ms。",
        "超过门限的样本已剔除，没有用最近点硬配替代插值。",
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
            "EVO 原始输出见 `evo/evo_ape.txt` 与 `evo/evo_rpe_1s.txt`；三维/三视图对比见 `robot_tcp_vs_umi_3d.png`。",
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
    args = parser.parse_args()
    report = process(
        slam_dir=args.slam_dir,
        umi_path=args.umi,
        robot_path=args.robot,
        output_dir=args.out,
        clock_offset_ms=args.clock_offset_ms,
        max_pair_error_ms=args.max_pair_error_ms,
        max_bracket_gap_ms=args.max_bracket_gap_ms,
        handeye_path=args.handeye,
        body_to_camera_path=args.body_to_camera,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
