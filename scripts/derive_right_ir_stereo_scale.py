#!/usr/bin/env python3
"""Re-express recorded left-IR stereo motion in an independent right-IR MASt3R track."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from align_mast3r_scale_with_stereo import (
    change_relative_pose_frame,
    load_stereo_calibration,
    robust_scale,
    trajectory_step_continuity,
)
from fuse_mast3r_dual_stream import load_trajectory


def nearest_pose_index(times: np.ndarray, timestamp: float, max_delta_s: float) -> int | None:
    index = int(np.searchsorted(times, timestamp))
    candidates = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(times)]
    nearest = min(candidates, key=lambda candidate: abs(times[candidate] - timestamp))
    return nearest if abs(times[nearest] - timestamp) <= max_delta_s else None


def convert_observation(
    observation: dict,
    times: np.ndarray,
    positions: np.ndarray,
    camera_rotations: Rotation,
    right_from_left_rotation: Rotation,
    right_from_left_translation: np.ndarray,
    max_timestamp_error_s: float = 0.010,
) -> dict:
    converted = dict(observation)
    if not observation.get("accepted"):
        return converted
    if observation.get("metric_displacement_frame") != "infrared_left_camera_i":
        raise ValueError("source stereo observation is not in left-IR camera coordinates")
    first = nearest_pose_index(times, float(observation["first_t_sec"]), max_timestamp_error_s)
    second = nearest_pose_index(times, float(observation["second_t_sec"]), max_timestamp_error_s)
    if first is None or second is None or second <= first:
        converted.update(accepted=False, reason="missing_right_pose")
        return converted
    left_rotation = Rotation.from_quat(observation["pnp_rotation_quaternion_xyzw"])
    left_displacement = np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
    left_translation = -left_rotation.apply(left_displacement)
    right_rotation, right_translation = change_relative_pose_frame(
        left_rotation,
        left_translation,
        right_from_left_rotation,
        right_from_left_translation,
    )
    right_displacement = -right_rotation.inv().apply(right_translation)
    visual_displacement = camera_rotations[first].inv().apply(positions[second] - positions[first])
    metric_distance = float(np.linalg.norm(right_displacement))
    visual_distance = float(np.linalg.norm(visual_displacement))
    if metric_distance < 0.003 or visual_distance < 1e-4:
        converted.update(accepted=False, reason="translation_excitation_low")
        return converted
    direction_cosine = float(np.dot(right_displacement, visual_displacement) / (metric_distance * visual_distance))
    expected_rotation = camera_rotations[second].inv() * camera_rotations[first]
    rotation_error_deg = float(np.degrees((expected_rotation.inv() * right_rotation).magnitude()))
    if direction_cosine < 0.5 or rotation_error_deg > 5.0:
        converted.update(
            accepted=False,
            reason="translation_direction_disagrees" if direction_cosine < 0.5 else "rotation_disagrees",
            direction_cosine=direction_cosine,
            rotation_error_deg=rotation_error_deg,
        )
        return converted
    for stale in ("forward_scale", "reverse_scale", "bidirectional_relative_disagreement"):
        converted.pop(stale, None)
    converted.update(
        scale=float(np.dot(right_displacement, visual_displacement) / visual_distance**2),
        metric_distance_m=metric_distance,
        metric_displacement_camera_i_m=right_displacement.tolist(),
        metric_displacement_frame="infrared_right_camera_i",
        mast3r_distance=visual_distance,
        direction_cosine=direction_cosine,
        rotation_error_deg=rotation_error_deg,
        pnp_rotation_quaternion_xyzw=right_rotation.as_quat().tolist(),
        first_index=first,
        second_index=second,
        first_t_sec=float(times[first]),
        second_t_sec=float(times[second]),
        scale_estimator="right_track_vs_left_stereo_geometry",
    )
    return converted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left-stereo-report", type=Path, required=True)
    parser.add_argument("--right-trajectory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    left = json.loads(args.left_stereo_report.read_text(encoding="utf-8"))
    if left.get("result") != "PASS" or left.get("observation_frame") != "infrared_left_camera_i":
        raise ValueError("requires a passing left-IR stereo report")
    calibration = load_stereo_calibration(Path(left["db3"]))
    baseline_report = float(left["factory_stereo_calibration"]["baseline_m"])
    if abs(calibration["baseline_m"] - baseline_report) > 1e-9:
        raise ValueError("factory baseline changed since source report")
    times, positions, quaternions = load_trajectory(args.right_trajectory)
    rotations = Rotation.from_quat(quaternions)
    observations = [
        convert_observation(
            observation,
            times,
            positions,
            rotations,
            Rotation.from_matrix(calibration["right_rotation_from_left"]),
            calibration["right_translation_from_left_m"],
        )
        for observation in left["observations"]
    ]
    failures = []
    try:
        scale, quality = robust_scale(observations, min_observations=4)
    except ValueError as error:
        scale, quality = None, {"error": str(error)}
        failures.append("right_stereo_scale_unobservable")
    report = dict(left)
    report.update(
        result="FAIL" if failures else "PASS",
        failures=failures,
        inputs="right-IR MASt3R poses + recorded D405 stereo geometry + factory extrinsic only",
        trajectory=str(args.right_trajectory.resolve()),
        observation_frame="infrared_right_camera_i",
        observations=observations,
        scale_m_per_mast3r_unit=scale,
        quality=quality,
        trajectory_continuity=(trajectory_step_continuity(positions, scale, times) if scale else None),
        derived_from_left_stereo_report=str(args.left_stereo_report.resolve()),
        factory_stereo_calibration={
            **left["factory_stereo_calibration"],
            "right_rotation_from_left": calibration["right_rotation_from_left"].tolist(),
        },
        output=str(args.output.resolve()),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "scale": scale, "quality": quality}))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
