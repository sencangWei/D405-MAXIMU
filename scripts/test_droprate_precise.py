#!/usr/bin/env python3
"""三路流精确丢帧测试 (用帧号跨度, 不用墙钟计时)。

跑 120 秒, 统计每路流的:
  - 收到帧数
  - 相机帧号跨度 (=相机实际产生数)
  - 帧号间隙 (中间丢帧数)
  - 真实丢帧率 = 帧号间隙 / 帧号跨度

结论判定:
  - 帧号间隙 = 0 且 跨度/时长 ≈ 30Hz -> 零丢帧
  - 帧号间隙 > 0 -> 中间真有帧丢失 (相机或传输)
  - 收到数 < 跨度 -> 应用层没取完, 但帧号连续说明相机OK
"""
from __future__ import annotations

import argparse
import time

import pyrealsense2 as rs

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30


def test(duration: float, use_queue: bool):
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(SERIAL)
    config.enable_stream(rs.stream.color, W, H, rs.format.yuyv, FPS)
    config.enable_stream(rs.stream.infrared, 1, W, H, rs.format.y8, FPS)
    config.enable_stream(rs.stream.infrared, 2, W, H, rs.format.y8, FPS)

    if use_queue:
        queue = rs.frame_queue(300, keep_frames=True)
        pipeline.start(config, queue)
        get = lambda: queue.wait_for_frame(timeout_ms=2000)
    else:
        pipeline.start(config)
        get = lambda: pipeline.wait_for_frames(timeout_ms=2000)

    # 预热
    for _ in range(50):
        get()

    # 每路: (first_frame_number, last_frame_number, count, gaps)
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
            f = get()
            fs = f.as_frameset() if use_queue else f
            track("color", fs.get_color_frame())
            track("ir1", fs.get_infrared_frame(1))
            track("ir2", fs.get_infrared_frame(2))
    except KeyboardInterrupt:
        pass
    elapsed = time.time() - t0
    pipeline.stop()

    print(f"\n===== {'frame_queue' if use_queue else 'wait_for_frames'} ({duration:.0f}s) =====")
    for key, name in [("color", "Color"), ("ir1", "IR1"), ("ir2", "IR2")]:
        s = stats[key]
        if s["first"] is None:
            print(f"  {name}: 无帧")
            continue
        span = s["last"] - s["first"]
        rate = span / elapsed
        drop = s["gaps"] / span * 100 if span > 0 else 0
        missing = (span + 1) - s["count"]  # 应用漏取的
        print(f"  {name}: 收到{s['count']}帧, 帧号跨度{span}, 间隙{s['gaps']}, "
              f"速率{rate:.2f}Hz, 丢帧率{drop:.2f}%, 应用漏取{missing}")

    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--method", choices=["queue", "wait", "both"], default="both")
    args = ap.parse_args()

    if args.method in ("queue", "both"):
        test(args.duration, use_queue=True)
    if args.method in ("wait", "both"):
        test(args.duration, use_queue=False)


if __name__ == "__main__":
    main()
