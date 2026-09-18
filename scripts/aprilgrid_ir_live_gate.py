#!/usr/bin/env python3
"""Show both D405 IR streams and require a stable AprilGrid detection."""

from __future__ import annotations

import argparse
import time

import cv2
import numpy as np
import pyrealsense2 as rs
from aprilgrid import Detector


def valid_tag_count(detections, max_tag_id: int) -> int:
    return sum(0 <= int(detection.tag_id) < max_tag_id for detection in detections)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="260322279785")
    parser.add_argument("--min-tags", type=int, default=4)
    parser.add_argument("--stable-frames", type=int, default=15)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--tag-rows", type=int, default=6)
    parser.add_argument("--tag-cols", type=int, default=6)
    args = parser.parse_args()
    if args.min_tags <= 0 or args.stable_frames <= 0 or args.timeout <= 0:
        raise ValueError("min-tags, stable-frames and timeout must be positive")

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(args.serial)
    config.enable_stream(rs.stream.infrared, 1, 1280, 720, rs.format.y8, 30)
    config.enable_stream(rs.stream.infrared, 2, 1280, 720, rs.format.y8, 30)
    detector = Detector("t36h11")
    window = "D405 APRILGRID VISIBILITY GATE - IR LEFT | IR RIGHT"
    max_tag_id = args.tag_rows * args.tag_cols
    stable = 0
    started = time.monotonic()
    pipeline_started = False

    try:
        pipeline.start(config)
        pipeline_started = True
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window, 1280, 420)
        cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 1)
        while time.monotonic() - started < args.timeout:
            frames = pipeline.wait_for_frames(2000)
            left_frame = frames.get_infrared_frame(1)
            right_frame = frames.get_infrared_frame(2)
            if not left_frame or not right_frame:
                stable = 0
                continue
            left = np.asanyarray(left_frame.get_data())
            right = np.asanyarray(right_frame.get_data())
            left_count = valid_tag_count(detector.detect(left), max_tag_id)
            right_count = valid_tag_count(detector.detect(right), max_tag_id)
            ready = left_count >= args.min_tags and right_count >= args.min_tags
            stable = stable + 1 if ready else 0

            tiles = []
            for label, image, count in (
                ("IR LEFT", left, left_count),
                ("IR RIGHT", right, right_count),
            ):
                tile = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
                tile = cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR)
                color = (0, 220, 0) if count >= args.min_tags else (0, 0, 255)
                cv2.putText(
                    tile,
                    f"{label}: {count} tags",
                    (18, 38),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    color,
                    2,
                    cv2.LINE_AA,
                )
                tiles.append(tile)
            mosaic = np.hstack(tiles)
            status = (
                f"READY {stable}/{args.stable_frames}"
                if ready
                else f"SHOW FULL 6x6 APRILGRID (need >= {args.min_tags} tags per IR)"
            )
            cv2.putText(
                mosaic,
                status,
                (18, 345),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (0, 220, 0) if ready else (0, 180, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow(window, mosaic)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                print("APRILGRID_GATE_ABORTED")
                return 2
            if stable >= args.stable_frames:
                print(
                    "APRILGRID_GATE_PASS "
                    f"left_tags={left_count} right_tags={right_count} "
                    f"stable_frames={stable}"
                )
                return 0
        print("APRILGRID_GATE_TIMEOUT")
        return 2
    finally:
        if pipeline_started:
            pipeline.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
