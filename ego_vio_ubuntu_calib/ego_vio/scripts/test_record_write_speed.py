#!/usr/bin/env python3
"""测试录制场景下的实际写入速度。

模拟 ego_vio / rdk_x5_capture 的录制方式:
  - 每帧 640x480 BGR 图像 → JPG (quality=90)
  - 每帧追加 IMU bin 记录
  - 写 CSV 时间戳
  - 目标目录可指定为 USB3.0 外接盘

用法:
  python scripts/test_record_write_speed.py --out E:\record_test --secs 30
"""
import argparse
import csv
import os
import struct
import sys
import threading
import time
from pathlib import Path
from queue import Queue

import cv2
import numpy as np


IMU_PACK_FMT = "<dI7f"
IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)


def make_fake_imu_sample(ts: float):
    """生成一帧假 IMU 数据。"""
    return struct.pack(
        IMU_PACK_FMT, ts, 0,
        0.1, 0.2, 0.3,    # gx gy gz
        -1.0, 0.01, 0.02, # ax ay az
        25.0,             # temp
    )


def test_write_speed(out_dir: Path, width: int, height: int, fps: int,
                     duration: float, jpg_quality: int, imu_hz: int):
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    cam_csv = open(out_dir / "camera_ts.csv", "w", newline="")
    cam_w = csv.writer(cam_csv)
    cam_w.writerow(["idx", "ts_mono", "ts_wall", "has_depth"])

    imu_bin = open(out_dir / "imu.bin", "wb")
    imu_csv = open(out_dir / "imu_ts.csv", "w", newline="")
    imu_w = csv.writer(imu_csv)
    imu_w.writerow(["counter", "ts_mono", "ts_wall"])

    q = Queue(maxsize=120)
    dropped = 0
    written_frames = 0
    written_imu = 0
    total_bytes = 0
    running = True
    done = threading.Event()

    def writer_loop():
        nonlocal written_frames, written_imu, total_bytes
        while True:
            item = q.get()
            if item is None:
                break
            kind = item[0]
            if kind == "img":
                _, idx, ts, img, ts_wall = item
                path = frames_dir / f"{idx:06d}.jpg"
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), jpg_quality])
                if ok:
                    buf.tofile(path)
                    total_bytes += len(buf)
                    written_frames += 1
                cam_w.writerow([idx, f"{ts:.9f}", f"{ts_wall:.6f}", 0])
            elif kind == "imu":
                _, ts = item
                imu_bin.write(make_fake_imu_sample(ts))
                written_imu += 1

        # 刷盘
        imu_bin.flush()
        cam_csv.flush()
        imu_csv.flush()
        done.set()

    writer = threading.Thread(target=writer_loop)
    writer.start()

    t_start = time.monotonic()
    t_end = t_start + duration
    next_img = t_start
    next_imu = t_start
    img_dt = 1.0 / fps
    imu_dt = 1.0 / imu_hz
    idx = 0

    last_report = t_start
    last_frames = 0
    last_bytes = 0

    print(f"开始写入测试: {out_dir}")
    print(f"  图像: {width}x{height} @ {fps}fps, JPG quality={jpg_quality}")
    print(f"  IMU: {imu_hz}Hz, 持续 {duration}s\n")

    while time.monotonic() < t_end:
        now = time.monotonic()

        # 生成 IMU
        while next_imu <= now and now < t_end:
            if q.qsize() >= q.maxsize:
                dropped += 1
            else:
                q.put(("imu", next_imu))
            next_imu += imu_dt

        # 生成图像
        if next_img <= now:
            img = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
            if q.qsize() >= q.maxsize:
                dropped += 1
            else:
                q.put(("img", idx, now, img, time.time()))
            idx += 1
            next_img += img_dt

        # 每秒报告
        if now - last_report >= 1.0:
            elapsed = now - last_report
            f_cnt = written_frames - last_frames
            mb = (total_bytes - last_bytes) / (1024 * 1024)
            print(f"[{now-t_start:5.1f}s] 写 {f_cnt} 帧 ({f_cnt/elapsed:.1f}fps) "
                  f"{mb:.1f}MB/s | 队列 {q.qsize()} | 累计丢 {dropped}")
            last_report = now
            last_frames = written_frames
            last_bytes = total_bytes

        time.sleep(0.001)

    running = False
    q.put(None)
    writer.join(timeout=5.0)
    cam_csv.close()
    imu_csv.close()
    imu_bin.close()

    # 最终结果
    elapsed = time.monotonic() - t_start
    print(f"\n测试结束: {elapsed:.1f}s")
    print(f"  图像帧: {written_frames} ({written_frames/elapsed:.1f}fps)")
    print(f"  IMU 帧: {written_imu} ({written_imu/elapsed:.1f}Hz)")
    print(f"  总写入: {total_bytes/(1024*1024):.1f}MB ({total_bytes/elapsed/(1024*1024):.1f}MB/s)")
    print(f"  丢队列: {dropped}")
    print(f"  平均单帧: {total_bytes/max(written_frames,1)/1024:.1f}KB")

    # 清理
    for f in out_dir.rglob("*"):
        if f.is_file():
            f.unlink()
    for d in sorted(out_dir.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        if d.is_dir():
            d.rmdir()
    out_dir.rmdir()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="recordings/write_speed_test",
                    help="测试输出目录, 建议指定为 USB3.0 外接盘路径")
    ap.add_argument("--secs", type=float, default=30, help="测试秒数")
    ap.add_argument("--fps", type=int, default=30, help="相机 fps")
    ap.add_argument("--imu_hz", type=int, default=400, help="IMU Hz")
    ap.add_argument("--width", type=int, default=640, help="图像宽")
    ap.add_argument("--height", type=int, default=480, help="图像高")
    ap.add_argument("--quality", type=int, default=90, help="JPG quality")
    args = ap.parse_args()

    test_write_speed(
        out_dir=Path(args.out),
        width=args.width,
        height=args.height,
        fps=args.fps,
        duration=args.secs,
        jpg_quality=args.quality,
        imu_hz=args.imu_hz,
    )


if __name__ == "__main__":
    main()
