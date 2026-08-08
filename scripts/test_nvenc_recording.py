#!/usr/bin/env python3
"""三路流 + NVENC 硬件编码 丢帧/体积测试。

用 RTX 5090 的 NVENC 硬件编码三路 720p@30:
  - IR 用 HEVC 高质量 (SLAM 用, 接近无损)
  - RGB 用 H.264 (老板看, 体积极小)
  - 通过 FFmpeg 子进程喂帧, 硬件编码, CPU 占用极低

对比原始录 bag:
  - 原始: 4.3GB/40s (110MB/s)
  - NVENC: 预计 <100MB/40s
"""
from __future__ import annotations

import argparse
import subprocess
import time

import cv2
import numpy as np
import pyrealsense2 as rs

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30


def start_nvenc(path: str, quality: str, use_hevc: bool):
    codec = "hevc_nvenc" if use_hevc else "h264_nvenc"
    cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
        "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-c:v", codec,
        "-preset", "p6", "-tune", "ll", "-rc", "vbr",
        "-cq", quality,
        "-pix_fmt", "yuv420p",
        "-f", "mp4", path,
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def test(duration: float, use_hevc: bool, quality: str, rgb_path: str):
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(SERIAL)
    config.enable_stream(rs.stream.color, W, H, rs.format.yuyv, FPS)
    config.enable_stream(rs.stream.infrared, 1, W, H, rs.format.y8, FPS)
    config.enable_stream(rs.stream.infrared, 2, W, H, rs.format.y8, FPS)
    pipeline.start(config)
    for _ in range(30):
        pipeline.wait_for_frames()

    # 三个 NVENC 编码器
    enc_ir1 = start_nvenc("/tmp/nvenc_ir1.mp4", quality, use_hevc)
    enc_ir2 = start_nvenc("/tmp/nvenc_ir2.mp4", quality, use_hevc)
    enc_rgb = start_nvenc(rgb_path, "24", False)  # RGB 低码率

    stats = {k: {"first": None, "last": None, "count": 0, "gaps": 0, "last_n": None}
             for k in ("color", "ir1", "ir2")}

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
            i1 = frames.get_infrared_frame(1)
            i2 = frames.get_infrared_frame(2)
            c = frames.get_color_frame()

            track("ir1", i1); track("ir2", i2); track("color", c)

            if i1:
                enc_ir1.stdin.write(np.asanyarray(i1.get_data()).tobytes())
            if i2:
                enc_ir2.stdin.write(np.asanyarray(i2.get_data()).tobytes())
            if c:
                yuyv = np.asanyarray(c.get_data())
                if yuyv.dtype == np.uint16:
                    yuyv = yuyv.view(np.uint8).reshape(yuyv.shape + (2,))
                bgr = cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUY2)
                enc_rgb.stdin.write(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).tobytes())
    except KeyboardInterrupt:
        pass
    elapsed = time.time() - t0
    for enc in (enc_ir1, enc_ir2, enc_rgb):
        enc.stdin.close()
        enc.wait(timeout=10)
    pipeline.stop()

    print(f"\n===== NVENC {codec_label(use_hevc, quality)} ({duration:.0f}s) =====")
    for key, name in [("ir1", "IR1"), ("ir2", "IR2"), ("color", "Color")]:
        s = stats[key]
        span = s["last"] - s["first"]
        rate = span / elapsed
        drop = s["gaps"] / span * 100 if span > 0 else 0
        missing = (span + 1) - s["count"]
        print(f"  {name}: 收到{s['count']}帧 跨度{span} 间隙{s['gaps']} "
              f"速率{rate:.2f}Hz 丢帧率{drop:.2f}% 漏取{missing}")


def codec_label(hevc, quality):
    return f"HEVC-cq{quality}" if hevc else f"H264-cq{quality}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=40.0)
    args = ap.parse_args()
    test(args.duration, use_hevc=True, quality="18", rgb_path="/tmp/nvenc_rgb.mp4")


if __name__ == "__main__":
    main()
