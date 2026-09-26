#!/usr/bin/env python3
"""Compare board/Tracker rotation increments against raw UMI gyro; no SLAM input."""

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from calibrate_lighthouse_aprilgrid import load_camera_poses, map_camera_times
from calibrate_lighthouse_umi import interpolate_tracker, load_tracker, summary
from estimate_lighthouse_imu_time_offset import load_imu


def integrated_rotations(times, gyro):
    """Gyro is radians/second in its own body frame, not world-frame velocity."""
    rotations = [Rotation.identity()]
    for dt, first, second in zip(np.diff(times), gyro[:-1], gyro[1:]):
        rotations.append(rotations[-1] * Rotation.from_rotvec(dt * (first + second) / 2))
    return Slerp(times, Rotation.from_quat([item.as_quat() for item in rotations]))


def axis_mapping(reference, measured):
    """Fit one constant sensor-frame rotation, never a per-frame correction."""
    rotation, _ = Rotation.align_vectors(measured, reference)
    errors = np.degrees(np.linalg.norm(rotation.apply(reference) - measured, axis=1))
    return rotation, summary(errors)


def rotation_difference(first, second):
    return float(np.degrees((first.inv() * second).magnitude()))


def audit(run, imu_shift_s):
    session = Path((run / "d405_session.txt").read_text().strip())
    calibration = json.loads(
        (run / "calibration/lighthouse_d405_aprilgrid_calibration.json").read_text()
    )
    times, _, quaternions = load_camera_poses(run / "calibration/aprilgrid_camera_poses.csv")
    times, clock = map_camera_times(times, "host_monotonic", session / "d405_frames.csv")
    tracker_times, positions, tracker_q, _ = load_tracker(run / "tracker.csv", "host_monotonic")
    offset = calibration["tracker_query_offset_ms"] / 1000
    tracker_poses, valid = interpolate_tracker(
        times + offset, tracker_times, positions, tracker_q, .03
    )
    # Reproduce the *existing* calibration's split indices, not a new segmentation.
    selected_times = times[valid]
    board = Rotation.from_quat(quaternions[valid])
    tracker = Rotation.from_matrix(tracker_poses[:, :3, :3])
    window = calibration["split_half_stability"]["active_motion_window"]
    start, end = window["start_sample"], window["end_sample_exclusive"]
    selected_times = selected_times[start:end]
    board, tracker = board[start:end], tracker[start:end]
    midpoint = len(selected_times) // 2
    imu_times, gyro = load_imu(session / "external_imu/imu.bin")
    inertial = integrated_rotations(imu_times, gyro)
    profiles = []
    for horizon in (.2, .5, 1., 1.5):
        mappings = {"board": [], "tracker": [], "tracker_to_board": []}
        halves = []
        for name, lo, hi in [("first", 0, midpoint), ("second", midpoint, len(selected_times))]:
            first = np.arange(lo, hi)
            last = np.searchsorted(selected_times, selected_times[first] + horizon)
            keep = last < hi
            first, last = first[keep], last[keep]
            keep = (
                (selected_times[last] - selected_times[first] <= horizon + .04)
                & (selected_times[first] + imu_shift_s >= imu_times[0])
                & (selected_times[last] + imu_shift_s <= imu_times[-1])
            )
            first, last = first[keep], last[keep]
            delta_board = (board[first].inv() * board[last]).as_rotvec()
            delta_tracker = (tracker[first].inv() * tracker[last]).as_rotvec()
            delta_imu = (
                inertial(selected_times[first] + imu_shift_s).inv()
                * inertial(selected_times[last] + imu_shift_s)
            ).as_rotvec()
            active = np.linalg.norm(delta_imu, axis=1) >= np.radians(1.)
            delta_board, delta_tracker, delta_imu = [
                values[active] for values in (delta_board, delta_tracker, delta_imu)
            ]
            if len(delta_imu) < 10:
                raise ValueError(f"too few active increments: {name}/{horizon}")
            half = {"name": name, "increments": len(delta_imu)}
            for sensor, values in [("board", delta_board), ("tracker", delta_tracker)]:
                rotation, errors = axis_mapping(delta_imu, values)
                mappings[sensor].append(rotation)
                half[sensor + "_gyro_vector_residual_deg"] = errors
                half[sensor + "_gyro_angle_magnitude_difference_deg"] = summary(
                    np.degrees(abs(np.linalg.norm(values, axis=1) - np.linalg.norm(delta_imu, axis=1)))
                )
            mappings["tracker_to_board"].append(axis_mapping(delta_tracker, delta_board)[0])
            halves.append(half)
        profiles.append({
            "horizon_s": horizon,
            "half_axis_mapping_difference_deg": {
                key: rotation_difference(*values) for key, values in mappings.items()
            },
            "halves": halves,
        })
    return {
        "run": str(run.resolve()), "camera_clock_mapping": clock,
        "tracker_query_offset_ms": offset * 1000,
        "imu_query_shift_s": imu_shift_s,
        "split_times_relative_to_first_supported_board_s": [
            float(times[valid][start] - times[valid][0]),
            float(times[valid][start + midpoint] - times[valid][0]),
            float(times[valid][end - 1] - times[valid][0]),
        ],
        "profiles": profiles,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--imu-query-shift-s", type=float, default=0.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {
        "schema": "lighthouse_handeye_rotation_audit_v1",
        "diagnostic_only": True, "auto_apply": False, "slam_supervision": False,
        "method": "exact existing split; coordinate-invariant angles and constant SO(3) axis mappings",
        "limitations": "Raw gyro is not absolute truth; no bias fit; increments overlap; mapping residual is rotvec Euclidean difference, not absolute pose ATE.",
        "captures": [audit(run, args.imu_query_shift_s) for run in args.runs],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    for capture in result["captures"]:
        print(Path(capture["run"]).name, capture["split_times_relative_to_first_supported_board_s"])
        for profile in capture["profiles"]:
            print(profile["horizon_s"], profile["half_axis_mapping_difference_deg"])
    print(args.output)


if __name__ == "__main__":
    main()
