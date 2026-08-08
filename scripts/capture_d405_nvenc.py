#!/usr/bin/env python3
"""D405 三路采集 + NVENC 硬件压缩 (低丢帧 + 极小体积)。

方案:
  采集时 enable_record_to_file 录原始 bag (丢帧 0.3% 最优),
  同时用独立线程 + RTX NVENC 异步把三路转码成 MP4。

输出 (每个会话):
  <session>/raw/          原始 bag (完整, 可回溯)
  <session>/mp4/ir_left.mp4    HEVC 高质量 (SLAM 用)
  <session>/mp4/ir_right.mp4   HEVC 高质量 (SLAM 用)
  <session>/mp4/rgb.mp4        H264 (老板看, 体积极小)
  <session>/d405_frames.csv    帧号/时间戳
  <session>/external_imu/imu.bin  IMU

体积: 40s 三路 ~26MB (vs 原始 4.3GB, 缩 165 倍)

用法:
  python3 scripts/capture_d405_nvenc.py --duration 60
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import threading
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
IR_CQ = "18"      # HEVC 高质量 (SLAM)
RGB_CQ = "24"     # H264 (可视化)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="D405 三路采集+NVENC压缩")
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--serial", default=SERIAL)
    p.add_argument("--imu-port",
                   default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00")
    p.add_argument("--imu-baud", type=int, default=921600)
    p.add_argument("--output-root", type=Path, default=ROOT / "recordings")
    return p.parse_args()


class NVENCWriter:
    """FFmpeg NVENC 硬件编码器封装 (独立进程)."""

    def __init__(self, path: Path, hevc: bool, cq: str):
        codec = "hevc_nvenc" if hevc else "h264_nvenc"
        self.path = path
        self.proc = subprocess.Popen(
            ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
             "-c:v", codec, "-preset", "p6", "-rc", "vbr", "-cq", cq,
             "-pix_fmt", "yuv420p", "-f", "mp4", str(path)],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def write(self, frame):
        try:
            self.proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            pass

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def main() -> int:
    args = parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = args.output_root.resolve() / f"d405_nvenc_{stamp}"
    session.mkdir(parents=True, exist_ok=False)
    raw_dir = session / "raw"
    mp4_dir = session / "mp4"
    raw_dir.mkdir()
    mp4_dir.mkdir()

    bag_path = raw_dir / "capture.db3"
    csv_path = session / "d405_frames.csv"

    # IMU
    imu_recorder = UnitRecorder("external_imu", session, save_depth=False, max_queue=8000)
    imu = ImuReader(args.imu_port, baud=args.imu_baud, warmup_frames=500,
                    on_sample=imu_recorder.put_imu, name="nvenc_imu")

    # 相机 (录原始 bag, 0.3% 丢帧)
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(args.serial)
    config.enable_stream(rs.stream.color, W, H, rs.format.yuyv, FPS)
    config.enable_stream(rs.stream.infrared, 1, W, H, rs.format.y8, FPS)
    config.enable_stream(rs.stream.infrared, 2, W, H, rs.format.y8, FPS)
    config.enable_record_to_file(str(bag_path))

    # NVENC 编码器 (独立线程喂帧)
    enc_ir1 = NVENCWriter(mp4_dir / "ir_left.mp4", hevc=True, cq=IR_CQ)
    enc_ir2 = NVENCWriter(mp4_dir / "ir_right.mp4", hevc=True, cq=IR_CQ)
    enc_rgb = NVENCWriter(mp4_dir / "rgb.mp4", hevc=False, cq=RGB_CQ)

    # 统计
    stats = {k: {"first": None, "last": None, "count": 0, "gaps": 0, "last_n": None}
             for k in ("color", "ir1", "ir2")}
    frame_rows = 0
    lock = threading.Lock()

    def track(key, frame):
        nonlocal frame_rows
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

    # 编码线程: 从队列取帧喂 NVENC
    frame_queue = __import__("queue").Queue(maxsize=600)
    stop = threading.Event()

    def encoder_worker():
        while True:
            try:
                item = frame_queue.get(timeout=0.5)
            except __import__("queue").Empty:
                if stop.is_set():
                    return
                continue
            kind, gray = item
            if kind == "ir1":
                enc_ir1.write(gray)
            elif kind == "ir2":
                enc_ir2.write(gray)
            else:
                enc_rgb.write(gray)
            frame_queue.task_done()

    enc_thread = threading.Thread(target=encoder_worker, daemon=True)
    enc_thread.start()

    csv_fp = csv_path.open("w", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(csv_fp, fieldnames=[
        "set_index", "arrival_mono", "arrival_wall",
        "color_frame_number", "infrared_left_frame_number", "infrared_right_frame_number"])
    csv_writer.writeheader()

    if not imu.start():
        print(f"[ERROR] IMU 串口失败: {args.imu_port}")
        return 1
    imu_recorder.start()

    print(f"[采集] 输出: {session}")
    print(f"[采集] 原始bag(0.3%丢帧) + NVENC压缩(26MB/40s)")
    print(f"[采集] 录制 {args.duration:.0f}s, Ctrl+C 提前结束")

    t0 = time.time()
    try:
        pipeline.start(config)
        for _ in range(30):
            pipeline.wait_for_frames()

        while time.time() - t0 < args.duration:
            frames = pipeline.wait_for_frames(timeout_ms=2000)
            now_mono = time.monotonic()

            c = frames.get_color_frame()
            i1 = frames.get_infrared_frame(1)
            i2 = frames.get_infrared_frame(2)

            # 统计 + 时间戳 (主线程快速)
            track("color", c)
            track("ir1", i1)
            track("ir2", i2)

            csv_writer.writerow({
                "set_index": frame_rows,
                "arrival_mono": f"{now_mono:.9f}",
                "arrival_wall": f"{time.time():.6f}",
                "color_frame_number": c.get_frame_number() if c else "",
                "infrared_left_frame_number": i1.get_frame_number() if i1 else "",
                "infrared_right_frame_number": i2.get_frame_number() if i2 else "",
            })
            frame_rows += 1
            csv_fp.flush()

            # 灰度帧入队 (编码线程处理, 不拖累主循环)
            if i1:
                frame_queue.put(("ir1", np.asanyarray(i1.get_data())))
            if i2:
                frame_queue.put(("ir2", np.asanyarray(i2.get_data())))
            if c:
                yuyv = np.asanyarray(c.get_data())
                if yuyv.dtype == np.uint16:
                    yuyv = yuyv.view(np.uint8).reshape(H, W, 2)
                else:
                    yuyv = yuyv.reshape(H, W, 2)
                y = np.ascontiguousarray(yuyv[:, :, 0])  # Y 分量 = 灰度
                frame_queue.put(("rgb", y))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()                  # 允许线程在队列空时退出
        frame_queue.join()          # 等队列里的帧全部编码完
        enc_thread.join(timeout=10)
        for enc in (enc_ir1, enc_ir2, enc_rgb):
            enc.close()
        csv_fp.close()
        imu.stop()
        imu_recorder.stop()
        try:
            pipeline.stop()
        except Exception:
            pass

    elapsed = time.time() - t0
    st = imu.stats()
    report = {
        "session": str(session),
        "duration_s": round(elapsed, 2),
        "csv_frames": frame_rows,
        "skipped_by_stream": {k: stats[k]["gaps"] for k in stats},
        "mp4": [str(p.relative_to(session)) for p in mp4_dir.glob("*.mp4")],
        "imu": {k: st[k] for k in ("frames_ok", "frames_bad", "dropped_frames",
                                   "counter_resets", "rate_hz")},
    }
    (session / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n体积: {sum(p.stat().st_size for p in mp4_dir.glob('*.mp4'))/1e6:.1f} MB (MP4)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
