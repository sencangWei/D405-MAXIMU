#!/usr/bin/env python3
"""D405 sensor 级回调丢帧测试 (终极方案)。

用 sensor.start(callback) 代替 pipeline.wait_for_frames():
  每路流独立回调, 不经 pipeline frameset 同步, 避免同步丢帧。

D405 只有一个 Stereo Module, 同时输出 color/ir1/ir2。
需一次 open 所有 profile, start 一个回调, 回调里按流类型分发。
"""
from __future__ import annotations

import argparse
import threading
import time

import pyrealsense2 as rs

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30


def test(duration: float):
    ctx = rs.context()
    dev = ctx.query_devices()[0]
    sens = dev.query_sensors()[0]
    print(f"传感器: {sens.get_info(rs.camera_info.name)}")

    # 收集需要的 profiles
    profiles = []
    for p in sens.get_stream_profiles():
        vs = p.as_video_stream_profile()
        if vs.width() != W or vs.height() != H or vs.fps() != FPS:
            continue
        t = p.stream_type()
        if t == rs.stream.color and p.format() == rs.format.yuyv:
            profiles.append(p)
        elif t == rs.stream.infrared and p.format() == rs.format.y8:
            profiles.append(p)
    print(f"启用 {len(profiles)} 个 profile")

    stats = {k: {"first": None, "last": None, "count": 0, "gaps": 0, "last_n": None}
             for k in ("color", "ir1", "ir2")}

    def cb(frame):
        t = frame.get_profile().stream_type()
        idx = frame.get_profile().as_video_stream_profile().stream_index()
        if t == rs.stream.color:
            key = "color"
        elif t == rs.stream.infrared and idx == 1:
            key = "ir1"
        elif t == rs.stream.infrared and idx == 2:
            key = "ir2"
        else:
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

    sens.open(profiles)
    sens.start(cb)
    time.sleep(1)  # 预热
    t0 = time.time()
    while time.time() - t0 < duration:
        time.sleep(1)
    sens.stop()
    sens.close()

    print(f"\n===== sensor 回调 ({duration:.0f}s) =====")
    for key, name in [("color", "Color"), ("ir1", "IR1"), ("ir2", "IR2")]:
        s = stats[key]
        if s["first"] is None:
            print(f"  {name}: 无帧")
            continue
        span = s["last"] - s["first"]
        rate = span / duration
        drop = s["gaps"] / span * 100 if span > 0 else 0
        missing = (span + 1) - s["count"]
        print(f"  {name}: 收到{s['count']}帧, 跨度{span}, 间隙{s['gaps']}, "
              f"速率{rate:.2f}Hz, 丢帧率{drop:.2f}%, 漏取{missing}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=180.0)
    args = ap.parse_args()
    test(args.duration)


if __name__ == "__main__":
    main()
