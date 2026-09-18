#!/usr/bin/env python3
"""Jointly estimate Tracker-to-Docker2-body from multiple AprilGrid captures.

Each capture contributes only independent AprilGrid camera poses and Lighthouse
Tracker poses.  The SLAM trajectory is not an input.  A separate capture can be
held out to validate the fitted rigid transform.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from calibrate_lighthouse_aprilgrid import (
    load_body_T_cam0,
    load_camera_poses,
    map_camera_times,
)
from calibrate_lighthouse_umi import (
    fit_transform,
    interpolate_tracker,
    load_tracker,
    pose_matrices,
    relative_pairs,
    residual_vector,
    summary,
)


def transform_difference(first: np.ndarray, second: np.ndarray) -> dict[str, float]:
    return {
        "translation_mm": float(
            np.linalg.norm(first[:3, 3] - second[:3, 3]) * 1000.0
        ),
        "rotation_deg": float(
            np.degrees(
                Rotation.from_matrix(
                    first[:3, :3] @ second[:3, :3].T
                ).magnitude()
            )
        ),
    }


def profile_transform(
    transform: np.ndarray, pairs: list[tuple[np.ndarray, np.ndarray]]
) -> dict[str, object]:
    parameters = np.r_[
        Rotation.from_matrix(transform[:3, :3]).as_rotvec(), transform[:3, 3]
    ]
    raw = residual_vector(parameters, pairs).reshape((-1, 6))
    rotation_error = np.linalg.norm(raw[:, :3], axis=1) / 0.10
    translation_error = np.linalg.norm(raw[:, 3:], axis=1)
    combined = np.sqrt(translation_error**2 + (0.10 * rotation_error) ** 2)
    return {
        "translation_residual_mm": summary(translation_error, 1000.0),
        "rotation_residual_deg": summary(np.degrees(rotation_error)),
        "combined_residual_mm": summary(combined, 1000.0),
    }


def fit_joint_pair_sets(
    pair_sets: list[list[tuple[np.ndarray, np.ndarray]]],
) -> tuple[np.ndarray, dict[str, object]]:
    return fit_transform([pair for pairs in pair_sets for pair in pairs])


def load_capture(
    name: str,
    camera_pose_path: Path,
    tracker_path: Path,
    d405_frames_path: Path,
    body_T_camera: np.ndarray,
    tracker_query_offset_ms: float,
    discard_leading_s: float,
    max_gap_s: float,
) -> dict[str, object]:
    camera_times, positions, quaternions = load_camera_poses(camera_pose_path)
    camera_times, clock_mapping = map_camera_times(
        camera_times, "host_monotonic", d405_frames_path
    )
    tracker_times, tracker_positions, tracker_quaternions, serial = load_tracker(
        tracker_path, "host_monotonic"
    )
    body_poses = (
        pose_matrices(positions, quaternions)
        @ np.linalg.inv(body_T_camera)[None, :, :]
    )
    keep = camera_times >= camera_times[0] + discard_leading_s
    camera_times = camera_times[keep]
    body_poses = body_poses[keep]
    tracker_poses, valid = interpolate_tracker(
        camera_times + tracker_query_offset_ms / 1000.0,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    body_poses = body_poses[valid]
    selected_times = camera_times[valid]
    if len(selected_times) < 100:
        raise ValueError(f"{name}: fewer than 100 timestamp-overlapped poses")
    sample_rate_hz = (len(selected_times) - 1) / (
        selected_times[-1] - selected_times[0]
    )
    pairs, excitation = relative_pairs(tracker_poses, body_poses, sample_rate_hz)
    transform, independent_profile = fit_transform(pairs)
    return {
        "name": name,
        "serial": serial,
        "pairs": pairs,
        "tracker_T_body": transform,
        "independent_profile": independent_profile,
        "excitation": excitation,
        "overlap_samples": int(len(selected_times)),
        "sample_rate_hz": float(sample_rate_hz),
        "clock_mapping": clock_mapping,
        "inputs": {
            "aprilgrid_camera_poses": str(camera_pose_path.resolve()),
            "tracker": str(tracker_path.resolve()),
            "d405_frames": str(d405_frames_path.resolve()),
        },
    }


def public_capture(capture: dict[str, object]) -> dict[str, object]:
    return {
        key: value.tolist() if isinstance(value, np.ndarray) else value
        for key, value in capture.items()
        if key != "pairs"
    }


def calibrate_joint(
    training_specs: list[tuple[str, Path, Path, Path]],
    held_out_specs: list[tuple[str, Path, Path, Path]],
    body_camera_config: Path,
    time_offset_report: Path,
    discard_leading_s: float,
    max_gap_s: float,
) -> dict[str, object]:
    time_report = json.loads(time_offset_report.read_text(encoding="utf-8"))
    if time_report.get("result") not in {"PASS", "PASS_CANDIDATE"}:
        raise ValueError("independent IMU-to-Tracker time-offset report did not pass")
    tracker_query_offset_ms = float(time_report["tracker_query_offset_ms"])
    body_T_camera = load_body_T_cam0(body_camera_config)
    training = [
        load_capture(
            *spec,
            body_T_camera,
            tracker_query_offset_ms,
            discard_leading_s,
            max_gap_s,
        )
        for spec in training_specs
    ]
    held_out = [
        load_capture(
            *spec,
            body_T_camera,
            tracker_query_offset_ms,
            discard_leading_s,
            max_gap_s,
        )
        for spec in held_out_specs
    ]
    serials = {capture["serial"] for capture in training + held_out}
    if len(serials) != 1:
        raise ValueError(f"captures use different Tracker serials: {sorted(serials)}")

    joint_transform, joint_profile = fit_joint_pair_sets(
        [capture["pairs"] for capture in training]
    )
    for capture in training + held_out:
        capture["joint_profile"] = profile_transform(
            joint_transform, capture["pairs"]
        )
        capture["joint_external_difference"] = transform_difference(
            joint_transform, capture["tracker_T_body"]
        )

    training_differences = []
    for first_index, first in enumerate(training):
        for second in training[first_index + 1 :]:
            training_differences.append(
                {
                    "first": first["name"],
                    "second": second["name"],
                    **transform_difference(
                        first["tracker_T_body"], second["tracker_T_body"]
                    ),
                }
            )
    held_out_max_translation = max(
        capture["joint_external_difference"]["translation_mm"]
        for capture in held_out
    )
    held_out_max_rotation = max(
        capture["joint_external_difference"]["rotation_deg"]
        for capture in held_out
    )
    held_out_max_residual = max(
        capture["joint_profile"]["combined_residual_mm"]["p95"]
        for capture in held_out
    )
    passed = (
        len(training) >= 2
        and len(held_out) >= 1
        and all(
            capture["excitation"]["observability"]["result"] == "PASS"
            for capture in training + held_out
        )
        and held_out_max_translation <= 2.0
        and held_out_max_rotation <= 0.3
        and held_out_max_residual <= 10.0
    )
    return {
        "schema": "lighthouse_d405_aprilgrid_joint_handeye_v1",
        "result": "PASS_CANDIDATE" if passed else "DIAGNOSTIC_CANDIDATE",
        "auto_apply": False,
        "method": "multi-capture robust relative-motion AX=XB directly to Docker2 VINS body",
        "independent_ground_truth": True,
        "slam_supervision": False,
        "slam_inputs": [],
        "tracker_serial": next(iter(serials)),
        "tracker_time_source": "host_monotonic",
        "tracker_query_offset_ms": tracker_query_offset_ms,
        "time_offset_policy": "fixed common offset from independent IMU-to-Tracker multi-capture estimate",
        "offset_semantics": "interpolate Tracker at AprilGrid timestamp + tracker_query_offset_ms",
        "tracker_T_body": joint_transform.tolist(),
        "body_T_cam0": body_T_camera.tolist(),
        "translation_norm_mm": float(
            np.linalg.norm(joint_transform[:3, 3]) * 1000.0
        ),
        "rotation_angle_deg": float(
            np.degrees(Rotation.from_matrix(joint_transform[:3, :3]).magnitude())
        ),
        "joint_profile": joint_profile,
        "training_pairwise_independent_differences": training_differences,
        "training_captures": [public_capture(capture) for capture in training],
        "held_out_captures": [public_capture(capture) for capture in held_out],
        "acceptance": {
            "maximum_held_out_translation_mm": held_out_max_translation,
            "maximum_held_out_rotation_deg": held_out_max_rotation,
            "maximum_held_out_combined_residual_p95_mm": held_out_max_residual,
            "thresholds": {
                "maximum_held_out_translation_mm": 2.0,
                "maximum_held_out_rotation_deg": 0.3,
                "maximum_held_out_combined_residual_p95_mm": 10.0,
            },
        },
        "inputs": {
            "body_camera_config": str(body_camera_config.resolve()),
            "time_offset_report": str(time_offset_report.resolve()),
            "discard_leading_s": discard_leading_s,
            "max_gap_s": max_gap_s,
        },
        "separation_rule": "R2 and R5 fit the external; R4 remains independent held-out validation; SLAM is evaluation-only",
    }


def capture_spec(values: list[str]) -> tuple[str, Path, Path, Path]:
    return values[0], Path(values[1]), Path(values[2]), Path(values[3])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--train",
        nargs=4,
        action="append",
        metavar=("NAME", "CAMERA_POSES", "TRACKER", "D405_FRAMES"),
        required=True,
    )
    parser.add_argument(
        "--held-out",
        nargs=4,
        action="append",
        metavar=("NAME", "CAMERA_POSES", "TRACKER", "D405_FRAMES"),
        required=True,
    )
    parser.add_argument("--body-camera-config", type=Path, required=True)
    parser.add_argument("--time-offset-report", type=Path, required=True)
    parser.add_argument("--discard-leading-s", type=float, default=3.0)
    parser.add_argument("--max-gap-ms", type=float, default=30.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = calibrate_joint(
        [capture_spec(spec) for spec in args.train],
        [capture_spec(spec) for spec in args.held_out],
        args.body_camera_config,
        args.time_offset_report,
        args.discard_leading_s,
        args.max_gap_ms / 1000.0,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["result"] == "PASS_CANDIDATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
