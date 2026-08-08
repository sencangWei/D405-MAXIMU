#!/usr/bin/env python3
"""三路分离录制丢帧测试: IR 录 bag + RGB 编 MP4。

验证: 当 RGB 走 H264/MP4 软件编码时, 双 IR 录进 bag 的丢帧率。

关键: IR 数据用 enable_record_to_file 底层录制(不受RGB编码影响),
      RGB 单独用 OpenCV 编码成 MP4 (给老板看)。
"""
from __future__ import annotations

import argparse
import time

import cv2
import numpy as np
import pyrealsense2 as rs

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30


def test(duration: float, rgb_encode: bool, bag_path: str):
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(SERIAL)
    config.enable_stream(rs.stream.color, W, H, rs.format.yuyv, FPS)
    config.enable_stream(rs.stream.infrared, 1, W, H, rs.format.y8, FPS)
    config.enable_stream(rs.stream.infrared, 2, W, H, rs.format.y8, FPS)
    config.enable_record_to_file(bag_path)  # IR + RGB 原始都进 bag

    writer = None
    if rgb_encode:
        writer = cv2.VideoWriter("/tmp/rgb_sep.mp4",
                                 cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))

    pipeline.start(config)
    for _ in range(50):
        pipeline.wait_for_frames()

    stats = {k: {"first": None, "last": None, "count": 0, "gaps": 0, "last_n": None}
             for k in ("ir1", "ir2", "color")}

    def track(key, frame):
        if frame is None:
            return
        s = stats[key]
        n = frame.get_frame_number()
        if s["first"] is None:
            s["first"] = n
        s["last"] = n
        s["count"] += 1
        if s["last_n"] is not None and n > s["last_n"] + 1:
            s["gaps"] += n - s["last_n"] - 1
        s["last_n"] = n

    t0 = time.time()
    try:
        while time.time() - t0 < duration:
            frames = pipeline.wait_for_frames(timeout_ms=2000)
            track("ir1", frames.get_infrared_frame(1))
            track("ir2", frames.get_infrared_frame(2))
            c = frames.get_color_frame()
            track("color", c)
            if writer and c:
                yuyv = np.asanyarray(c.get_data())
                if yuyv.dtype == np.uint16:
                    yuyv = yuyv.view(np.uint8).reshape(yuyv.shape + (2,))
                bgr = cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUY2)
                writer.write(bgr)
    except KeyboardInterrupt:
        pass
    elapsed = time.time() - t0
    if writer:
        writer.release()
    pipeline.stop()

    print(f"\n===== {'RGB-H264 + IR录bag' if rgb_encode else 'RGB原始 + IR录bag'} ({duration:.0f}s) =====")
    for key, name in [("ir1", "IR1"), ("ir2", "IR2"), ("color", "Color")]:
        s = stats[key]
        span = s["last"] - s["first"]
        rate = span / elapsed
        drop = s["gaps"] / span * 100 if span > 0 else 0
        missing = (span + 1) - s["count"]
        print(f"  {name}: 收到{s['count']}帧, 跨度{span}, 间隙{s['gaps']}, "
              f"速率{rate:.2f}Hz, 丢帧率{drop:.2f}%, 应用漏取{missing}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--method", choices=["both", "raw", "h264"], default="both")
    args = ap.parse_args()

    if args.method in ("raw", "both"):
        test(args.duration, rgb_encode=False, bag_path="/tmp/raw_test.db3")
    if args.method in ("h264", "both"):
        test(args.duration, rgb_encode=True, bag_path="/tmp/h264_test.db3")


if __name__ == "__main__":
    main()
