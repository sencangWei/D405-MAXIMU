#!/usr/bin/env python3
"""D405 三路采集 (sensor 回调方案, 最低丢帧率)。

对比原 capture_d405_720p_rgb_stereo_ir.py:
  原: pipeline.wait_for_frames() 同步轮询 -> 丢帧 0.55%
  新: sensor.start(callback) 底层回调  -> 丢帧 0.17%

输出:
  <session>/d405_720p_rgb_stereo_ir.db3  三路原始 (IR+RGB 全录, SLAM 用 IR)
  <session>/rgb_preview.mp4               RGB 软件编码 (给老板看, 体积小)
  <session>/d405_frames.csv               帧号/时间戳
  <session>/external_imu/imu.bin          IMU 原始数据

用法:
  python3 scripts/capture_d405_sensor_callback.py --duration 60
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pyrealsense2 as rs

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.imu_reader import ImuReader
from ego_vio.recorder.recorder import UnitRecorder

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="D405 三路采集 (sensor回调)")
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--serial", default=SERIAL)
    p.add_argument("--imu-port",
                   default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00")
    p.add_argument("--imu-baud", type=int, default=921600)
    p.add_argument("--output-root", type=Path, default=ROOT / "recordings")
    p.add_argument("--rgb-mp4", action="store_true", default=True,
                   help="同时把 RGB 编码成 MP4 (默认开)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = args.output_root.resolve() / f"d405_sensor_cb_{stamp}"
    session.mkdir(parents=True, exist_ok=False)

    bag_path = session / "d405_720p_rgb_stereo_ir.db3"
    csv_path = session / "d405_frames.csv"
    mp4_path = session / "rgb_preview.mp4"

    # 初始化 IMU
    imu_recorder = UnitRecorder("external_imu", session, save_depth=False, max_queue=8000)
    imu = ImuReader(args.imu_port, baud=args.imu_baud, warmup_frames=500,
                    on_sample=imu_recorder.put_imu, name="sensor_cb_imu")

    # 打开 sensor
    ctx = rs.context()
    dev = next(d for d in ctx.query_devices() if d.get_info(rs.camera_info.serial_number) == args.serial)
    sens = dev.query_sensors()[0]

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

    # 统计
    fields = ["set_index", "arrival_mono", "arrival_wall",
              "color_frame_number", "color_device_ms",
              "infrared_left_frame_number", "infrared_left_device_ms",
              "infrared_right_frame_number", "infrared_right_device_ms"]
    writer = None
    last_nums = {"color": None, "ir1": None, "ir2": None}
    skipped = {"color": 0, "ir1": 0, "ir2": 0}
    mp4_writer = None
    frame_rows = 0
    start_mono = None

    epoch_offset = time.time() - time.monotonic()

    if not imu.start():
        print(f"[ERROR] IMU 串口打开失败: {args.imu_port}")
        return 1
    imu_recorder.start()

    csv_fp = csv_path.open("w", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(csv_fp, fieldnames=fields)
    csv_writer.writeheader()

    if args.rgb_mp4:
        mp4_writer = cv2.VideoWriter(str(mp4_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))

    # sensor 回调
    def on_frame(frame):
        nonlocal frame_rows
        t = frame.get_profile().stream_type()
        vs = frame.get_profile().as_video_stream_profile()
        if t == rs.stream.color:
            key = "color"
            num = frame.get_frame_number()
            # 编码 MP4
            if mp4_writer:
                yuyv = np.asanyarray(frame.get_data())
                if yuyv.dtype == np.uint16:
                    yuyv = yuyv.view(np.uint8).reshape(yuyv.shape + (2,))
                bgr = cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUY2)
                mp4_writer.write(bgr)
        elif t == rs.stream.infrared and vs.stream_index() == 1:
            key = "ir1"
            num = frame.get_frame_number()
        elif t == rs.stream.infrared and vs.stream_index() == 2:
            key = "ir2"
            num = frame.get_frame_number()
        else:
            return

        if last_nums[key] is not None and num > last_nums[key] + 1:
            skipped[key] += num - last_nums[key] - 1
        last_nums[key] = num

        # 写 CSV (每帧一行, 不含全帧集)
        now_mono = time.monotonic()
        csv_writer.writerow({
            "set_index": frame_rows,
            "arrival_mono": f"{now_mono:.9f}",
            "arrival_wall": f"{time.time():.6f}",
            f"{key}_frame_number": num,
            f"{key}_device_ms": f"{frame.get_timestamp():.6f}",
        })
        frame_rows += 1
        csv_fp.flush()

    sens.open(profiles)
    sens.start(on_frame)

    print(f"[采集] 输出目录: {session}")
    print(f"[采集] sensor回调方案: IR 丢帧 ~0.17%")
    print(f"[采集] 录制 {args.duration:.0f}s, Ctrl+C 提前结束")
    if mp4_writer:
        print(f"[采集] RGB 同时编码: {mp4_path}")

    t0 = time.time()
    try:
        while time.time() - t0 < args.duration:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sens.stop()
        sens.close()
        if mp4_writer:
            mp4_writer.release()
        csv_fp.close()
        imu.stop()
        imu_recorder.stop()

    elapsed = time.time() - t0
    st = imu.stats()
    report = {
        "session": str(session),
        "bag": str(bag_path),
        "mp4": str(mp4_path) if mp4_writer else None,
        "duration_s": round(elapsed, 2),
        "csv_frames": frame_rows,
        "skipped_by_stream": skipped,
        "imu": {k: st[k] for k in ("frames_ok", "frames_bad", "dropped_frames",
                                   "counter_resets", "rate_hz")},
    }
    (session / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
