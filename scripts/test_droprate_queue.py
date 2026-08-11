#!/usr/bin/env python3
"""D405 三路采集丢帧率对比: wait_for_frames vs frame_queue(keep_frames).

方案:
  A. pipeline.wait_for_frames() 轮询 (当前方案)
  B. rs.frame_queue(capacity, keep_frames=True) 官方推荐
  C. low-level sensor 回调 (每个流独立回调)

保持 1280x720@30Hz 三路 (RGB YUYV + IR1 + IR2)。
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pyrealsense2 as rs

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30


def make_config():
    config = rs.config()
    config.enable_device(SERIAL)
    config.enable_stream(rs.stream.color, W, H, rs.format.yuyv, FPS)
    config.enable_stream(rs.stream.infrared, 1, W, H, rs.format.y8, FPS)
    config.enable_stream(rs.stream.infrared, 2, W, H, rs.format.y8, FPS)
    return config


def method_wait_for_frames(duration: float) -> dict:
    """方案A: wait_for_frames 轮询."""
    pipeline = rs.pipeline()
    pipeline.start(make_config())
    for _ in range(30):
        pipeline.wait_for_frames()

    counts = {"color": 0, "ir1": 0, "ir2": 0, "all": 0}
    t0 = time.time()
    while time.time() - t0 < duration:
        frames = pipeline.wait_for_frames(timeout_ms=2000)
        c = frames.get_color_frame()
        i1 = frames.get_infrared_frame(1)
        i2 = frames.get_infrared_frame(2)
        if c: counts["color"] += 1
        if i1: counts["ir1"] += 1
        if i2: counts["ir2"] += 1
        if c and i1 and i2: counts["all"] += 1
    pipeline.stop()
    counts["elapsed"] = time.time() - t0
    return counts


def method_frame_queue(duration: float) -> dict:
    """方案B: frame_queue(keep_frames=True)."""
    pipeline = rs.pipeline()
    queue = rs.frame_queue(100, keep_frames=True)
    pipeline.start(make_config(), queue)
    # 预热
    for _ in range(30):
        queue.wait_for_frame()

    counts = {"color": 0, "ir1": 0, "ir2": 0, "all": 0}
    t0 = time.time()
    while time.time() - t0 < duration:
        frames = queue.wait_for_frame(timeout_ms=2000)
        c = frames.as_frameset().get_color_frame()
        i1 = frames.as_frameset().get_infrared_frame(1)
        i2 = frames.as_frameset().get_infrared_frame(2)
        if c: counts["color"] += 1
        if i1: counts["ir1"] += 1
        if i2: counts["ir2"] += 1
        if c and i1 and i2: counts["all"] += 1
    pipeline.stop()
    counts["elapsed"] = time.time() - t0
    return counts


def method_sensor_queue(duration: float, stream_keys: set[str]) -> dict:
    """方案C: low-level sensor + native frame_queue, 绕过 pipeline 同步器.

    frame_queue 在 librealsense 的 C++ 回调线程中入队。Python 线程只负责
    取出单帧和统计帧号，不在相机回调里做转换、编码或文件 I/O。
    """
    ctx = rs.context()
    dev = next(
        d for d in ctx.query_devices()
        if d.get_info(rs.camera_info.serial_number) == SERIAL
    )
    sens = dev.first_depth_sensor()

    counts = {"color": 0, "ir1": 0, "ir2": 0, "color_nums": [], "ir1_nums": [], "ir2_nums": []}
    profiles = []
    for profile in sens.get_stream_profiles():
        video = profile.as_video_stream_profile()
        if video.width() != W or video.height() != H or profile.fps() != FPS:
            continue
        if (
            "color" in stream_keys
            and profile.stream_type() == rs.stream.color
            and profile.format() == rs.format.yuyv
        ):
            profiles.append(profile)
        elif profile.stream_type() == rs.stream.infrared and profile.format() == rs.format.y8:
            index = video.stream_index()
            if index == 1 and "ir1" in stream_keys:
                profiles.append(profile)
            elif index == 2 and "ir2" in stream_keys:
                profiles.append(profile)

    selected = {
        "color" if p.stream_type() == rs.stream.color
        else f"ir{p.as_video_stream_profile().stream_index()}"
        for p in profiles
    }
    if selected != stream_keys:
        raise RuntimeError(f"三路 profile 不完整: {selected}")

    queue = rs.frame_queue(300, keep_frames=True)
    sens.open(profiles)
    sens.start(queue)
    try:
        # 单帧队列中三路各占一项，预热约 1 秒。
        for _ in range(FPS * len(stream_keys)):
            queue.wait_for_frame(timeout_ms=2000)

        t0 = time.monotonic()
        while time.monotonic() - t0 < duration:
            frame = queue.wait_for_frame(timeout_ms=2000)
            profile = frame.get_profile()
            stream = profile.stream_type()
            index = profile.as_video_stream_profile().stream_index()
            if stream == rs.stream.color:
                key = "color"
            elif stream == rs.stream.infrared and index == 1:
                key = "ir1"
            elif stream == rs.stream.infrared and index == 2:
                key = "ir2"
            else:
                continue
            counts[key] += 1
            counts[f"{key}_nums"].append(int(frame.get_frame_number()))
        counts["elapsed"] = time.monotonic() - t0
    finally:
        sens.stop()
        sens.close()
    return counts


def report(name: str, counts: dict, duration: float):
    print(f"\n===== {name} =====")
    elapsed = counts.get("elapsed", duration)
    for key in ("color", "ir1", "ir2"):
        n = counts.get(key, 0)
        rate = n / elapsed if elapsed > 0 else 0.0
        print(f"  {key}: {n} 帧 ({rate:.2f}Hz)")
    for key in ("color_nums", "ir1_nums", "ir2_nums"):
        nums = counts.get(key, [])
        if nums:
            gaps = [nums[i] - nums[i - 1] - 1 for i in range(1, len(nums)) if nums[i] > nums[i - 1] + 1]
            print(f"  {key} 帧号间隙: {sum(gaps)} 帧 / {len(gaps)} 处")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--methods", nargs="*", default=["all"],
                    choices=["wait", "queue", "sensor_queue", "all"])
    ap.add_argument(
        "--sensor-streams",
        nargs="+",
        choices=["color", "ir1", "ir2"],
        default=["color", "ir1", "ir2"],
        help="sensor_queue 方法启用的原始流",
    )
    args = ap.parse_args()

    methods = ["wait", "queue", "sensor_queue"] if "all" in args.methods else args.methods
    for m in methods:
        try:
            if m == "wait":
                counts = method_wait_for_frames(args.duration)
            elif m == "queue":
                counts = method_frame_queue(args.duration)
            else:
                counts = method_sensor_queue(args.duration, set(args.sensor_streams))
            report(m, counts, args.duration)
        except Exception as e:
            print(f"\n[{m}] 失败: {e}")


if __name__ == "__main__":
    main()
