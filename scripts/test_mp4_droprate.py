#!/usr/bin/env python3
"""D405 三路采集丢帧率测试: RGB(YUYV) + 双IR  vs  RGB(H264/MP4) + 双IR。

对比两种传输方案的丢帧率:
  A. RGB 原始 YUYV + IR1 + IR2 (全原始传输)
  B. RGB 软件编码 H264(MP4) + IR1 + IR2 (RGB压缩传输)

用法:
  python3 scripts/test_mp4_droprate.py --duration 30
  python3 scripts/test_mp4_droprate.py --duration 60 --mode h264
"""
from __future__ import annotations

import argparse
import time

import cv2
import numpy as np
import pyrealsense2 as rs

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--mode", choices=["raw", "h264", "both"], default="both")
    return p.parse_args()


def run_test(mode: str, duration: float) -> dict:
    """mode='raw': RGB原始+双IR; mode='h264': RGB编码+双IR."""
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(SERIAL)
    config.enable_stream(rs.stream.color, W, H, rs.format.yuyv, FPS)
    config.enable_stream(rs.stream.infrared, 1, W, H, rs.format.y8, FPS)
    config.enable_stream(rs.stream.infrared, 2, W, H, rs.format.y8, FPS)

    # 软件编码器 (RGB -> MP4)
    writer = None
    if mode == "h264":
        writer = cv2.VideoWriter(
            "/tmp/rgb_h264.mp4", cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
        if not writer.isOpened():
            print("[WARN] MP4 编码器打开失败, 回退到原始")
            mode = "raw"

    profile = pipeline.start(config)
    warmup = 30
    for _ in range(warmup):
        pipeline.wait_for_frames()

    stats = {
        "mode": mode,
        "frames": 0,
        "color_ok": 0,
        "ir1_ok": 0,
        "ir2_ok": 0,
        "all_ok": 0,
        "color_skipped": 0,
        "ir1_skipped": 0,
        "ir2_skipped": 0,
        "color_repeated": 0,
        "last_cnum": None, "last_i1num": None, "last_i2num": None,
        "cpu_usage": [],
    }

    t0 = time.time()
    try:
        while time.time() - t0 < duration:
            frames = pipeline.wait_for_frames(timeout_ms=2000)
            c = frames.get_color_frame()
            ir1 = frames.get_infrared_frame(1)
            ir2 = frames.get_infrared_frame(2)

            stats["frames"] += 1
            if c:
                stats["color_ok"] += 1
                cnum = c.get_frame_number()
                if stats["last_cnum"] is not None and cnum > stats["last_cnum"] + 1:
                    stats["color_skipped"] += cnum - stats["last_cnum"] - 1
                stats["last_cnum"] = cnum

                if writer:
                    yuyv = np.asanyarray(c.get_data())
                    if yuyv.dtype == np.uint16:
                        yuyv = yuyv.view(np.uint8).reshape(yuyv.shape + (2,))
                    bgr = cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUY2)
                    writer.write(bgr)

            if ir1:
                stats["ir1_ok"] += 1
                n1 = ir1.get_frame_number()
                if stats["last_i1num"] is not None and n1 > stats["last_i1num"] + 1:
                    stats["ir1_skipped"] += n1 - stats["last_i1num"] - 1
                stats["last_i1num"] = n1
            if ir2:
                stats["ir2_ok"] += 1
                n2 = ir2.get_frame_number()
                if stats["last_i2num"] is not None and n2 > stats["last_i2num"] + 1:
                    stats["ir2_skipped"] += n2 - stats["last_i2num"] - 1
                stats["last_i2num"] = n2

            if c and ir1 and ir2:
                stats["all_ok"] += 1

    except KeyboardInterrupt:
        pass
    finally:
        if writer:
            writer.release()
        pipeline.stop()

    elapsed = time.time() - t0
    stats["elapsed"] = elapsed
    return stats


def report(stats: dict):
    mode = stats["mode"]
    n = stats["frames"]
    el = stats["elapsed"]
    print(f"\n===== 模式: {mode.upper()} =====")
    print(f"帧集: {n}, 时长: {el:.1f}s, 帧率: {n/el:.1f}Hz (期望 {FPS})")
    print(f"三路齐全帧: {stats['all_ok']} ({stats['all_ok']/el:.1f}Hz)")
    print(f"Color 收到 {stats['color_ok']}, 丢 {stats['color_skipped']} 帧")
    print(f"IR1   收到 {stats['ir1_ok']}, 丢 {stats['ir1_skipped']} 帧")
    print(f"IR2   收到 {stats['ir2_ok']}, 丢 {stats['ir2_skipped']} 帧")
    total_expected = int(el * FPS)
    for name, key in [("Color", "color_skipped"), ("IR1", "ir1_skipped"), ("IR2", "ir2_skipped")]:
        skip = stats[key]
        rate = skip / total_expected * 100 if total_expected > 0 else 0
        print(f"  {name} 丢帧率: {skip}/{total_expected} = {rate:.1f}%")


def main():
    args = parse_args()
    if args.mode == "raw":
        run_list = ["raw"]
    elif args.mode == "h264":
        run_list = ["h264"]
    else:
        run_list = ["raw", "h264"]

    for m in run_list:
        stats = run_test(m, args.duration)
        report(stats)

    print("\n对比:")
    print("  丢帧率越低越好; CPU 占用高说明软件编码是瓶颈")


if __name__ == "__main__":
    main()
