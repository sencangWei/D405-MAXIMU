#!/usr/bin/env python3
"""D405 720p RGB + aligned-depth bench preview."""

import argparse
import time

import cv2
import numpy as np
import pyrealsense2 as rs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="260322273737")
    parser.add_argument("--max-depth", type=float, default=1.5)
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

    profile = pipeline.start(config)
    align = rs.align(rs.stream.color)
    depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()

    print("D405 720p RGB-D 已启动")
    print(
        f"序列号: {args.serial}  深度单位: {depth_scale:.6f} m  "
        f"曝光: {args.exposure_us} us  gain: {args.gain}"
    )
    print("把标定板放在约 0.2–0.5 m，检查彩图清晰且深度区域连续。按 Q 或 ESC 退出。")

    frame_count = 0
    start = time.monotonic()
    fps = 0.0
    try:
        while True:
            frames = align.process(pipeline.wait_for_frames(5000))
            color_frame = frames.get_color_frame()
            depth_frame = frames.get_depth_frame()
            if not color_frame or not depth_frame:
                continue

            color = np.asanyarray(color_frame.get_data())
            depth = np.asanyarray(depth_frame.get_data())
            depth_m = depth.astype(np.float32) * depth_scale

            valid = (depth_m > 0.0) & (depth_m <= args.max_depth)
            valid_ratio = 100.0 * float(np.count_nonzero(valid)) / valid.size
            h, w = depth.shape
            center = depth_m[h // 2 - 2:h // 2 + 3, w // 2 - 2:w // 2 + 3]
            center = center[center > 0.0]
            center_m = float(np.median(center)) if center.size else 0.0

            scaled = np.clip(depth_m / args.max_depth * 255.0, 0, 255).astype(np.uint8)
            depth_vis = cv2.applyColorMap(scaled, cv2.COLORMAP_TURBO)
            depth_vis[~valid] = 0

            frame_count += 1
            elapsed = time.monotonic() - start
            if elapsed >= 1.0:
                fps = frame_count / elapsed
                frame_count = 0
                start = time.monotonic()

            ts_ms = color_frame.get_timestamp()
            domain = str(color_frame.get_frame_timestamp_domain()).split(".")[-1]
            cv2.putText(color, "RGB 1280x720", (20, 38), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, (0, 255, 0), 2, cv2.LINE_AA)
            cv2.putText(color, f"FPS {fps:.1f}  {domain} {ts_ms:.1f} ms", (20, 76),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
            cv2.putText(depth_vis, "Aligned depth", (20, 38), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(depth_vis, f"center {center_m:.3f} m  valid {valid_ratio:.1f}%", (20, 76),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

            combined = np.hstack((color, depth_vis))
            if combined.shape[1] > 1800:
                scale = 1800.0 / combined.shape[1]
                combined = cv2.resize(combined, None, fx=scale, fy=scale,
                                      interpolation=cv2.INTER_AREA)
            cv2.imshow("D405 720p RGB + Depth", combined)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
