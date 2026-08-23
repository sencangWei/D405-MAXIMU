#!/usr/bin/env python3
"""Project VINS-Fusion's final 4DoF pose-graph corrections onto the full VIO stream.

The algorithm uses only raw VIO poses and the optimizer's republished
``/pose_graph_path``.  It never receives an expected endpoint, path dimensions,
or a planar-motion label.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_path_collisions(inputs: list[Path], outputs: list[Path]) -> None:
    resolved_inputs = {path.resolve() for path in inputs}
    resolved_outputs = [path.resolve() for path in outputs]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ValueError("output and report paths must be distinct")
    collisions = resolved_inputs.intersection(resolved_outputs)
    if collisions:
        raise ValueError(f"output path aliases an input: {sorted(map(str, collisions))}")


def read_poses(path: Path) -> tuple[np.ndarray, np.ndarray, Rotation]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if len(rows) < 2:
        raise ValueError(f"trajectory has insufficient poses: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows],
        dtype=float,
    )
    # SciPy uses x,y,z,w while the project CSV contract uses w,x,y,z.
    quaternions_xyzw = np.asarray(
        [
            [float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"])]
            for row in rows
        ],
        dtype=float,
    )
    if (
        not np.isfinite(times).all()
        or not np.isfinite(positions).all()
        or not np.isfinite(quaternions_xyzw).all()
    ):
        raise ValueError("trajectory contains non-finite values")
    if np.any(np.linalg.norm(quaternions_xyzw, axis=1) <= 1e-12):
        raise ValueError("trajectory contains a zero quaternion")
    if np.any(np.diff(times) <= 0.0):
        raise ValueError("trajectory timestamps must be strictly increasing")
    return times, positions, Rotation.from_quat(quaternions_xyzw)


def interpolate_positions(
    source_times: np.ndarray,
    source_positions: np.ndarray,
    target_times: np.ndarray,
) -> np.ndarray:
    return np.column_stack(
        [np.interp(target_times, source_times, source_positions[:, axis]) for axis in range(3)]
    )


def yaw_angles(rotations: Rotation) -> np.ndarray:
    """Return world-Z yaw without interpolating pitch or roll."""
    matrices = rotations.as_matrix()
    return np.arctan2(matrices[:, 1, 0], matrices[:, 0, 0])


def wrapped_angle_difference(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.arctan2(np.sin(left - right), np.cos(left - right))


def merge_pose_graph_times(
    raw_times: np.ndarray,
    graph_times: np.ndarray,
    *,
    tolerance_s: float = 1e-9,
) -> tuple[np.ndarray, int]:
    """Add missing graph knots to the emitted timeline without near-duplicates."""
    merged = list(raw_times)
    inserted = 0
    for graph_time in graph_times:
        index = int(np.searchsorted(raw_times, graph_time))
        neighbours = []
        if index > 0:
            neighbours.append(raw_times[index - 1])
        if index < len(raw_times):
            neighbours.append(raw_times[index])
        if neighbours and min(abs(graph_time - value) for value in neighbours) <= tolerance_s:
            continue
        merged.append(float(graph_time))
        inserted += 1
    return np.asarray(sorted(merged), dtype=float), inserted


def project_pose_graph(
    raw_times: np.ndarray,
    raw_positions: np.ndarray,
    raw_rotations: Rotation,
    graph_times: np.ndarray,
    graph_positions: np.ndarray,
    graph_rotations: Rotation,
) -> tuple[np.ndarray, np.ndarray, Rotation, dict[str, object]]:
    inside = (graph_times >= raw_times[0]) & (graph_times <= raw_times[-1])
    graph_times = graph_times[inside]
    graph_positions = graph_positions[inside]
    graph_rotations = graph_rotations[inside]
    if len(graph_times) < 2:
        raise ValueError("pose graph does not overlap the raw VIO stream")

    unique = np.r_[True, np.diff(graph_times) > 1e-9]
    graph_times = graph_times[unique]
    graph_positions = graph_positions[unique]
    graph_rotations = graph_rotations[unique]

    raw_position_at_graph = interpolate_positions(raw_times, raw_positions, graph_times)
    raw_rotation_at_graph = Slerp(raw_times, raw_rotations)(graph_times)
    full_correction_rotations = graph_rotations * raw_rotation_at_graph.inv()
    correction_yaws = yaw_angles(full_correction_rotations)
    correction_rotations = Rotation.from_euler("z", correction_yaws)
    removed_tilt_rotations = correction_rotations.inv() * full_correction_rotations
    removed_tilt_deg = np.rad2deg(removed_tilt_rotations.magnitude())
    correction_translations = graph_positions - correction_rotations.apply(
        raw_position_at_graph
    )

    knot_times = graph_times.copy()
    knot_rotations = correction_rotations
    knot_translations = correction_translations.copy()
    if knot_times[0] > raw_times[0]:
        # Before the loop database starts there is no graph evidence.  Preserve
        # raw VIO and blend from identity to the first measured correction.
        knot_times = np.r_[raw_times[0], knot_times]
        knot_rotations = Rotation.concatenate([Rotation.identity(), knot_rotations])
        knot_translations = np.vstack((np.zeros(3), knot_translations))
    if knot_times[-1] < raw_times[-1]:
        # The stationary tail may not create more keyframes.  Hold the last
        # optimizer correction instead of inventing an endpoint constraint.
        knot_times = np.r_[knot_times, raw_times[-1]]
        knot_rotations = Rotation.concatenate(
            [knot_rotations, knot_rotations[-1:]]
        )
        knot_translations = np.vstack((knot_translations, knot_translations[-1]))

    # A causal raw-VIO guard may legitimately suppress an isolated bad pose.
    # Keep every optimizer knot in the final artifact even when its original
    # full-rate sample was suppressed; the inserted pose is reconstructed from
    # the graph itself, not from the rejected raw sample.
    emitted_times, inserted_graph_samples = merge_pose_graph_times(
        raw_times, graph_times
    )
    emitted_raw_positions = interpolate_positions(
        raw_times, raw_positions, emitted_times
    )
    emitted_raw_rotations = Slerp(raw_times, raw_rotations)(emitted_times)

    per_pose_rotation = Slerp(knot_times, knot_rotations)(emitted_times)
    per_pose_translation = interpolate_positions(
        knot_times, knot_translations, emitted_times
    )
    corrected_positions = (
        per_pose_rotation.apply(emitted_raw_positions) + per_pose_translation
    )
    corrected_rotations = per_pose_rotation * emitted_raw_rotations

    # Verify the actual emitted, full-rate trajectory at graph timestamps.  Do
    # not reuse the knot construction identity as an integrity check.
    reconstructed_graph_positions = interpolate_positions(
        emitted_times, corrected_positions, graph_times
    )
    reconstructed_graph_rotations = Slerp(
        emitted_times, corrected_rotations
    )(graph_times)
    graph_residual_m = np.linalg.norm(
        reconstructed_graph_positions - graph_positions, axis=1
    )
    graph_full_rotation_residual_deg = np.rad2deg(
        (reconstructed_graph_rotations * graph_rotations.inv()).magnitude()
    )
    reconstructed_corrections = (
        reconstructed_graph_rotations * raw_rotation_at_graph.inv()
    )
    graph_yaw_residual_deg = np.rad2deg(
        np.abs(
            wrapped_angle_difference(
                yaw_angles(reconstructed_corrections), correction_yaws
            )
        )
    )
    raw_steps = np.linalg.norm(np.diff(raw_positions, axis=0), axis=1)
    corrected_steps = np.linalg.norm(np.diff(corrected_positions, axis=0), axis=1)
    graph_residual_max_m = float(np.max(graph_residual_m))
    graph_yaw_residual_max_deg = float(np.max(graph_yaw_residual_deg))
    raw_max_step_m = float(np.max(raw_steps))
    corrected_max_step_m = float(np.max(corrected_steps))
    max_allowed_step_m = max(0.03, 3.5 * raw_max_step_m)
    integrity_failures = []
    if not np.isfinite(corrected_positions).all() or not np.isfinite(
        corrected_rotations.as_quat()
    ).all():
        integrity_failures.append("corrected trajectory contains non-finite values")
    if graph_residual_max_m > 0.001:
        integrity_failures.append(
            f"emitted trajectory misses pose-graph knots by {graph_residual_max_m:.6f}m"
        )
    if graph_yaw_residual_max_deg > 0.1:
        integrity_failures.append(
            "emitted trajectory misses pose-graph correction yaw by "
            f"{graph_yaw_residual_max_deg:.6f}deg"
        )
    if corrected_max_step_m > max_allowed_step_m:
        integrity_failures.append(
            f"corrected step {corrected_max_step_m:.6f}m exceeds {max_allowed_step_m:.6f}m"
        )
    report = {
        "result": "PASS" if not integrity_failures else "FAIL",
        "method": "final_4dof_yaw_pose_graph_correction_interpolation",
        "rotation_projection": "yaw_only",
        "acceptance_scope": "postprocess_integrity_only",
        "uses_endpoint_constraint": False,
        "uses_expected_dimensions": False,
        "raw_samples": int(len(raw_times)),
        "emitted_samples": int(len(emitted_times)),
        "inserted_pose_graph_samples": int(inserted_graph_samples),
        "pose_graph_keyframes": int(len(graph_times)),
        "pose_graph_time_range_s": [float(graph_times[0]), float(graph_times[-1])],
        "raw_time_range_s": [float(raw_times[0]), float(raw_times[-1])],
        "keyframe_reconstruction_rms_mm": float(
            np.sqrt(np.mean(graph_residual_m**2)) * 1000.0
        ),
        "keyframe_reconstruction_max_mm": float(np.max(graph_residual_m) * 1000.0),
        "keyframe_yaw_reconstruction_rms_deg": float(
            np.sqrt(np.mean(graph_yaw_residual_deg**2))
        ),
        "keyframe_yaw_reconstruction_max_deg": graph_yaw_residual_max_deg,
        "keyframe_full_rotation_difference_rms_deg": float(
            np.sqrt(np.mean(graph_full_rotation_residual_deg**2))
        ),
        "keyframe_full_rotation_difference_max_deg": float(
            np.max(graph_full_rotation_residual_deg)
        ),
        "maximum_removed_tilt_deg": float(np.max(removed_tilt_deg)),
        "raw_max_step_mm": raw_max_step_m * 1000.0,
        "corrected_max_step_mm": corrected_max_step_m * 1000.0,
        "maximum_allowed_corrected_step_mm": max_allowed_step_m * 1000.0,
        "raw_endpoint_mm": float(
            np.linalg.norm(raw_positions[-1] - raw_positions[0]) * 1000.0
        ),
        "corrected_endpoint_mm": float(
            np.linalg.norm(corrected_positions[-1] - corrected_positions[0]) * 1000.0
        ),
        "failures": integrity_failures,
    }
    return emitted_times, corrected_positions, corrected_rotations, report


def write_poses(
    path: Path,
    times: np.ndarray,
    positions: np.ndarray,
    rotations: Rotation,
) -> None:
    quaternions = rotations.as_quat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for timestamp, position, quaternion in zip(times, positions, quaternions):
            writer.writerow(
                [timestamp, *position, quaternion[3], quaternion[0], quaternion[1], quaternion[2]]
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--pose-graph", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    reject_path_collisions([args.raw, args.pose_graph], [args.output, args.report])
    source_hashes = {
        "raw_sha256": sha256_file(args.raw),
        "pose_graph_sha256": sha256_file(args.pose_graph),
    }

    raw_times, raw_positions, raw_rotations = read_poses(args.raw)
    graph_times, graph_positions, graph_rotations = read_poses(args.pose_graph)
    emitted_times, corrected_positions, corrected_rotations, report = project_pose_graph(
        raw_times,
        raw_positions,
        raw_rotations,
        graph_times,
        graph_positions,
        graph_rotations,
    )
    write_poses(args.output, emitted_times, corrected_positions, corrected_rotations)
    report["inputs"] = {
        "raw": str(args.raw.resolve()),
        "raw_sha256": source_hashes["raw_sha256"],
        "pose_graph": str(args.pose_graph.resolve()),
        "pose_graph_sha256": source_hashes["pose_graph_sha256"],
    }
    report["output"] = str(args.output.resolve())
    report["output_sha256"] = sha256_file(args.output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
