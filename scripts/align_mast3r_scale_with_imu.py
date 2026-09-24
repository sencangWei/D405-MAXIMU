#!/usr/bin/env python3
"""Recover MASt3R-SLAM's metric scale from the UMI's calibrated IMU.

This offline initialization may use the UMI VIO orientation; it never uses
robot/TCP or Lighthouse trajectories. Lighthouse remains scoring-only.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.vins_transform import load_vins_imu_rotation


IMU_DTYPE = np.dtype(
    [
        ("ts", "<f8"),
        ("counter", "<u4"),
        ("gx", "<f4"),
        ("gy", "<f4"),
        ("gz", "<f4"),
        ("ax", "<f4"),
        ("ay", "<f4"),
        ("az", "<f4"),
        ("temp", "<f4"),
    ]
)
FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not set(FIELDS).issubset(rows[0]):
        raise ValueError(f"invalid trajectory: {path}")
    times = np.array([float(row["t_sec"]) for row in rows])
    positions = np.array(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.array(
        [
            [float(row[key]) for key in ("qx", "qy", "qz", "qw")]
            for row in rows
        ]
    )
    if np.any(np.diff(times) <= 0):
        raise ValueError("trajectory timestamps must be strictly increasing")
    return times, positions, quaternions


def camera_epoch_to_monotonic(
    frame_csv: Path, stream: str, query_epoch: np.ndarray
) -> np.ndarray:
    rows = list(csv.DictReader(frame_csv.open(newline="", encoding="utf-8")))
    epoch_key = f"{stream}_device_ms"
    mono_key = f"{stream}_mono"
    pairs = [
        (float(row[epoch_key]) / 1000.0, float(row[mono_key]))
        for row in rows
        if row.get(epoch_key) and row.get(mono_key)
    ]
    epoch, monotonic = np.asarray(pairs).T
    if query_epoch[0] < epoch[0] or query_epoch[-1] > epoch[-1]:
        raise ValueError("trajectory lies outside D405 timestamp table")
    return np.interp(query_epoch, epoch, monotonic)


def load_calibrated_acceleration(
    imu_bin: Path, calibration_path: Path
) -> tuple[np.ndarray, np.ndarray]:
    records = np.fromfile(imu_bin, dtype=IMU_DTYPE)
    if len(records) < 10 or np.any(np.diff(records["ts"]) <= 0):
        raise ValueError("invalid or non-monotonic imu.bin")
    calibration = yaml.safe_load(calibration_path.read_text(encoding="utf-8"))
    accel = calibration["accelerometer"]
    matrix = np.asarray(accel["matrix"], dtype=float)
    offset = np.asarray(accel["offset_g"], dtype=float)
    raw = np.column_stack((records["ax"], records["ay"], records["az"]))
    corrected_mps2 = (raw @ matrix.T + offset) * 9.805
    corrected_mps2 = corrected_mps2 @ load_vins_imu_rotation().T
    return records["ts"].astype(float), corrected_mps2


def load_body_t_camera(path: Path, key: str = "body_T_cam0") -> np.ndarray:
    storage = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    matrix = storage.getNode(key).mat()
    storage.release()
    if matrix is None or matrix.shape != (4, 4):
        raise ValueError(f"missing 4x4 {key} in {path}")
    return matrix


def select_scale_attitude(
    visual_times: np.ndarray,
    visual_camera_quaternions: np.ndarray,
    body_t_camera: np.ndarray,
    reference_times: np.ndarray | None = None,
    reference_body_quaternions: np.ndarray | None = None,
) -> tuple[np.ndarray, Rotation, dict]:
    visual_body = Rotation.from_quat(visual_camera_quaternions) * Rotation.from_matrix(
        body_t_camera[:3, :3]
    ).inv()
    if reference_times is None:
        return np.ones(len(visual_times), dtype=bool), visual_body, {"source": "mast3r"}
    if reference_body_quaternions is None or np.any(np.diff(reference_times) <= 0):
        raise ValueError("invalid onboard orientation trajectory")
    overlap = (visual_times >= reference_times[0]) & (
        visual_times <= reference_times[-1]
    )
    if np.count_nonzero(overlap) < 6:
        raise ValueError("insufficient onboard orientation overlap")
    reference_body = Slerp(
        reference_times, Rotation.from_quat(reference_body_quaternions)
    )(visual_times[overlap])
    world_alignment = (visual_body[overlap] * reference_body.inv()).mean()
    aligned_body = world_alignment * reference_body
    difference_deg = np.rad2deg(
        (visual_body[overlap].inv() * aligned_body).magnitude()
    )
    return overlap, aligned_body, {
        "source": "onboard_orientation_trajectory",
        "overlap_ratio": float(np.mean(overlap)),
        "visual_orientation_difference_p95_deg": float(
            np.percentile(difference_deg, 95)
        ),
    }


def choose_attitude_scale(legacy: dict, onboard: dict, stereo: dict) -> tuple[str, dict]:
    stereo_scale = stereo.get("scale_m_per_mast3r_unit")
    if (
        stereo.get("result") != "PASS"
        or not isinstance(stereo_scale, (int, float))
        or not np.isfinite(stereo_scale)
        or stereo_scale <= 0
    ):
        raise ValueError("attitude selection needs a validated stereo scale")

    def discrepancy(result: dict) -> float:
        scale = result["scale"]
        if (
            not np.isfinite(scale)
            or scale <= 0
            or not 7 <= result["gravity_norm"] <= 12
            or result["rank"] < result["unknowns"]
        ):
            return float("inf")
        return float(abs(np.log(scale / stereo_scale)))

    differences = {
        "mast3r": discrepancy(legacy),
        "onboard_orientation_trajectory": discrepancy(onboard),
    }
    source = min(differences, key=differences.get)
    if not np.isfinite(differences[source]):
        raise ValueError("neither IMU attitude candidate has a valid scale")
    return source, {
        "policy": "smallest_log_scale_difference_to_validated_onboard_stereo",
        "stereo_scale": float(stereo_scale),
        "mast3r_scale": float(legacy["scale"]),
        "onboard_scale": float(onboard["scale"]),
        "log_scale_differences": {
            key: value if np.isfinite(value) else None
            for key, value in differences.items()
        },
    }


def integrate_specific_force(
    start_imu: float,
    end_imu: float,
    td_s: float,
    visual_times_mono: np.ndarray,
    body_rotations: Rotation,
    imu_times: np.ndarray,
    accel_body: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    inside = imu_times[(imu_times > start_imu) & (imu_times < end_imu)]
    grid = np.concatenate(([start_imu], inside, [end_imu]))
    accel = np.column_stack(
        [np.interp(grid, imu_times, accel_body[:, axis]) for axis in range(3)]
    )
    rotations = Slerp(visual_times_mono, body_rotations)(grid - td_s)
    accel_world = rotations.apply(accel)
    delta_velocity = np.zeros(3)
    delta_position = np.zeros(3)
    velocity_bias_jacobian = np.zeros((3, 3))
    position_bias_jacobian = np.zeros((3, 3))
    for index, dt in enumerate(np.diff(grid)):
        average_accel = 0.5 * (accel_world[index] + accel_world[index + 1])
        average_bias_jacobian = -0.5 * (
            rotations[index].as_matrix() + rotations[index + 1].as_matrix()
        )
        delta_position += delta_velocity * dt + 0.5 * average_accel * dt * dt
        position_bias_jacobian += (
            velocity_bias_jacobian * dt
            + 0.5 * average_bias_jacobian * dt * dt
        )
        delta_velocity += average_accel * dt
        velocity_bias_jacobian += average_bias_jacobian * dt
    return (
        delta_position,
        delta_velocity,
        position_bias_jacobian,
        velocity_bias_jacobian,
    )


def solve_visual_inertial_scale(
    visual_times_mono: np.ndarray,
    visual_positions: np.ndarray,
    body_rotations: Rotation,
    imu_times: np.ndarray,
    accel_body: np.ndarray,
    camera_position_in_body: np.ndarray,
    td_s: float,
    node_stride: int,
    max_hop: int = 1,
) -> dict:
    indices = np.arange(0, len(visual_times_mono), node_stride, dtype=int)
    if indices[-1] != len(visual_times_mono) - 1:
        indices = np.append(indices, len(visual_times_mono) - 1)
    if len(indices) < 6:
        raise ValueError("insufficient visual-inertial nodes")
    node_times = visual_times_mono[indices]
    node_positions = visual_positions[indices]
    node_rotations = body_rotations[indices]
    velocity_offset = 0
    gravity_offset = 3 * len(indices)
    scale_offset = gravity_offset + 3
    bias_offset = scale_offset + 1
    unknowns = bias_offset + 3
    rows = []
    targets = []
    interval_count = 0
    for i in range(len(indices) - 1):
        for hop in range(1, min(max_hop, len(indices) - i - 1) + 1):
            j = i + hop
            dt = node_times[j] - node_times[i]
            start_imu = node_times[i] + td_s
            end_imu = node_times[j] + td_s
            if start_imu < imu_times[0] or end_imu > imu_times[-1]:
                continue
            (
                delta_p_imu,
                delta_v_imu,
                position_bias_jacobian,
                velocity_bias_jacobian,
            ) = integrate_specific_force(
                start_imu,
                end_imu,
                td_s,
                visual_times_mono,
                body_rotations,
                imu_times,
                accel_body,
            )
            position_block = np.zeros((3, unknowns))
            position_block[:, velocity_offset + 3 * i : velocity_offset + 3 * i + 3] = -dt * np.eye(3)
            position_block[:, gravity_offset : gravity_offset + 3] = -0.5 * dt * dt * np.eye(3)
            position_block[:, scale_offset] = node_positions[j] - node_positions[i]
            position_block[:, bias_offset : bias_offset + 3] = (
                -position_bias_jacobian
            )
            lever_delta = (
                node_rotations[j].as_matrix() - node_rotations[i].as_matrix()
            ) @ camera_position_in_body
            rows.append(position_block)
            targets.append(delta_p_imu + lever_delta)

            velocity_block = np.zeros((3, unknowns))
            velocity_block[:, velocity_offset + 3 * i : velocity_offset + 3 * i + 3] = -np.eye(3)
            velocity_block[:, velocity_offset + 3 * j : velocity_offset + 3 * j + 3] = np.eye(3)
            velocity_block[:, gravity_offset : gravity_offset + 3] = -dt * np.eye(3)
            velocity_block[:, bias_offset : bias_offset + 3] = (
                -velocity_bias_jacobian
            )
            rows.append(velocity_block)
            targets.append(delta_v_imu)
            interval_count += 1
    bias_prior = np.zeros((3, unknowns))
    bias_prior[:, bias_offset : bias_offset + 3] = np.eye(3) / 0.5
    rows.append(bias_prior)
    targets.append(np.zeros(3))
    design = np.vstack(rows)
    target = np.concatenate(targets)
    solution, _, rank, singular_values = np.linalg.lstsq(design, target, rcond=None)
    residual = design @ solution - target
    gravity = solution[gravity_offset : gravity_offset + 3]
    scale = float(solution[scale_offset])
    accel_bias = solution[bias_offset : bias_offset + 3]
    return {
        "scale": scale,
        "gravity": gravity.tolist(),
        "gravity_norm": float(np.linalg.norm(gravity)),
        "accelerometer_bias_mps2": accel_bias.tolist(),
        "accelerometer_bias_norm_mps2": float(np.linalg.norm(accel_bias)),
        "residual_rmse_mps": float(np.sqrt(np.mean(residual**2))),
        "rank": int(rank),
        "unknowns": int(design.shape[1]),
        "condition_number": float(singular_values[0] / singular_values[-1]),
        "nodes": int(len(indices)),
        "intervals": interval_count,
    }


def write_scaled_trajectory(
    path: Path,
    times: np.ndarray,
    positions: np.ndarray,
    quaternions_xyzw: np.ndarray,
    scale: float,
) -> None:
    scaled = positions[0] + scale * (positions - positions[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for time_s, position, quaternion in zip(times, scaled, quaternions_xyzw):
            writer.writerow(
                [
                    f"{time_s:.9f}",
                    *(f"{value:.9f}" for value in position),
                    f"{quaternion[3]:.9f}",
                    *(f"{value:.9f}" for value in quaternion[:3]),
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--stream", choices=("color", "infrared_left"), default="color")
    parser.add_argument("--body-t-camera-yaml", type=Path, required=True)
    parser.add_argument("--imu-calibration", type=Path, required=True)
    parser.add_argument("--orientation-trajectory", type=Path)
    parser.add_argument("--stereo-scale-report", type=Path)
    parser.add_argument("--td-s", type=float, required=True)
    parser.add_argument("--node-stride", type=int, default=10)
    parser.add_argument("--max-hop", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.node_stride < 2:
        parser.error("--node-stride must be at least 2")
    if args.max_hop < 1:
        parser.error("--max-hop must be at least 1")
    if args.stereo_scale_report is not None and args.orientation_trajectory is None:
        parser.error("--stereo-scale-report requires --orientation-trajectory")

    times, positions, quaternions = load_trajectory(args.trajectory)
    mono_times = camera_epoch_to_monotonic(
        args.session / "d405_frames.csv", args.stream, times
    )
    imu_times, accel = load_calibrated_acceleration(
        args.session / "external_imu" / "imu.bin", args.imu_calibration
    )
    body_t_camera = load_body_t_camera(args.body_t_camera_yaml)
    reference_times = reference_quaternions = None
    if args.orientation_trajectory is not None:
        reference_times, _, reference_quaternions = load_trajectory(
            args.orientation_trajectory
        )
    overlap, body_rotations, attitude = select_scale_attitude(
        times,
        quaternions,
        body_t_camera,
        reference_times,
        reference_quaternions,
    )
    result = solve_visual_inertial_scale(
        mono_times[overlap],
        positions[overlap],
        body_rotations,
        imu_times,
        accel,
        body_t_camera[:3, 3],
        args.td_s,
        args.node_stride,
        args.max_hop,
    )
    if args.stereo_scale_report is not None:
        _, legacy_rotations, _ = select_scale_attitude(
            times, quaternions, body_t_camera
        )
        legacy_result = solve_visual_inertial_scale(
            mono_times[overlap],
            positions[overlap],
            legacy_rotations[overlap],
            imu_times,
            accel,
            body_t_camera[:3, 3],
            args.td_s,
            args.node_stride,
            args.max_hop,
        )
        stereo = json.loads(args.stereo_scale_report.read_text(encoding="utf-8"))
        source, selection = choose_attitude_scale(legacy_result, result, stereo)
        result = legacy_result if source == "mast3r" else result
        attitude.update({"source": source, "selection": selection})
    failures = []
    if not 0.05 <= result["scale"] <= 20.0:
        failures.append("scale_out_of_range")
    if not 7.0 <= result["gravity_norm"] <= 12.0:
        failures.append("gravity_norm_out_of_range")
    if result["rank"] < result["unknowns"]:
        failures.append("rank_deficient")
    result.update(
        {
            "schema": "umi_mast3r_imu_scale_v1",
            "result": "PASS" if not failures else "FAIL",
            "failures": failures,
            "slam_supervision": False,
            "external_ground_truth_used": False,
            "inputs": "MASt3R camera poses + onboard calibrated 400Hz IMU"
            + (
                " + onboard orientation trajectory"
                if args.orientation_trajectory
                else " only"
            ),
            "attitude": attitude,
            "orientation_trajectory": (
                str(args.orientation_trajectory.resolve())
                if args.orientation_trajectory is not None else None
            ),
            "stereo_scale_report": (
                str(args.stereo_scale_report.resolve())
                if args.stereo_scale_report is not None
                else None
            ),
            "td_s": args.td_s,
            "node_stride": args.node_stride,
            "max_hop": args.max_hop,
            "trajectory": str(args.trajectory.resolve()),
            "imu_calibration": str(args.imu_calibration.resolve()),
            "output": str(args.output.resolve()),
        }
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if failures:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3
    write_scaled_trajectory(args.output, times, positions, quaternions, result["scale"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
