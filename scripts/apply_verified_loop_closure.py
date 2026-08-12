#!/usr/bin/env python3
"""Apply a translation loop constraint only after stereo image verification.

This is an offline loop-translation correction for VINS position CSV files.  It
requires the operator to confirm that the recording ends at its start position,
then requires both IR cameras to independently confirm the same rigid scene.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


META_TS_RE = re.compile(r"timestamp=([0-9.]+)")


@dataclass
class Frame:
    epoch_s: float
    relative_s: float
    image: np.ndarray


@dataclass
class MatchEvidence:
    good_matches: int
    inliers: int
    inlier_ratio: float
    median_error_px: float
    median_displacement_px: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="视觉验真后为 VINS CSV 添加闭环平移约束"
    )
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--confirm-same-position",
        action="store_true",
        required=True,
        help="确认真实终点与起点是同一位置；没有该确认时拒绝施加闭环约束",
    )
    parser.add_argument("--start-window-s", type=float, default=6.0)
    parser.add_argument("--end-window-s", type=float, default=10.0)
    parser.add_argument("--sample-period-s", type=float, default=0.5)
    parser.add_argument("--min-inliers", type=int, default=200)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.50)
    parser.add_argument("--max-median-error-px", type=float, default=1.5)
    parser.add_argument("--max-median-displacement-px", type=float, default=60.0)
    return parser.parse_args()


def load_sampled_stereo_frames(
    session: Path, start_window_s: float, end_window_s: float, sample_period_s: float
) -> dict[str, list[Frame]]:
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Image as RosImage
    from std_msgs.msg import String

    db3_files = list(session.glob("*.db3"))
    if len(db3_files) != 1:
        raise RuntimeError(f"会话应包含一个 db3，实际为 {len(db3_files)}")

    acceptance_path = session / "acceptance.json"
    if not acceptance_path.exists():
        raise RuntimeError(f"缺少采集验收文件：{acceptance_path}")
    duration_s = float(
        json.loads(acceptance_path.read_text(encoding="utf-8"))["duration_s"]
    )

    sampled: dict[str, list[Frame]] = {
        "ir_left_start": [],
        "ir_left_end": [],
        "ir_right_start": [],
        "ir_right_end": [],
    }
    topic_names = {
        "ir_left": (
            "/device_0/sensor_0/Infrared_1/image/data",
            "/device_0/sensor_0/Infrared_1/image/metadata",
        ),
        "ir_right": (
            "/device_0/sensor_0/Infrared_2/image/data",
            "/device_0/sensor_0/Infrared_2/image/metadata",
        ),
    }
    database = sqlite3.connect(str(db3_files[0]))
    topic_ids = {
        name: topic_id
        for topic_id, name in database.execute("SELECT id, name FROM topics")
    }
    for stream, (data_name, metadata_name) in topic_names.items():
        data_id = topic_ids[data_name]
        metadata_id = topic_ids[metadata_name]
        for region, lower_s, upper_s in (
            ("start", 1.5, start_window_s),
            ("end", duration_s - end_window_s, duration_s - 0.4),
        ):
            lower_ns = int(lower_s * 1e9)
            upper_ns = int(upper_s * 1e9)
            timestamps = [
                timestamp
                for (timestamp,) in database.execute(
                    """
                    SELECT timestamp FROM messages
                    WHERE topic_id = ? AND timestamp BETWEEN ? AND ?
                    ORDER BY timestamp
                    """,
                    (data_id, lower_ns, upper_ns),
                )
            ]
            selected_timestamps = []
            last_sample_s = float("-inf")
            for bag_timestamp in timestamps:
                relative_s = bag_timestamp * 1e-9
                if relative_s - last_sample_s < sample_period_s:
                    continue
                selected_timestamps.append(bag_timestamp)
                last_sample_s = relative_s
            for bag_timestamp in selected_timestamps:
                relative_s = bag_timestamp * 1e-9
                image_row = database.execute(
                    "SELECT data FROM messages WHERE topic_id = ? AND timestamp = ?",
                    (data_id, bag_timestamp),
                ).fetchone()
                metadata_row = database.execute(
                    "SELECT data FROM messages WHERE topic_id = ? AND timestamp = ?",
                    (metadata_id, bag_timestamp),
                ).fetchone()
                if image_row is None or metadata_row is None:
                    continue
                image_blob = image_row[0]
                metadata_blob = metadata_row[0]
                metadata = deserialize_message(bytes(metadata_blob), String).data
                match = META_TS_RE.search(metadata)
                if match is None:
                    continue
                epoch_s = float(match.group(1)) / 1000.0
                message = deserialize_message(bytes(image_blob), RosImage)
                image = np.frombuffer(message.data, dtype=np.uint8).reshape(
                    message.height, message.step
                )[:, : message.width]
                sampled[f"{stream}_{region}"].append(
                    Frame(epoch_s, relative_s, image.copy())
                )
    database.close()
    empty = [key for key, frames in sampled.items() if not frames]
    if empty:
        raise RuntimeError("闭环采样窗口为空：" + ", ".join(empty))
    return sampled


def describe(image: np.ndarray) -> tuple[list[cv2.KeyPoint], np.ndarray | None]:
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(image)
    detector = cv2.ORB_create(
        nfeatures=2500, scaleFactor=1.2, nlevels=8, fastThreshold=7
    )
    return detector.detectAndCompute(enhanced, None)


def match_images(
    first: tuple[list[cv2.KeyPoint], np.ndarray | None],
    second: tuple[list[cv2.KeyPoint], np.ndarray | None],
) -> MatchEvidence:
    first_keypoints, first_descriptors = first
    second_keypoints, second_descriptors = second
    if first_descriptors is None or second_descriptors is None:
        return MatchEvidence(0, 0, 0.0, float("inf"), float("inf"))

    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
        first_descriptors, second_descriptors, k=2
    )
    good = [match for match, other in pairs if match.distance < 0.75 * other.distance]
    if len(good) < 8:
        return MatchEvidence(len(good), 0, 0.0, float("inf"), float("inf"))

    first_points = np.float32(
        [first_keypoints[match.queryIdx].pt for match in good]
    )
    second_points = np.float32(
        [second_keypoints[match.trainIdx].pt for match in good]
    )
    homography, mask = cv2.findHomography(
        first_points, second_points, cv2.RANSAC, 2.0
    )
    if homography is None or mask is None:
        return MatchEvidence(len(good), 0, 0.0, float("inf"), float("inf"))

    selected = mask.ravel().astype(bool)
    predicted = cv2.perspectiveTransform(
        first_points.reshape(-1, 1, 2), homography
    ).reshape(-1, 2)
    errors = np.linalg.norm(predicted - second_points, axis=1)
    displacements = np.linalg.norm(first_points - second_points, axis=1)
    inliers = int(selected.sum())
    return MatchEvidence(
        len(good),
        inliers,
        inliers / len(good),
        float(np.median(errors[selected])),
        float(np.median(displacements[selected])),
    )


def pair_stereo_indices(
    left_frames: list[Frame], right_frames: list[Frame], max_delta_s: float = 0.005
) -> list[tuple[int, int]]:
    pairs = []
    right_index = 0
    for left_index, left_frame in enumerate(left_frames):
        while (
            right_index + 1 < len(right_frames)
            and abs(
                right_frames[right_index + 1].relative_s - left_frame.relative_s
            )
            <= abs(right_frames[right_index].relative_s - left_frame.relative_s)
        ):
            right_index += 1
        if (
            right_index < len(right_frames)
            and abs(right_frames[right_index].relative_s - left_frame.relative_s)
            <= max_delta_s
        ):
            pairs.append((left_index, right_index))
    return pairs


def evidence_failures(
    evidence: dict[str, MatchEvidence], args: argparse.Namespace
) -> list[str]:
    failures = []
    for stream, value in evidence.items():
        if value.inliers < args.min_inliers:
            failures.append(f"{stream} 内点 {value.inliers} < {args.min_inliers}")
        if value.inlier_ratio < args.min_inlier_ratio:
            failures.append(
                f"{stream} 内点率 {value.inlier_ratio:.3f} < {args.min_inlier_ratio:.3f}"
            )
        if value.median_error_px > args.max_median_error_px:
            failures.append(
                f"{stream} 中位误差 {value.median_error_px:.3f}px > "
                f"{args.max_median_error_px:.3f}px"
            )
        if value.median_displacement_px > args.max_median_displacement_px:
            failures.append(
                f"{stream} 中位位移 {value.median_displacement_px:.3f}px > "
                f"{args.max_median_displacement_px:.3f}px"
            )
    return failures


def find_verified_loop(
    frames: dict[str, list[Frame]], args: argparse.Namespace
) -> tuple[Frame, Frame, dict[str, MatchEvidence]]:
    descriptions = {
        key: [describe(frame.image) for frame in values]
        for key, values in frames.items()
    }
    start_pairs = pair_stereo_indices(
        frames["ir_left_start"], frames["ir_right_start"]
    )
    end_pairs = pair_stereo_indices(frames["ir_left_end"], frames["ir_right_end"])
    if not start_pairs or not end_pairs:
        raise RuntimeError("左右 IR 在闭环窗口内没有 5ms 以内的同步帧")

    candidates = []
    for left_start_index, right_start_index in start_pairs:
        start_frame = frames["ir_left_start"][left_start_index]
        for left_end_index, right_end_index in end_pairs:
            end_frame = frames["ir_left_end"][left_end_index]
            evidence = {}
            evidence["ir_left"] = match_images(
                descriptions["ir_left_start"][left_start_index],
                descriptions["ir_left_end"][left_end_index],
            )
            evidence["ir_right"] = match_images(
                descriptions["ir_right_start"][right_start_index],
                descriptions["ir_right_end"][right_end_index],
            )
            score = min(value.inliers for value in evidence.values())
            candidates.append(
                (score, start_frame, end_frame, evidence, evidence_failures(evidence, args))
            )

    valid_candidates = [candidate for candidate in candidates if not candidate[4]]
    if not valid_candidates:
        _, _, _, _, failures = max(candidates, key=lambda item: item[0])
        raise RuntimeError("视觉闭环验真失败：" + "；".join(failures))
    _, start_frame, end_frame, evidence, _ = max(
        valid_candidates, key=lambda item: item[0]
    )
    return start_frame, end_frame, evidence


def load_trajectory(path: Path) -> tuple[list[dict[str, str]], np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open()))
    if len(rows) < 2:
        raise RuntimeError(f"轨迹点不足：{len(rows)}")
    times = np.array([float(row["t_sec"]) for row in rows])
    points = np.array(
        [[float(row["x"]), float(row["y"]), float(row["z"])] for row in rows]
    )
    return rows, times, points


def apply_path_weighted_loop_constraint(
    times: np.ndarray, points: np.ndarray, start_epoch_s: float, end_epoch_s: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    start_index = int(np.argmin(abs(times - start_epoch_s)))
    end_index = int(np.argmin(abs(times - end_epoch_s)))
    if end_index <= start_index:
        raise RuntimeError("闭环终点不晚于起点")

    path_coordinate = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    denominator = path_coordinate[end_index] - path_coordinate[start_index]
    if denominator <= 0.05:
        raise RuntimeError(f"闭环锚点间路径仅 {denominator:.3f}m，拒绝校正")

    alpha = np.clip(
        (path_coordinate - path_coordinate[start_index]) / denominator, 0.0, 1.0
    )
    loop_error = points[end_index] - points[start_index]
    corrected = points - alpha[:, None] * loop_error
    return corrected, np.array([start_index, end_index]), loop_error


def main() -> int:
    args = parse_args()
    session = args.session.resolve()
    frames = load_sampled_stereo_frames(
        session, args.start_window_s, args.end_window_s, args.sample_period_s
    )
    start_frame, end_frame, evidence = find_verified_loop(frames, args)
    rows, times, points = load_trajectory(args.trajectory)
    corrected, indices, loop_error = apply_path_weighted_loop_constraint(
        times, points, start_frame.epoch_s, end_frame.epoch_s
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=rows[0].keys())
        writer.writeheader()
        for row, point in zip(rows, corrected):
            updated = dict(row)
            updated.update(x=f"{point[0]:.12g}", y=f"{point[1]:.12g}", z=f"{point[2]:.12g}")
            writer.writerow(updated)

    start_index, end_index = indices
    raw_total_closure = float(np.linalg.norm(points[-1] - points[0]))
    corrected_total_closure = float(np.linalg.norm(corrected[-1] - corrected[0]))
    corrected_anchor_closure = float(
        np.linalg.norm(corrected[end_index] - corrected[start_index])
    )
    report = {
        "result": "PASS",
        "method": "stereo_verified_path_weighted_translation_constraint",
        "operator_confirmed_same_position": args.confirm_same_position,
        "session": str(session),
        "input_trajectory": str(args.trajectory.resolve()),
        "output_trajectory": str(args.output.resolve()),
        "loop_frames_relative_s": [start_frame.relative_s, end_frame.relative_s],
        "loop_trajectory_indices": [int(start_index), int(end_index)],
        "stereo_evidence": {
            stream: {
                "good_matches": value.good_matches,
                "inliers": value.inliers,
                "inlier_ratio": value.inlier_ratio,
                "median_error_px": value.median_error_px,
                "median_displacement_px": value.median_displacement_px,
            }
            for stream, value in evidence.items()
        },
        "raw_loop_error_m": loop_error.tolist(),
        "raw_total_closure_cm": raw_total_closure * 100.0,
        "corrected_anchor_closure_cm": corrected_anchor_closure * 100.0,
        "corrected_total_closure_cm": corrected_total_closure * 100.0,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
