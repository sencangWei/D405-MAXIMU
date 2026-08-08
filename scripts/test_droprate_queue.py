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
import threading
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


def method_callbacks(duration: float) -> dict:
    """方案C: low-level sensor 回调, 每流独立线程."""
    ctx = rs.context()
    dev = ctx.query_devices()[0]

    counts = {"color": 0, "ir1": 0, "ir2": 0, "color_nums": [], "ir1_nums": [], "ir2_nums": []}
    stop = threading.Event()

    def make_cb(key, nums):
        def cb(frame):
            if stop.is_set():
                return
            counts[key] += 1
            nums.append(frame.get_frame_number())
        return cb

    # 打开传感器并启流
    profiles = []
    for sens in dev.query_sensors():
        for p in sens.get_stream_profiles():
            vs = p.as_video_stream_profile()
            if p.stream_type() == rs.stream.color and vs.width() == W and vs.format() == rs.format.yuyv:
                if p.fps() == FPS:
                    sens.open(p)
                    sens.start(make_cb("color", counts["color_nums"]))
                    profiles.append(p)
            elif p.stream_type() == rs.stream.infrared and vs.width() == W and vs.format() == rs.format.y8:
                idx = vs.stream_index()
                if p.fps() == FPS:
                    sens.open(p)
                    if idx == 1:
                        sens.start(make_cb("ir1", counts["ir1_nums"]))
                    elif idx == 2:
                        sens.start(make_cb("ir2", counts["ir2_nums"]))
                    profiles.append(p)

    t0 = time.time()
    while time.time() - t0 < duration:
        time.sleep(0.5)
    stop.set()
    time.sleep(0.5)
    for sens in dev.query_sensors():
        try:
            sens.stop()
            sens.close()
        except Exception:
            pass
    counts["elapsed"] = time.time() - t0
    return counts


def report(name: str, counts: dict, duration: float):
    print(f"\n===== {name} =====")
    for key in ("color", "ir1", "ir2"):
        n = counts.get(key, 0)
        rate = n / duration
        expected = int(duration * FPS)
        print(f"  {key}: {n} 帧 ({rate:.1f}Hz)  期望{expected}  缺失{expected - n} ({(expected-n)/expected*100:.2f}%)")
    if counts.get("color_nums"):
        # 检查帧号连续性
        for key in ("color_nums", "ir1_nums", "ir2_nums"):
            nums = sorted(counts[key])
            skips = sum(1 for i in range(1, len(nums)) if nums[i] > nums[i-1] + 1)
            print(f"  {key} 帧号跳变: {skips} 处")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--methods", nargs="*", default=["all"],
                    choices=["wait", "queue", "callback", "all"])
    args = ap.parse_args()

    methods = ["wait", "queue", "callback"] if "all" in args.methods else args.methods
    for m in methods:
        try:
            if m == "wait":
                counts = method_wait_for_frames(args.duration)
            elif m == "queue":
                counts = method_frame_queue(args.duration)
            else:
                counts = method_callbacks(args.duration)
            report(m, counts, args.duration)
        except Exception as e:
            print(f"\n[{m}] 失败: {e}")


if __name__ == "__main__":
    main()
