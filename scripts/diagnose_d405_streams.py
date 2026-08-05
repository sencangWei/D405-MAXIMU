#!/usr/bin/env python3
"""Measure D405 device frame continuity without visualization or VIO."""

import argparse
import time

import numpy as np
import pyrealsense2 as rs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="260322273737")
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--all4", action="store_true")
    parser.add_argument("--exposure-us", type=int, default=20000)
    parser.add_argument("--gain", type=int, default=48)
    args = parser.parse_args()

    context = rs.context()
    device = next(
        (
            d for d in context.query_devices()
            if d.get_info(rs.camera_info.serial_number) == args.serial
        ),
        None,
    )
    if device is None:
        raise RuntimeError(f"找不到D405(serial={args.serial})")
    sensor = device.first_depth_sensor()
    sensor.set_option(rs.option.enable_auto_exposure, 0)
    sensor.set_option(rs.option.exposure, float(args.exposure_us))
    sensor.set_option(rs.option.gain, float(args.gain))

    pipeline = rs.pipeline(context)
    config = rs.config()
    config.enable_device(args.serial)
    config.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
    config.enable_stream(rs.stream.depth, 1280, 720, rs.format.z16, 30)
    keys = ["color", "depth"]
    if args.all4:
        config.enable_stream(rs.stream.infrared, 1, 1280, 720, rs.format.y8, 30)
        config.enable_stream(rs.stream.infrared, 2, 1280, 720, rs.format.y8, 30)
        keys.extend(("ir1", "ir2"))

    def get_frame(frames, key):
        if key == "color":
            return frames.get_color_frame()
        if key == "depth":
            return frames.get_depth_frame()
        return frames.get_infrared_frame(int(key[-1]))

    pipeline.start(config)
    try:
        for _ in range(30):
            pipeline.wait_for_frames(5000)
        arrivals = []
        numbers = {key: [] for key in keys}
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            frames = pipeline.wait_for_frames(5000)
            arrivals.append(time.monotonic())
            for key in keys:
                frame = get_frame(frames, key)
                if frame:
                    numbers[key].append(int(frame.get_frame_number()))
    finally:
        pipeline.stop()

    intervals_ms = np.diff(arrivals) * 1000.0
    gaps = {
        key: sum(max(0, current - previous - 1) for previous, current in zip(values, values[1:]))
        for key, values in numbers.items()
    }
    fps = (len(arrivals) - 1) / (arrivals[-1] - arrivals[0])
    print(f"module={rs.__file__}")
    print(
        f"streams={'all4' if args.all4 else 'rgbd'} frames={len(arrivals)} "
        f"fps={fps:.3f} jitter={np.std(intervals_ms):.3f}ms "
        f"p99={np.percentile(intervals_ms, 99):.3f}ms "
        f"max={np.max(intervals_ms):.3f}ms gaps={gaps}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
