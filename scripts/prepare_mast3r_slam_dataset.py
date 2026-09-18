#!/usr/bin/env python3
"""Export one recorded D405 stream for MASt3R-SLAM without changing time.

The output images are lossless PNG files.  ``frames.csv`` preserves the
authoritative D405 global-time exposure timestamp for every exported frame.
MASt3R-SLAM itself remains unsupervised: no robot or Lighthouse trajectory is
read here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

import cv2
import numpy as np


TOPICS = {
    "color": (
        "/device_0/sensor_0/Color_0/image/data",
        "/device_0/sensor_0/Color_0/image/metadata",
    ),
    "infrared_left": (
        "/device_0/sensor_0/Infrared_1/image/data",
        "/device_0/sensor_0/Infrared_1/image/metadata",
    ),
    "infrared_right": (
        "/device_0/sensor_0/Infrared_2/image/data",
        "/device_0/sensor_0/Infrared_2/image/metadata",
    ),
}
CAMERA_INFO_TOPICS = {
    "color": "/device_0/sensor_0/Color_0/camera_info",
    "infrared_left": "/device_0/sensor_0/Infrared_1/camera_info",
    "infrared_right": "/device_0/sensor_0/Infrared_2/camera_info",
}
STEREO_BASELINE_TOPIC = "/device_0/sensor_0/option/Stereo_Baseline/value"
FRAME_NUMBER_RE = re.compile(r"(?:^|;)Frame number=(\d+)")
TIMESTAMP_RE = re.compile(r"(?:^|;)timestamp=([0-9.]+)")


def set_topic_filter(reader, topics: list[str]) -> None:
    """Restrict rosbag2 reads to the topics needed by the current pass."""
    import rosbag2_py

    reader.set_filter(rosbag2_py.StorageFilter(topics=topics))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_db3(session: Path) -> Path:
    bags = [path for path in session.glob("*.db3") if path.stat().st_size]
    if not bags:
        raise FileNotFoundError(f"no non-empty db3 in {session}")
    return max(bags, key=lambda path: path.stat().st_size)


def load_authoritative_frames(path: Path, stream: str) -> list[dict]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    frame_key = f"{stream}_frame_number"
    time_key = f"{stream}_device_ms"
    if not rows or frame_key not in rows[0] or time_key not in rows[0]:
        raise ValueError(f"{path} lacks {frame_key}/{time_key}")

    frames = []
    seen = set()
    previous_time = None
    for row in rows:
        if not row.get(frame_key) or not row.get(time_key):
            continue
        frame_number = int(row[frame_key])
        timestamp_s = float(row[time_key]) / 1000.0
        if frame_number in seen:
            raise ValueError(f"duplicate {stream} frame number {frame_number}")
        if previous_time is not None and timestamp_s <= previous_time:
            raise ValueError(f"non-monotonic {stream} timestamp at {frame_number}")
        frames.append({"frame_number": frame_number, "t_sec": timestamp_s})
        seen.add(frame_number)
        previous_time = timestamp_s
    if len(frames) < 2:
        raise ValueError(f"insufficient {stream} frames in {path}")
    return frames


def load_stereo_pairs(path: Path, left_frame_numbers: list[int]) -> list[dict]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    required = {
        "infrared_left_frame_number",
        "infrared_left_device_ms",
        "infrared_right_frame_number",
        "infrared_right_device_ms",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{path} lacks synchronized stereo frame columns")
    by_left = {
        int(row["infrared_left_frame_number"]): row
        for row in rows
        if row.get("infrared_left_frame_number")
        and row.get("infrared_right_frame_number")
    }
    missing = [number for number in left_frame_numbers if number not in by_left]
    if missing:
        raise ValueError(f"missing right-IR pairing for left frames {missing[:5]}")

    pairs = []
    for left_number in left_frame_numbers:
        row = by_left[left_number]
        left_ms = float(row["infrared_left_device_ms"])
        right_ms = float(row["infrared_right_device_ms"])
        skew_ms = abs(left_ms - right_ms)
        if skew_ms > 0.1:
            raise ValueError(
                "left/right IR are not hardware synchronized: "
                f"left={left_number}, skew={skew_ms:.6f} ms"
            )
        pairs.append(
            {
                "left_frame_number": left_number,
                "right_frame_number": int(row["infrared_right_frame_number"]),
                "right_t_sec": right_ms / 1000.0,
                "skew_ms": skew_ms,
            }
        )
    return pairs


def parse_metadata(text: str) -> tuple[int, float] | None:
    frame = FRAME_NUMBER_RE.search(text)
    timestamp = TIMESTAMP_RE.search(text)
    if not frame or not timestamp:
        return None
    return int(frame.group(1)), float(timestamp.group(1)) / 1000.0


def parse_camera_info(text: str) -> dict:
    fields = dict(item.split("=", 1) for item in text.split(";") if "=" in item)
    required = {"width", "height", "fx", "fy", "ppx", "ppy", "model", "coeffs"}
    if not required.issubset(fields):
        raise ValueError("incomplete RealSense camera_info")
    coefficients = [float(value) for value in fields["coeffs"].split(",")]
    return {
        "width": int(fields["width"]),
        "height": int(fields["height"]),
        "fx": float(fields["fx"]),
        "fy": float(fields["fy"]),
        "ppx": float(fields["ppx"]),
        "ppy": float(fields["ppy"]),
        "model": fields["model"],
        "coeffs": coefficients,
    }


def load_camera_info(db3: Path, stream: str) -> dict:
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from std_msgs.msg import String

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("", ""),
    )
    target = CAMERA_INFO_TOPICS[stream]
    set_topic_filter(reader, [target])
    while reader.has_next():
        topic, data, _ = reader.read_next()
        if topic == target:
            return parse_camera_info(deserialize_message(data, String).data)
    raise ValueError(f"camera_info not found for {stream}")


def load_stereo_baseline_m(db3: Path) -> float:
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from std_msgs.msg import String

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("", ""),
    )
    set_topic_filter(reader, [STEREO_BASELINE_TOPIC])
    while reader.has_next():
        topic, data, _ = reader.read_next()
        if topic == STEREO_BASELINE_TOPIC:
            baseline_m = float(deserialize_message(data, String).data) / 1000.0
            if not 0.005 <= baseline_m <= 0.1:
                raise ValueError(f"invalid factory stereo baseline: {baseline_m} m")
            return baseline_m
    raise ValueError("factory stereo baseline not found")


def mast3r_calibration(camera_info: dict) -> tuple[list[float], str]:
    calibration = [
        camera_info["fx"],
        camera_info["fy"],
        camera_info["ppx"],
        camera_info["ppy"],
    ]
    model = camera_info["model"].lower()
    if model == "brown conrady":
        calibration.extend(camera_info["coeffs"])
        return calibration, "opencv_brown_conrady"
    if model == "inverse brown conrady":
        return calibration, "intrinsics_only_inverse_brown_not_misused_as_forward"
    raise ValueError(f"unsupported RealSense distortion model: {camera_info['model']}")


def decode_image(message, stream: str) -> np.ndarray:
    height, width = int(message.height), int(message.width)
    payload = np.frombuffer(message.data, dtype=np.uint8)
    if stream == "color":
        expected = height * int(message.step)
        if payload.size != expected or int(message.step) != width * 2:
            raise ValueError(
                f"unexpected color image layout: {width}x{height}, "
                f"step={message.step}, bytes={payload.size}"
            )
        yuyv = payload.reshape(height, width, 2)
        return cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUY2)
    expected = height * int(message.step)
    if payload.size != expected or int(message.step) != width:
        raise ValueError(
            f"unexpected mono image layout: {width}x{height}, "
            f"step={message.step}, bytes={payload.size}"
        )
    return payload.reshape(height, width)


def crop_bottom(image: np.ndarray, pixels: int) -> np.ndarray:
    if pixels < 0 or pixels >= image.shape[0]:
        raise ValueError(
            f"crop-bottom must lie in [0, {image.shape[0] - 1}], got {pixels}"
        )
    return image if pixels == 0 else image[:-pixels]


def crop_camera_info_bottom(camera_info: dict, pixels: int) -> dict:
    height = int(camera_info["height"])
    if pixels < 0 or pixels >= height:
        raise ValueError(
            f"crop-bottom must lie in [0, {height - 1}], got {pixels}"
        )
    effective = dict(camera_info)
    effective["height"] = height - pixels
    return effective


def mask_fixed_self_occlusion(image: np.ndarray) -> np.ndarray:
    """Remove the camera-rigid UMI gripper without changing image geometry."""
    height, width = image.shape[:2]
    polygon = np.rint(
        np.asarray(
            [
                [0.525 * width, 0.63 * height],
                [0.605 * width, 0.63 * height],
                [0.680 * width, float(height - 1)],
                [0.450 * width, float(height - 1)],
            ]
        )
    ).astype(np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(mask, polygon, 255)
    feather_sigma = max(2.0, width / 256.0)
    alpha = cv2.GaussianBlur(mask, (0, 0), feather_sigma).astype(np.float32) / 255.0

    reference = image[
        max(0, int(round(0.55 * height))) : max(1, int(round(0.62 * height))),
        int(round(0.45 * width)) : int(round(0.68 * width)),
    ]
    fill = np.median(reference, axis=(0, 1)).astype(np.float32)
    source = image.astype(np.float32)
    if image.ndim == 3:
        alpha = alpha[..., None]
    return np.rint(source * (1.0 - alpha) + fill * alpha).astype(image.dtype)


def image_events(db3: Path, stream: str):
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Image
    from std_msgs.msg import String

    image_topic, metadata_topic = TOPICS[stream]
    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("", ""),
    )
    set_topic_filter(reader, [image_topic, metadata_topic])
    pending_image = None
    pending_metadata = None
    while reader.has_next():
        topic, data, _ = reader.read_next()
        if topic == image_topic:
            pending_image = deserialize_message(data, Image)
        elif topic == metadata_topic:
            pending_metadata = parse_metadata(deserialize_message(data, String).data)
        else:
            continue
        if pending_image is not None and pending_metadata is not None:
            yield pending_metadata[0], pending_metadata[1], pending_image
            pending_image = None
            pending_metadata = None


def export_dataset(
    session: Path,
    output: Path,
    stream: str,
    every: int,
    max_frames: int,
    start_index: int = 0,
    include_stereo_right: bool = False,
    crop_bottom_px: int = 0,
    mask_fixed_self: bool = False,
) -> dict:
    if every < 1:
        raise ValueError("--every must be at least 1")
    if start_index < 0:
        raise ValueError("--start-index must be non-negative")
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")

    frame_csv = session / "d405_frames.csv"
    db3 = select_db3(session)
    camera_info = load_camera_info(db3, stream)
    export_camera_info = crop_camera_info_bottom(camera_info, crop_bottom_px)
    calibration, distortion_policy = mast3r_calibration(export_camera_info)
    authoritative = load_authoritative_frames(frame_csv, stream)
    selected = authoritative[start_index::every]
    if max_frames > 0:
        selected = selected[:max_frames]
    expected = {row["frame_number"]: row["t_sec"] for row in selected}

    if include_stereo_right and stream != "infrared_left":
        raise ValueError("--include-stereo-right requires --stream infrared_left")

    output.mkdir(parents=True)
    exported_rows = []
    timestamp_deltas_ms = []
    for frame_number, metadata_time, message in image_events(db3, stream):
        authoritative_time = expected.get(frame_number)
        if authoritative_time is None:
            continue
        delta_ms = abs(metadata_time - authoritative_time) * 1000.0
        if delta_ms > 0.01:
            raise ValueError(
                f"frame {frame_number} metadata/CSV mismatch: {delta_ms:.6f} ms"
            )
        image_name = f"{len(exported_rows):010d}.png"
        image = crop_bottom(decode_image(message, stream), crop_bottom_px)
        if mask_fixed_self:
            image = mask_fixed_self_occlusion(image)
        if not cv2.imwrite(str(output / image_name), image):
            raise IOError(f"failed to write {output / image_name}")
        exported_rows.append(
            {
                "input_index": len(exported_rows),
                "image": image_name,
                "source_frame_number": frame_number,
                "t_sec": f"{authoritative_time:.9f}",
            }
        )
        timestamp_deltas_ms.append(delta_ms)
        if len(exported_rows) == len(selected):
            break

    missing = sorted(set(expected) - {row["source_frame_number"] for row in exported_rows})
    if missing:
        raise RuntimeError(
            f"db3 is missing {len(missing)} selected {stream} frames; first={missing[:5]}"
        )

    stereo_manifest = None
    if include_stereo_right:
        pairs = load_stereo_pairs(
            frame_csv, [row["source_frame_number"] for row in exported_rows]
        )
        right_info = load_camera_info(db3, "infrared_right")
        if (right_info["width"], right_info["height"]) != (
            camera_info["width"],
            camera_info["height"],
        ):
            raise ValueError("left/right IR resolutions differ")
        if max(
            abs(right_info[key] - camera_info[key])
            for key in ("fx", "fy", "ppx", "ppy")
        ) > 0.5:
            raise ValueError("left/right IR intrinsics differ")
        right_dir = output / "stereo_right"
        right_dir.mkdir()
        expected_right = {
            pair["right_frame_number"]: (index, pair["right_t_sec"])
            for index, pair in enumerate(pairs)
        }
        written_right = set()
        for frame_number, metadata_time, message in image_events(
            db3, "infrared_right"
        ):
            target = expected_right.get(frame_number)
            if target is None:
                continue
            index, authoritative_time = target
            delta_ms = abs(metadata_time - authoritative_time) * 1000.0
            if delta_ms > 0.01:
                raise ValueError(
                    f"right frame {frame_number} metadata/CSV mismatch: "
                    f"{delta_ms:.6f} ms"
                )
            image_name = f"{index:010d}.png"
            image = crop_bottom(
                decode_image(message, "infrared_right"), crop_bottom_px
            )
            if mask_fixed_self:
                image = mask_fixed_self_occlusion(image)
            if not cv2.imwrite(str(right_dir / image_name), image):
                raise IOError(f"failed to write {right_dir / image_name}")
            written_right.add(frame_number)
            if len(written_right) == len(expected_right):
                break
        missing_right = sorted(set(expected_right) - written_right)
        if missing_right:
            raise RuntimeError(
                "db3 is missing selected infrared_right frames; "
                f"first={missing_right[:5]}"
            )
        stereo_manifest = {
            "right_directory": "stereo_right",
            "baseline_m": load_stereo_baseline_m(db3),
            "left_focal_length_px": camera_info["fx"],
            "right_camera_info": right_info,
            "max_left_right_skew_ms": max(pair["skew_ms"] for pair in pairs),
            "frames": len(pairs),
        }

    frames_path = output / "frames.csv"
    with frames_path.open("w", newline="", encoding="utf-8") as stream_file:
        writer = csv.DictWriter(stream_file, fieldnames=exported_rows[0].keys())
        writer.writeheader()
        writer.writerows(exported_rows)

    (output / "calibration.yaml").write_text(
        json.dumps(
            {
                "width": export_camera_info["width"],
                "height": export_camera_info["height"],
                "calibration": calibration,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    acceptance_path = session / "acceptance.json"
    acceptance = (
        json.loads(acceptance_path.read_text(encoding="utf-8"))
        if acceptance_path.is_file()
        else None
    )
    manifest = {
        "schema": "umi_mast3r_dataset_v1",
        "slam_supervision": False,
        "source_session": str(session),
        "source_db3": str(db3),
        "source_db3_size_bytes": db3.stat().st_size,
        "source_frames_csv": str(frame_csv),
        "source_frames_csv_sha256": sha256(frame_csv),
        "source_capture_result": acceptance.get("result") if acceptance else None,
        "source_camera_serial": acceptance.get("camera_serial") if acceptance else None,
        "stream": stream,
        "image_encoding": "lossless_png_bgr8" if stream == "color" else "lossless_png_mono8",
        "timestamp_source": f"d405_frames.csv:{stream}_device_ms_global_time",
        "camera_info": export_camera_info,
        "source_camera_info": camera_info,
        "image_preprocessing": {
            "crop_bottom_px": crop_bottom_px,
            "mask_fixed_self_occlusion": mask_fixed_self,
            "purpose": "exclude camera-rigid self-occlusion from visual SLAM",
        },
        "mast3r_distortion_policy": distortion_policy,
        "mast3r_calibration": str((output / "calibration.yaml").resolve()),
        "every": every,
        "start_index": start_index,
        "frames": len(exported_rows),
        "first_t_sec": float(exported_rows[0]["t_sec"]),
        "last_t_sec": float(exported_rows[-1]["t_sec"]),
        "max_db3_vs_csv_timestamp_delta_ms": max(timestamp_deltas_ms, default=0.0),
    }
    if stereo_manifest is not None:
        manifest["stereo_depth_source"] = stereo_manifest
    (output / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stream", choices=sorted(TOPICS), default="color")
    parser.add_argument("--every", type=int, default=1)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--include-stereo-right", action="store_true")
    parser.add_argument("--crop-bottom-px", type=int, default=0)
    parser.add_argument("--mask-fixed-self", action="store_true")
    args = parser.parse_args()
    manifest = export_dataset(
        args.session.resolve(),
        args.output.resolve(),
        args.stream,
        args.every,
        args.max_frames,
        args.start_index,
        args.include_stereo_right,
        args.crop_bottom_px,
        args.mask_fixed_self,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
