#!/usr/bin/env python3
"""Record D405 720p color, stereo IR, and the external IMU.

The RealSense streams are stored losslessly in a rosbag2 sqlite file. The SDK
backend is selected by the launcher. Color remains in camera-native YUYV; BGR
conversion is preview-only. The timestamp CSV is reconstructed from the
recorded DB3 so a slow preview cannot alter the SLAM input timeline. The
external IMU remains in the project's established imu.bin/imu_ts.csv format.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pyrealsense2 as rs

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.imu_reader import ImuReader
from ego_vio.recorder.recorder import IMU_PACK_SIZE, UnitRecorder


STREAMS = (
    ("color", rs.stream.color, 0, rs.format.yuyv),
    ("infrared_left", rs.stream.infrared, 1, rs.format.y8),
    ("infrared_right", rs.stream.infrared, 2, rs.format.y8),
)

STREAM_KEYS = tuple(name for name, *_ in STREAMS)
IMU_WARMUP_FRAMES = 500
MIN_CAMERA_RATE_HZ = 29.5
MAX_CAMERA_GAP_RATIO = 0.001
MIN_IMU_RATE_HZ = 399.0
MAX_IMU_RATE_HZ = 401.0
SYNC_TOLERANCE_MS = 2.0
CAMERA_RAW_BYTES_PER_SECOND = 1280 * 720 * (2 + 1 + 1) * 30
STAGING_HEADROOM_RATIO = 1.15
MONITOR_QUEUE_CAPACITY = 64
DEFAULT_IR_AUTO_EXPOSURE_LIMIT_US = 8000.0
DEFAULT_IR_AUTO_GAIN_LIMIT = 248.0
IR_EXPOSURE_LIMIT_TOLERANCE_US = 100.0
FRAME_NUMBER_RE = re.compile(r"(?:^|;)Frame number=(\d+)")
TIMESTAMP_RE = re.compile(r"(?:^|;)timestamp=([0-9.]+)")
ACTUAL_EXPOSURE_RE = re.compile(r"(?:^|;)Actual Exposure=([0-9.]+)")
GAIN_LEVEL_RE = re.compile(r"(?:^|;)Gain Level=([0-9.]+)")
METADATA_TOPICS = {
    "color": "/device_0/sensor_0/Color_0/image/metadata",
    "infrared_left": "/device_0/sensor_0/Infrared_1/image/metadata",
    "infrared_right": "/device_0/sensor_0/Infrared_2/image/metadata",
}


@dataclass
class StreamContinuity:
    received: int = 0
    first_number: int | None = None
    last_number: int | None = None
    first_timestamp_ms: float | None = None
    last_timestamp_ms: float | None = None
    skipped_frames: int = 0
    gap_events: int = 0
    repeated_frames: int = 0
    frame_number_resets: int = 0
    timestamp_regressions: int = 0

    def add(self, number: int, timestamp_ms: float) -> None:
        if self.first_number is None:
            self.first_number = number
            self.first_timestamp_ms = timestamp_ms
        if self.last_number is not None:
            if number > self.last_number + 1:
                self.skipped_frames += number - self.last_number - 1
                self.gap_events += 1
            elif number == self.last_number:
                self.repeated_frames += 1
            elif number < self.last_number:
                self.frame_number_resets += 1
        if self.last_timestamp_ms is not None and timestamp_ms < self.last_timestamp_ms:
            self.timestamp_regressions += 1
        self.received += 1
        self.last_number = number
        self.last_timestamp_ms = timestamp_ms

    def report(self) -> dict:
        number_span = (
            self.last_number - self.first_number
            if self.first_number is not None and self.last_number is not None
            else 0
        )
        timestamp_span_s = (
            (self.last_timestamp_ms - self.first_timestamp_ms) / 1000.0
            if self.first_timestamp_ms is not None
            and self.last_timestamp_ms is not None
            and self.last_timestamp_ms >= self.first_timestamp_ms
            else 0.0
        )
        return {
            "received": self.received,
            "first_frame_number": self.first_number,
            "last_frame_number": self.last_number,
            "skipped_frames": self.skipped_frames,
            "gap_events": self.gap_events,
            "gap_ratio": round(self.skipped_frames / number_span, 9) if number_span else 0.0,
            "rate_hz": round(number_span / timestamp_span_s, 6) if timestamp_span_s else 0.0,
            "repeated_frames": self.repeated_frames,
            "frame_number_resets": self.frame_number_resets,
            "timestamp_regressions": self.timestamp_regressions,
        }


@dataclass(frozen=True)
class MetadataFrame:
    number: int
    device_ms: float
    exposure_us: float | None = None
    gain: float | None = None


def stats_delta(start: dict, end: dict) -> dict:
    return {key: end.get(key, 0) - value for key, value in start.items()}


def timestamps_aligned(timestamps_ms: list[float], tolerance_ms: float) -> bool:
    return bool(timestamps_ms) and max(timestamps_ms) - min(timestamps_ms) <= tolerance_ms


def decode_cdr_string(blob: bytes) -> str:
    if len(blob) < 8:
        raise ValueError("CDR string 数据不足 8 字节")
    little_endian = blob[1] == 1
    length = struct.unpack_from("<I" if little_endian else ">I", blob, 4)[0]
    if length < 1 or 8 + length > len(blob):
        raise ValueError(f"CDR string 长度无效: {length}")
    return blob[8:8 + length - 1].decode("utf-8")


def metadata_frame_from_text(text: str) -> MetadataFrame | None:
    number_match = FRAME_NUMBER_RE.search(text)
    timestamp_match = TIMESTAMP_RE.search(text)
    if not number_match or not timestamp_match:
        return None
    exposure_match = ACTUAL_EXPOSURE_RE.search(text)
    gain_match = GAIN_LEVEL_RE.search(text)
    return MetadataFrame(
        number=int(number_match.group(1)),
        device_ms=float(timestamp_match.group(1)),
        exposure_us=float(exposure_match.group(1)) if exposure_match else None,
        gain=float(gain_match.group(1)) if gain_match else None,
    )


def read_db3_metadata(bag_path: Path) -> dict[str, list[MetadataFrame]]:
    records = {key: [] for key in STREAM_KEYS}
    topic_to_key = {topic: key for key, topic in METADATA_TOPICS.items()}
    placeholders = ",".join("?" for _ in topic_to_key)
    query = f"""
        SELECT topics.name, messages.data
        FROM messages JOIN topics ON messages.topic_id = topics.id
        WHERE topics.name IN ({placeholders})
        ORDER BY messages.id
    """
    with sqlite3.connect(bag_path) as connection:
        for topic, blob in connection.execute(query, tuple(topic_to_key)):
            text = decode_cdr_string(blob)
            frame = metadata_frame_from_text(text)
            if frame is not None:
                records[topic_to_key[topic]].append(frame)
    return records


def analyze_ir_exposure(
    records: dict[str, list[MetadataFrame]],
    limit_us: float,
    tolerance_us: float = IR_EXPOSURE_LIMIT_TOLERANCE_US,
) -> dict:
    streams = {}
    for key in ("infrared_left", "infrared_right"):
        stream_records = records.get(key, [])
        exposures = [
            record.exposure_us
            for record in stream_records
            if record.exposure_us is not None
        ]
        gains = [record.gain for record in stream_records if record.gain is not None]
        complete = (
            bool(stream_records)
            and len(exposures) == len(stream_records)
            and len(gains) == len(stream_records)
        )
        within_limit = bool(exposures) and max(exposures) <= limit_us + tolerance_us
        streams[key] = {
            "result": "PASS" if complete and within_limit else "FAIL",
            "metadata_complete": complete,
            "metadata_frames": len(stream_records),
            "exposure_samples": len(exposures),
            "gain_samples": len(gains),
            "exposure_us": {
                "min": float(np.min(exposures)) if exposures else None,
                "median": float(np.median(exposures)) if exposures else None,
                "p95": float(np.percentile(exposures, 95)) if exposures else None,
                "max": float(np.max(exposures)) if exposures else None,
            },
            "gain": {
                "min": float(np.min(gains)) if gains else None,
                "median": float(np.median(gains)) if gains else None,
                "p95": float(np.percentile(gains, 95)) if gains else None,
                "max": float(np.max(gains)) if gains else None,
            },
        }
    return {
        "result": (
            "PASS"
            if all(stream["result"] == "PASS" for stream in streams.values())
            else "FAIL"
        ),
        "requested_limit_us": limit_us,
        "tolerance_us": tolerance_us,
        "streams": streams,
    }


def configure_ir_auto_exposure(
    sensor, exposure_limit_us: float, gain_limit: float
) -> dict:
    required_options = (
        rs.option.enable_auto_exposure,
        rs.option.auto_exposure_limit_toggle,
        rs.option.auto_exposure_limit,
        rs.option.auto_gain_limit_toggle,
        rs.option.auto_gain_limit,
    )
    unsupported = [str(option) for option in required_options if not sensor.supports(option)]
    if unsupported:
        raise RuntimeError(f"D405缺少自动曝光限制选项: {unsupported}")

    exposure_range = sensor.get_option_range(rs.option.auto_exposure_limit)
    gain_range = sensor.get_option_range(rs.option.auto_gain_limit)
    if not exposure_range.min <= exposure_limit_us <= exposure_range.max:
        raise ValueError(
            f"IR自动曝光上限超出范围: {exposure_limit_us} not in "
            f"[{exposure_range.min}, {exposure_range.max}]"
        )
    if not gain_range.min <= gain_limit <= gain_range.max:
        raise ValueError(
            f"IR自动增益上限超出范围: {gain_limit} not in "
            f"[{gain_range.min}, {gain_range.max}]"
        )

    sensor.set_option(rs.option.enable_auto_exposure, 1.0)
    sensor.set_option(rs.option.auto_exposure_limit_toggle, 1.0)
    sensor.set_option(rs.option.auto_exposure_limit, float(exposure_limit_us))
    sensor.set_option(rs.option.auto_gain_limit_toggle, 1.0)
    sensor.set_option(rs.option.auto_gain_limit, float(gain_limit))
    applied = {
        "enable_auto_exposure": sensor.get_option(rs.option.enable_auto_exposure),
        "auto_exposure_limit_toggle": sensor.get_option(
            rs.option.auto_exposure_limit_toggle
        ),
        "auto_exposure_limit_us": sensor.get_option(rs.option.auto_exposure_limit),
        "auto_gain_limit_toggle": sensor.get_option(rs.option.auto_gain_limit_toggle),
        "auto_gain_limit": sensor.get_option(rs.option.auto_gain_limit),
    }
    exposure_tolerance = max(float(getattr(exposure_range, "step", 0.0)), 1e-6)
    gain_tolerance = max(float(getattr(gain_range, "step", 0.0)), 1e-6)
    mismatches = []
    if applied["enable_auto_exposure"] < 0.5:
        mismatches.append("enable_auto_exposure")
    if applied["auto_exposure_limit_toggle"] < 0.5:
        mismatches.append("auto_exposure_limit_toggle")
    if (
        abs(applied["auto_exposure_limit_us"] - exposure_limit_us)
        > exposure_tolerance
    ):
        mismatches.append("auto_exposure_limit_us")
    if applied["auto_gain_limit_toggle"] < 0.5:
        mismatches.append("auto_gain_limit_toggle")
    if abs(applied["auto_gain_limit"] - gain_limit) > gain_tolerance:
        mismatches.append("auto_gain_limit")
    if mismatches:
        raise RuntimeError(f"IR自动曝光配置读回值不一致: {mismatches}; applied={applied}")
    return {
        "result": "PASS",
        "requested": {
            "auto_exposure_limit_us": exposure_limit_us,
            "auto_gain_limit": gain_limit,
        },
        "applied": applied,
        "takes_effect": "next_streaming_session",
    }


def analyze_metadata_records(
    records: dict[str, list[MetadataFrame]],
) -> dict[str, dict]:
    stats = {key: StreamContinuity() for key in STREAM_KEYS}
    for key, stream_records in records.items():
        for record in stream_records:
            stats[key].add(record.number, record.device_ms)
    return {key: value.report() for key, value in stats.items()}


def pair_metadata_frames(
    records: dict[str, list[MetadataFrame]], tolerance_ms: float
) -> list[dict[str, MetadataFrame]]:
    indexes = {key: 0 for key in STREAM_KEYS}
    pairs = []
    while all(indexes[key] < len(records[key]) for key in STREAM_KEYS):
        current = {key: records[key][indexes[key]] for key in STREAM_KEYS}
        timestamps = [record.device_ms for record in current.values()]
        if timestamps_aligned(timestamps, tolerance_ms):
            pairs.append(current)
            for key in STREAM_KEYS:
                indexes[key] += 1
            continue
        earliest = min(timestamps)
        for key, record in current.items():
            if record.device_ms == earliest:
                indexes[key] += 1
    return pairs


def write_frames_csv(
    path: Path,
    records: dict[str, list[MetadataFrame]],
    epoch_offset: float,
    tolerance_ms: float,
) -> int:
    fields = ["set_index", "arrival_mono", "arrival_wall"]
    for name in STREAM_KEYS:
        fields.extend(
            [f"{name}_frame_number", f"{name}_device_ms", f"{name}_mono", f"{name}_domain"]
        )
    pairs = pair_metadata_frames(records, tolerance_ms)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for index, pair in enumerate(pairs):
            # librealsense recorder writes messages.timestamp relative to bag
            # start, not Unix epoch. All selected D405 streams use global_time,
            # so the latest exposure timestamp is the stable wall/mono proxy.
            arrival_wall = max(record.device_ms for record in pair.values()) / 1000.0
            row = {
                "set_index": index,
                "arrival_mono": f"{arrival_wall - epoch_offset:.9f}",
                "arrival_wall": f"{arrival_wall:.6f}",
            }
            for name, record in pair.items():
                row[f"{name}_frame_number"] = record.number
                row[f"{name}_device_ms"] = f"{record.device_ms:.6f}"
                row[f"{name}_mono"] = f"{record.device_ms / 1000.0 - epoch_offset:.9f}"
                row[f"{name}_domain"] = "global_time"
            writer.writerow(row)
    return len(pairs)


def analyze_db3_metadata(bag_path: Path) -> dict[str, dict]:
    return analyze_metadata_records(read_db3_metadata(bag_path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="D405 720p三路(RGB+双IR)＋外置IMU采集")
    parser.add_argument("--serial", default="260322273737")
    parser.add_argument(
        "--imu-port",
        default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00",
    )
    parser.add_argument("--imu-baud", type=int, default=921600)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--warmup-frames", type=int, default=30)
    parser.add_argument(
        "--ir-auto-exposure-limit-us",
        type=float,
        default=DEFAULT_IR_AUTO_EXPOSURE_LIMIT_US,
        help="双IR自动曝光上限；必须在开流前设置，默认8000us",
    )
    parser.add_argument(
        "--ir-auto-gain-limit",
        type=float,
        default=DEFAULT_IR_AUTO_GAIN_LIMIT,
        help="双IR自动增益上限，默认248",
    )
    parser.add_argument("--output-root", type=Path, default=ROOT / "recordings")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument(
        "--no-ram-stage",
        action="store_true",
        help="直接写输出硬盘；默认先写/dev/shm，停止采集后再搬运DB3",
    )
    parser.add_argument(
        "--ram-stage-root",
        type=Path,
        default=Path("/dev/shm/ego_vio_d405"),
    )
    return parser.parse_args()


def required_staging_bytes(duration_s: float) -> int:
    if duration_s <= 0:
        return 0
    return int(CAMERA_RAW_BYTES_PER_SECOND * duration_s * STAGING_HEADROOM_RATIO)


def stream_key(frame) -> str | None:
    profile = frame.get_profile()
    stream = profile.stream_type()
    index = profile.as_video_stream_profile().stream_index()
    if stream == rs.stream.color:
        return "color"
    if stream == rs.stream.infrared and index == 1:
        return "infrared_left"
    if stream == rs.stream.infrared and index == 2:
        return "infrared_right"
    return None


def select_profiles(sensor) -> list:
    profiles = []
    for profile in sensor.get_stream_profiles():
        video = profile.as_video_stream_profile()
        if video.width() != 1280 or video.height() != 720 or profile.fps() != 30:
            continue
        if profile.stream_type() == rs.stream.color and profile.format() == rs.format.yuyv:
            profiles.append(profile)
        elif (
            profile.stream_type() == rs.stream.infrared
            and profile.format() == rs.format.y8
            and video.stream_index() in (1, 2)
        ):
            profiles.append(profile)
    selected = {stream_key_from_profile(profile) for profile in profiles}
    if selected != set(STREAM_KEYS):
        raise RuntimeError(f"D405 720p 三路 profile 不完整: {sorted(selected)}")
    return profiles


def stream_key_from_profile(profile) -> str | None:
    stream = profile.stream_type()
    index = profile.as_video_stream_profile().stream_index()
    if stream == rs.stream.color:
        return "color"
    if stream == rs.stream.infrared and index == 1:
        return "infrared_left"
    if stream == rs.stream.infrared and index == 2:
        return "infrared_right"
    return None


def preview_mosaic(frame_map: dict) -> np.ndarray:
    color_yuyv = np.asanyarray(frame_map["color"].get_data())
    if color_yuyv.dtype == np.uint16:
        color_yuyv = color_yuyv.view(np.uint8).reshape(color_yuyv.shape + (2,))
    color = cv2.cvtColor(color_yuyv, cv2.COLOR_YUV2BGR_YUY2)
    left = np.asanyarray(frame_map["infrared_left"].get_data())
    right = np.asanyarray(frame_map["infrared_right"].get_data())

    left_bgr = cv2.cvtColor(left, cv2.COLOR_GRAY2BGR)
    right_bgr = cv2.cvtColor(right, cv2.COLOR_GRAY2BGR)
    tiles = []
    for label, image in (
        ("RGB", color),
        ("IR Left", left_bgr),
        ("IR Right", right_bgr),
    ):
        tile = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
        cv2.putText(tile, label, (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        tiles.append(tile)
    return np.hstack(tiles)


def main() -> int:
    args = parse_args()
    realsense_backend = os.environ.get("EGO_VIO_REALSENSE_BACKEND", "system-default")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = args.output_root.resolve() / f"d405_720p_rgb_stereo_ir_{stamp}"
    session.mkdir(parents=True, exist_ok=False)
    bag_path = session / "d405_720p_rgb_stereo_ir.db3"
    frame_csv_path = session / "d405_frames.csv"
    use_ram_stage = not args.no_ram_stage and args.duration > 0
    record_path = bag_path
    staging_required_bytes = required_staging_bytes(args.duration)
    output_free = shutil.disk_usage(session).free
    if staging_required_bytes and output_free < staging_required_bytes:
        raise RuntimeError(
            "输出硬盘空间不足: "
            f"需要约 {staging_required_bytes / 1e9:.2f}GB, "
            f"可用 {output_free / 1e9:.2f}GB"
        )
    if use_ram_stage:
        args.ram_stage_root.mkdir(parents=True, exist_ok=True)
        staging_free = shutil.disk_usage(args.ram_stage_root).free
        if staging_free < staging_required_bytes:
            raise RuntimeError(
                "内存盘空间不足: "
                f"需要约 {staging_required_bytes / 1e9:.2f}GB, "
                f"可用 {staging_free / 1e9:.2f}GB；可缩短时长或使用--no-ram-stage"
            )
        record_path = args.ram_stage_root / f"{session.name}_{bag_path.name}"

    imu_recorder = UnitRecorder("external_imu", session, save_depth=False, max_queue=8000)
    recording_active = threading.Event()

    def record_imu(sample) -> None:
        if recording_active.is_set():
            imu_recorder.put_imu(sample)

    imu = ImuReader(
        args.imu_port,
        baud=args.imu_baud,
        warmup_frames=0,
        on_sample=record_imu,
        name="all_streams_imu",
    )

    context = rs.context()
    base_device = next(
        (
            device for device in context.query_devices()
            if device.get_info(rs.camera_info.serial_number) == args.serial
        ),
        None,
    )
    if base_device is None:
        raise RuntimeError(f"找不到 D405(serial={args.serial})")
    firmware = base_device.get_info(rs.camera_info.firmware_version)
    usb_type = base_device.get_info(rs.camera_info.usb_type_descriptor)

    recorder_device = rs.recorder(str(record_path), base_device)
    recorder_device.pause()
    sensor = recorder_device.first_depth_sensor()
    profiles = select_profiles(sensor)
    frame_queue = rs.frame_queue(MONITOR_QUEUE_CAPACITY, keep_frames=True)

    stream_stats = {key: StreamContinuity() for key in STREAM_KEYS}
    warmup_counts = {key: 0 for key in STREAM_KEYS}
    warmup_cutoff = {key: -1 for key in STREAM_KEYS}
    latest_frames: dict[str, object] = {}
    last_complete_signature = None
    complete_sets = 0
    formal_start_mono = None
    formal_stop_mono = None
    imu_stats_start = {}
    imu_stats_end = {}
    capture_error = None
    imu_recorder_started = False
    imu_started = False
    sensor_opened = False
    sensor_started = False
    recorder_resumed = False
    stage_move_error = None
    stage_move_duration_s = 0.0
    ir_exposure_configuration = None
    epoch_offset = time.time() - time.monotonic()
    try:
        imu_recorder.start()
        imu_recorder_started = True
        if not imu.start():
            raise RuntimeError(f"无法打开IMU串口: {args.imu_port}")
        imu_started = True

        ir_exposure_configuration = configure_ir_auto_exposure(
            sensor,
            exposure_limit_us=args.ir_auto_exposure_limit_us,
            gain_limit=args.ir_auto_gain_limit,
        )
        sensor.open(profiles)
        sensor_opened = True
        sensor.start(frame_queue)
        sensor_started = True

        print(f"[全流采集] 输出目录: {session}")
        print("[全流采集] 原生 recorder + sensor frame_queue（录制与预览解耦）")
        if use_ram_stage:
            print(
                f"[全流采集] DB3实时写入内存盘，结束后搬到输出目录；"
                f"预估占用 {staging_required_bytes / 1e9:.2f}GB"
            )
        print("[全流采集] 1280x720@30: 彩色YUYV + 左IR + 右IR；IMU 400Hz")
        print(
            "[全流采集] 双IR自动曝光上限 "
            f"{args.ir_auto_exposure_limit_us:.0f}us，"
            f"自动增益上限 {args.ir_auto_gain_limit:.0f}"
        )
        print(
            f"[全流采集] 预热: 相机每路 {max(0, args.warmup_frames)} 帧，"
            f"IMU {IMU_WARMUP_FRAMES} 帧；预热数据不写入正式文件"
        )

        warmup_deadline = time.monotonic() + 15.0
        while True:
            frame = frame_queue.wait_for_frame(timeout_ms=2000)
            key = stream_key(frame)
            if key is not None:
                warmup_counts[key] += 1
                warmup_cutoff[key] = int(frame.get_frame_number())
            camera_ready = all(
                count >= max(0, args.warmup_frames)
                for count in warmup_counts.values()
            )
            if camera_ready and imu.frames_ok >= IMU_WARMUP_FRAMES:
                break
            if time.monotonic() >= warmup_deadline:
                raise RuntimeError(
                    f"预热超时: camera={warmup_counts}, imu={imu.frames_ok}"
                )

        imu_counter_keys = (
            "frames_ok",
            "frames_bad",
            "resyncs",
            "dropped_frames",
            "counter_resets",
            "counter_stalls",
            "serial_errors",
            "serial_reconnects",
        )
        while True:
            queued_frame = frame_queue.poll_for_frame()
            if not queued_frame:
                break
            key = stream_key(queued_frame)
            if key is not None:
                warmup_cutoff[key] = int(queued_frame.get_frame_number())

        imu_stats_snapshot = imu.stats()
        imu_stats_start = {
            key: imu_stats_snapshot.get(key, 0) for key in imu_counter_keys
        }
        recorder_device.resume()
        recorder_resumed = True
        formal_start_mono = time.monotonic()
        recording_active.set()
        print(f"[全流采集] 正式采集 {args.duration:.1f} 秒；按 q 可提前结束")

        next_preview_mono = formal_start_mono
        stop_requested = False
        while True:
            frame = frame_queue.wait_for_frame(timeout_ms=2000)
            now_mono = time.monotonic()
            if args.duration > 0 and now_mono - formal_start_mono >= args.duration:
                break
            key = stream_key(frame)
            if key is None:
                continue
            number = int(frame.get_frame_number())
            if number <= warmup_cutoff[key]:
                continue
            device_ms = float(frame.get_timestamp())
            stream_stats[key].add(number, device_ms)
            latest_frames[key] = frame

            if len(latest_frames) == len(STREAM_KEYS):
                timestamps_ms = [
                    float(latest_frames[name].get_timestamp())
                    for name in STREAM_KEYS
                ]
                signature = tuple(
                    int(latest_frames[name].get_frame_number())
                    for name in STREAM_KEYS
                )
                aligned = timestamps_aligned(timestamps_ms, SYNC_TOLERANCE_MS)
            else:
                signature = None
                aligned = False

            if aligned and signature != last_complete_signature:
                complete_sets += 1
                if not args.no_preview and now_mono >= next_preview_mono:
                    cv2.imshow(
                        "D405 720p: RGB | IR Left | IR Right",
                        preview_mosaic(latest_frames),
                    )
                    next_preview_mono = now_mono + 0.1
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        stop_requested = True
                last_complete_signature = signature
            if stop_requested:
                break
        formal_stop_mono = time.monotonic()
    except Exception as exc:
        capture_error = f"{type(exc).__name__}: {exc}"
        print(f"[全流采集] ERROR: {capture_error}")
    finally:
        recording_active.clear()
        if formal_start_mono is not None and formal_stop_mono is None:
            formal_stop_mono = time.monotonic()
        if recorder_resumed:
            try:
                recorder_device.pause()
            except Exception:
                pass
        if imu_started:
            imu_stats_end = imu.stats()
        if sensor_started:
            try:
                sensor.stop()
            except Exception:
                pass
        if sensor_opened:
            try:
                sensor.close()
            except Exception:
                pass
        if imu_started:
            imu.stop()
        if imu_recorder_started:
            imu_recorder.stop()
        cv2.destroyAllWindows()
        sensor = None
        recorder_device = None
        base_device = None
        gc.collect()

    if use_ram_stage and record_path.exists():
        move_start = time.monotonic()
        try:
            print(f"[全流采集] 相机已停止，正在搬运DB3到: {bag_path}")
            shutil.move(str(record_path), str(bag_path))
            stage_move_duration_s = time.monotonic() - move_start
            print(f"[全流采集] DB3搬运完成，用时 {stage_move_duration_s:.1f}s")
        except Exception as exc:
            stage_move_error = f"{type(exc).__name__}: {exc}"
            if capture_error is None:
                capture_error = f"DB3搬运失败: {stage_move_error}；暂存文件保留在 {record_path}"

    duration = (
        max(formal_stop_mono - formal_start_mono, 1e-9)
        if formal_start_mono is not None and formal_stop_mono is not None
        else 0.0
    )
    source_streams = {key: value.report() for key, value in stream_stats.items()}
    bag_error = None
    bag_streams = {}
    db3_records = {}
    csv_error = None
    csv_rows = 0
    if bag_path.exists() and bag_path.stat().st_size > 0:
        try:
            db3_records = read_db3_metadata(bag_path)
            bag_streams = analyze_metadata_records(db3_records)
            csv_rows = write_frames_csv(
                frame_csv_path, db3_records, epoch_offset, SYNC_TOLERANCE_MS
            )
        except Exception as exc:
            bag_error = f"{type(exc).__name__}: {exc}"
            csv_error = bag_error
    else:
        bag_error = "db3 不存在或为空"
    ir_exposure_acceptance = analyze_ir_exposure(
        db3_records,
        limit_us=args.ir_auto_exposure_limit_us,
    )

    imu_counter_keys = (
        "frames_ok",
        "frames_bad",
        "resyncs",
        "dropped_frames",
        "counter_resets",
        "counter_stalls",
        "serial_errors",
        "serial_reconnects",
    )
    imu_formal = stats_delta(
        {key: imu_stats_start.get(key, 0) for key in imu_counter_keys},
        {key: imu_stats_end.get(key, 0) for key in imu_counter_keys},
    ) if imu_stats_start and imu_stats_end else {}
    imu_path = session / "external_imu" / "imu.bin"
    imu_samples_written = (
        imu_path.stat().st_size // IMU_PACK_SIZE if imu_path.exists() else 0
    )
    imu_rate_hz = imu_samples_written / duration if duration > 0 else 0.0

    camera_ok = (
        capture_error is None
        and bag_error is None
        and csv_error is None
        and ir_exposure_configuration is not None
        and ir_exposure_acceptance["result"] == "PASS"
        and csv_rows > 1
        and set(bag_streams) == set(STREAM_KEYS)
        and all(
            stream["received"] > 1
            and stream["rate_hz"] >= MIN_CAMERA_RATE_HZ
            and stream["gap_ratio"] <= MAX_CAMERA_GAP_RATIO
            and stream["repeated_frames"] == 0
            and stream["frame_number_resets"] == 0
            and stream["timestamp_regressions"] == 0
            for stream in bag_streams.values()
        )
    )
    imu_ok = (
        bool(imu_formal)
        and MIN_IMU_RATE_HZ <= imu_rate_hz <= MAX_IMU_RATE_HZ
        and imu_formal.get("frames_bad", 1) == 0
        and imu_formal.get("resyncs", 1) == 0
        and imu_formal.get("dropped_frames", 1) == 0
        and imu_formal.get("counter_resets", 1) == 0
        and imu_formal.get("counter_stalls", 1) == 0
        and imu_formal.get("serial_errors", 1) == 0
        and imu_formal.get("serial_reconnects", 1) == 0
        and imu_recorder.dropped == 0
    )
    report = {
        "result": "PASS" if camera_ok and imu_ok else "FAIL",
        "session": str(session),
        "bag": str(bag_path),
        "capture_error": capture_error,
        "resolution": [1280, 720],
        "fps_requested": 30,
        "color_record_format": "YUYV",
        "preview_color_format": "BGR",
        "capture_backend": (
            f"rs.recorder + direct sensor frame_queue ({realsense_backend})"
        ),
        "realsense_python_module": str(Path(rs.__file__).resolve()),
        "monitor_queue_capacity": MONITOR_QUEUE_CAPACITY,
        "camera_storage": {
            "ram_staged": use_ram_stage,
            "staging_path": str(record_path) if use_ram_stage else None,
            "staging_required_bytes": staging_required_bytes if use_ram_stage else 0,
            "move_duration_s": stage_move_duration_s,
            "move_error": stage_move_error,
        },
        "camera_serial": args.serial,
        "camera_firmware": firmware,
        "usb_type": usb_type,
        "warmup_frames": max(0, args.warmup_frames),
        "imu_warmup_frames": IMU_WARMUP_FRAMES,
        "warmup_recorded": False,
        "complete_framesets_for_csv": csv_rows,
        "monitor_complete_framesets": complete_sets,
        "csv_sync_tolerance_ms": SYNC_TOLERANCE_MS,
        "duration_s": duration,
        "thresholds": {
            "camera_min_rate_hz": MIN_CAMERA_RATE_HZ,
            "camera_max_gap_ratio": MAX_CAMERA_GAP_RATIO,
            "ir_actual_exposure_max_us": args.ir_auto_exposure_limit_us,
            "ir_exposure_tolerance_us": IR_EXPOSURE_LIMIT_TOLERANCE_US,
            "imu_rate_hz": [MIN_IMU_RATE_HZ, MAX_IMU_RATE_HZ],
        },
        "camera": {
            "result": "PASS" if camera_ok else "FAIL",
            "monitor_queue_streams": source_streams,
            "db3_streams": bag_streams,
            "db3_analysis_error": bag_error,
            "csv_rebuild_error": csv_error,
            "authoritative_stream_stats": "db3_streams",
            "ir_auto_exposure_configuration": ir_exposure_configuration,
            "ir_exposure_acceptance": ir_exposure_acceptance,
            "db3_size_bytes": bag_path.stat().st_size if bag_path.exists() else 0,
        },
        "imu": {
            "result": "PASS" if imu_ok else "FAIL",
            "warmup_cumulative_stats": imu_stats_start,
            "formal_window_stats": imu_formal,
            "samples_written": imu_samples_written,
            "rate_hz": imu_rate_hz,
            "recorder_drops": imu_recorder.dropped,
        },
    }
    (session / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
