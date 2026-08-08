#!/usr/bin/env python3
"""Record D405 720p color, stereo IR, depth, and the external IMU.

The RealSense streams are stored losslessly in the RSUSB rosbag2 sqlite file
used on this host. Color is recorded in the camera-native YUYV format to avoid
dropping frames in the BGR conversion path; the preview is still shown in
color. A CSV keeps device timestamps alongside host arrival time. The external
IMU remains in the project's established imu.bin/imu_ts.csv format.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pyrealsense2 as rs

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.imu_reader import ImuReader
from ego_vio.recorder.recorder import UnitRecorder


STREAMS = (
    ("color", rs.stream.color, 0, rs.format.yuyv),
    ("infrared_left", rs.stream.infrared, 1, rs.format.y8),
    ("infrared_right", rs.stream.infrared, 2, rs.format.y8),
)


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
    parser.add_argument("--output-root", type=Path, default=ROOT / "recordings")
    parser.add_argument("--no-preview", action="store_true")
    return parser.parse_args()


def frame_meta(frame, epoch_offset: float) -> tuple[int, float, float, str]:
    if not frame:
        return -1, float("nan"), float("nan"), "missing"
    device_ms = float(frame.get_timestamp())
    domain = str(frame.get_frame_timestamp_domain()).split(".")[-1]
    if frame.get_frame_timestamp_domain() == rs.timestamp_domain.global_time:
        mono = device_ms / 1000.0 - epoch_offset
    else:
        mono = float("nan")
    return int(frame.get_frame_number()), device_ms, mono, domain


def preview_mosaic(frames) -> np.ndarray:
    color_yuyv = np.asanyarray(frames.get_color_frame().get_data())
    if color_yuyv.dtype == np.uint16:
        color_yuyv = color_yuyv.view(np.uint8).reshape(color_yuyv.shape + (2,))
    color = cv2.cvtColor(color_yuyv, cv2.COLOR_YUV2BGR_YUY2)
    left = np.asanyarray(frames.get_infrared_frame(1).get_data())
    right = np.asanyarray(frames.get_infrared_frame(2).get_data())

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
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = args.output_root.resolve() / f"d405_720p_rgb_stereo_ir_{stamp}"
    session.mkdir(parents=True, exist_ok=False)
    # This host uses the RSUSB/rosbag2 recorder backend, which requires the
    # sqlite3 extension rather than librealsense's legacy .bag suffix.
    bag_path = session / "d405_720p_rgb_stereo_ir.db3"
    frame_csv_path = session / "d405_frames.csv"

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(args.serial)
    for _, stream, index, fmt in STREAMS:
        config.enable_stream(stream, index, 1280, 720, fmt, 30)
    config.enable_record_to_file(str(bag_path))

    imu_recorder = UnitRecorder("external_imu", session, save_depth=False, max_queue=8000)
    imu = ImuReader(
        args.imu_port,
        baud=args.imu_baud,
        warmup_frames=500,
        on_sample=imu_recorder.put_imu,
        name="all_streams_imu",
    )

    profile = None
    rows = 0
    missing_sets = 0
    start_mono = None
    end_mono = None
    last_frame_numbers: dict[str, int] = {}
    skipped_frames = {name: 0 for name, *_ in STREAMS}
    repeated_frames = {name: 0 for name, *_ in STREAMS}
    frame_number_resets = {name: 0 for name, *_ in STREAMS}
    epoch_offset = time.time() - time.monotonic()
    fields = ["set_index", "arrival_mono", "arrival_wall"]
    for name, *_ in STREAMS:
        fields.extend(
            [f"{name}_frame_number", f"{name}_device_ms", f"{name}_mono", f"{name}_domain"]
        )

    try:
        profile = pipeline.start(config)
        imu_recorder.start()
        if not imu.start():
            raise RuntimeError(f"无法打开IMU串口: {args.imu_port}")

        print(f"[全流采集] 输出目录: {session}")
        print("[全流采集] 1280x720@30: 彩色YUYV + 左IR + 右IR；IMU 400Hz")
        print(f"[全流采集] 相机预热 {args.warmup_frames} 帧（预热数据仍保存在原始录制中）")
        print(f"[全流采集] 静态验收 {args.duration:.1f} 秒，按 q 可提前结束")

        for _ in range(max(0, args.warmup_frames)):
            pipeline.wait_for_frames(5000)

        with frame_csv_path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writeheader()
            while True:
                frames = pipeline.wait_for_frames(5000)
                now_mono = time.monotonic()
                if start_mono is None:
                    start_mono = now_mono
                if args.duration > 0 and now_mono - start_mono >= args.duration:
                    break

                frame_map = {
                    "color": frames.get_color_frame(),
                    "infrared_left": frames.get_infrared_frame(1),
                    "infrared_right": frames.get_infrared_frame(2),
                }
                if not all(frame_map.values()):
                    missing_sets += 1
                    continue

                row = {
                    "set_index": rows,
                    "arrival_mono": f"{now_mono:.9f}",
                    "arrival_wall": f"{time.time():.6f}",
                }
                for name, frame in frame_map.items():
                    number, device_ms, mono, domain = frame_meta(frame, epoch_offset)
                    row[f"{name}_frame_number"] = number
                    row[f"{name}_device_ms"] = f"{device_ms:.6f}"
                    row[f"{name}_mono"] = f"{mono:.9f}" if np.isfinite(mono) else ""
                    row[f"{name}_domain"] = domain
                writer.writerow(row)
                rows += 1
                end_mono = now_mono

                for name, frame in frame_map.items():
                    number = int(frame.get_frame_number())
                    previous = last_frame_numbers.get(name)
                    if previous is not None:
                        if number > previous + 1:
                            skipped_frames[name] += number - previous - 1
                        elif number == previous:
                            repeated_frames[name] += 1
                        elif number < previous:
                            frame_number_resets[name] += 1
                    last_frame_numbers[name] = number

                if not args.no_preview:
                    cv2.imshow("D405 720p: RGB | IR Left | IR Right", preview_mosaic(frames))
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
    finally:
        imu.stop()
        imu_recorder.stop()
        if profile is not None:
            try:
                pipeline.stop()
            except Exception:
                pass
        cv2.destroyAllWindows()

    duration = (
        max(end_mono - start_mono, 1e-9)
        if start_mono is not None and end_mono is not None
        else 0.0
    )
    frameset_rate = (rows - 1) / duration if duration > 0 and rows > 1 else 0.0
    report = {
        "session": str(session),
        "bag": str(bag_path),
        "resolution": [1280, 720],
        "fps_requested": 30,
        "color_record_format": "YUYV",
        "preview_color_format": "BGR",
        "warmup_frames": max(0, args.warmup_frames),
        "framesets": rows,
        "missing_framesets": missing_sets,
        "skipped_frames_by_stream": skipped_frames,
        "repeated_frames_by_stream": repeated_frames,
        "frame_number_resets_by_stream": frame_number_resets,
        "frameset_rate_hz": frameset_rate,
        "duration_s": duration,
        "imu": imu.stats(),
    }
    (session / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    camera_ok = (
        frameset_rate >= 27.0
        and missing_sets == 0
        and not any(frame_number_resets.values())
    )
    return 0 if camera_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
