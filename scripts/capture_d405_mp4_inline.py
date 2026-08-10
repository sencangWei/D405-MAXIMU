#!/usr/bin/env python3
"""D405 三路 720p@30 边录边转 MP4 (NVENC 硬件编码) + 外置 IMU。

直接产出 3 个 MP4 (不再需要 3.3GB 中间 bag):
  mp4/ir_left.mp4    HEVC cq18  左IR   (SLAM cam0, 接近无损)
  mp4/ir_right.mp4   HEVC cq18  右IR   (SLAM cam1)
  mp4/color.mp4      H264 cq24  彩色   (观看)
  mp4/timestamps.csv           每帧设备全局时间戳 (供 SLAM 时间对齐)

关键优化 (相对 test_nvenc_recording.py 的 1.92% 丢帧):
  - 彩色 YUYV 原始字节直接喂 ffmpeg (pix_fmt yuyv422 -> yuv420p), 不做 Python 转换
  - IR 原始 y8 直接喂 ffmpeg (gray -> yuv420p)
  这样主循环每个 frameset 只做 3 次 stdin.write, 丢帧应回到 USB 传输层水平 (0.3-1.3%)。

用法:
  python3 scripts/capture_d405_mp4_inline.py --duration 30
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
from queue import Queue

import pyrealsense2 as rs

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.imu_reader import ImuReader
from ego_vio.recorder.recorder import UnitRecorder

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30
GRAY_BYTES = W * H          # 921600
YUYV_BYTES = W * H * 2      # 1843200


def start_nvenc(path: Path, pix_fmt: str, codec: str, cq: int):
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-vf", "format=yuv420p",
        "-c:v", codec, "-preset", "p6", "-tune", "ll", "-rc", "vbr",
        "-cq", str(cq), "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-f", "mp4", str(path),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def start_ffv1(path: Path, pix_fmt: str):
    """无损 FFV1 (.mkv) 内联编码: 往返解码与原始 Y8 逐字节一致 (实测验证),
    VINS 输入与原始 db3 完全相同 -> 精度无损。720p30 实测 ~300fps, 实时无压力。"""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-c:v", "ffv1", "-level", "3", "-g", "1",
        "-f", "matroska", str(path),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def main() -> int:
    ap = argparse.ArgumentParser(description="D405 三路边录边转 MP4 (NVENC) + IMU")
    ap.add_argument("--serial", default=SERIAL)
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--warmup-frames", type=int, default=30)
    ap.add_argument("--imu-port",
                    default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00")
    ap.add_argument("--imu-baud", type=int, default=921600)
    ap.add_argument("--ir-cq", type=int, default=18)
    ap.add_argument("--ir-codec", choices=["hevc_nvenc", "ffv1"], default="ffv1",
                    help="IR 流编码: ffv1=无损 (精度第一, .mkv), hevc_nvenc=有损 cq18 (仅观赏)")
    ap.add_argument("--rgb-cq", type=int, default=24)
    ap.add_argument("--output-root", type=Path, default=ROOT / "recordings")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = args.output_root.resolve() / f"d405_mp4_inline_{stamp}"
    session.mkdir(parents=True, exist_ok=False)
    mp4_dir = session / "mp4"
    mp4_dir.mkdir()

    # IMU 录制 (独立线程, 400Hz)
    imu_recorder = UnitRecorder("external_imu", session, save_depth=False, max_queue=8000)
    imu = ImuReader(args.imu_port, baud=args.imu_baud, warmup_frames=500,
                    on_sample=imu_recorder.put_imu, name="inline_imu")

    # 相机
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(args.serial)
    config.enable_stream(rs.stream.color, W, H, rs.format.yuyv, FPS)
    config.enable_stream(rs.stream.infrared, 1, W, H, rs.format.y8, FPS)
    config.enable_stream(rs.stream.infrared, 2, W, H, rs.format.y8, FPS)

    def ir_encoder(key: str):
        if args.ir_codec == "ffv1":
            return start_ffv1(mp4_dir / f"{key}.mkv", "gray")
        return start_nvenc(mp4_dir / f"{key}.mp4", "gray", "hevc_nvenc", args.ir_cq)

    encoders = {
        "ir_left": (ir_encoder("ir_left"), 0),
        "ir_right": (ir_encoder("ir_right"), 0),
        "color": (start_nvenc(mp4_dir / "color.mp4", "yuyv422", "h264_nvenc", args.rgb_cq), 0),
    }

    csv_path = mp4_dir / "timestamps.csv"
    fields = ["stream", "frame_index", "frame_number", "device_ts_ms"]
    stats = {k: {"first": None, "last": None, "count": 0, "gaps": 0, "last_n": None}
             for k in encoders}
    frame_idx = {k: 0 for k in encoders}
    ts_rows = []  # 批量缓存, 结束时一次性写 CSV, 避免热循环里 csv 开销
    first_ts = None  # (ir_left_device_ms, color_device_ms, arrival_mono) 供 epoch 对齐

    start_mono = None
    writer_threads = {}
    queues = {}  # per-stream 帧队列 (写线程消费)

    # 每个编码器一个写线程: 主循环只入队, 写管道/编码完全脱离主循环,
    # 避免 ffmpeg 管道反压阻塞采集 -> 消除 Python 侧额外丢帧
    def writer_loop(key, proc):
        q = queues[key]
        while True:
            frame = q.get()
            if frame is None:
                break
            try:
                proc.stdin.write(frame.get_data())
            except (BrokenPipeError, ValueError):
                break

    for key, (proc, _) in encoders.items():
        queues[key] = Queue(maxsize=60)  # 2s 缓冲, 平滑突发
        t = threading.Thread(target=writer_loop, args=(key, proc), daemon=True)
        t.start()
        writer_threads[key] = t

    try:
        profile = pipeline.start(config)
        imu_recorder.start()
        if not imu.start():
            raise RuntimeError(f"无法打开IMU串口: {args.imu_port}")
        print(f"[inline] 输出: {session}")
        print(f"[inline] 三路 1280x720@30 -> NVENC MP4 + IMU 400Hz, 录制 {args.duration:.0f}s")

        for _ in range(max(0, args.warmup_frames)):
            pipeline.wait_for_frames(5000)

        while True:
            now = time.monotonic()
            if start_mono is None:
                start_mono = now
            if args.duration > 0 and now - start_mono >= args.duration:
                break

            frames = pipeline.wait_for_frames(5000)
            i1 = frames.get_infrared_frame(1)
            i2 = frames.get_infrared_frame(2)
            c = frames.get_color_frame()
            if not (i1 and i2 and c):
                continue

            # 首帧记录设备全局时间 + 到达 monotonic, 供回放 epoch 对齐 (d405_frames.csv)
            if first_ts is None and i1.get_frame_timestamp_domain() == rs.timestamp_domain.global_time:
                first_ts = (float(i1.get_timestamp()), float(c.get_timestamp()), now)

            # 主循环: 只入队 + 轻量统计, 不写管道
            for key, frame in (("ir_left", i1), ("ir_right", i2), ("color", c)):
                s = stats[key]
                n = frame.get_frame_number()
                if s["first"] is None:
                    s["first"] = n
                s["last"] = n
                s["count"] += 1
                if s["last_n"] is not None and n > s["last_n"] + 1:
                    s["gaps"] += n - s["last_n"] - 1
                s["last_n"] = n
                ts_ms = float(frame.get_timestamp())
                if frame.get_frame_timestamp_domain() != rs.timestamp_domain.global_time:
                    ts_ms = float("nan")
                ts_rows.append([key, frame_idx[key], n, f"{ts_ms:.3f}"])
                frame_idx[key] += 1
                queues[key].put(frame)  # 帧引用保持数据存活, 写线程取走写入
    except KeyboardInterrupt:
        pass
    finally:
        imu.stop()
        imu_recorder.stop()
        # 等写线程消费完再关管道
        for key in encoders:
            try:
                queues[key].put(None)
            except Exception:
                pass
        for t in writer_threads.values():
            t.join(timeout=5)
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(fields)
            w.writerows(ts_rows)

        # d405_frames.csv: 供 replay_mp4_to_ros2.py 的 load_epoch_minus_mono / compute_auto_align
        if first_ts:
            ir_ms, color_ms, arrival_mono = first_ts
            with (session / "d405_frames.csv").open("w", newline="") as f:
                wr = csv.DictWriter(f, fieldnames=[
                    "color_device_ms", "color_mono", "infrared_left_device_ms"])
                wr.writeheader()
                wr.writerow({"color_device_ms": f"{color_ms:.3f}",
                             "color_mono": f"{arrival_mono:.6f}",
                             "infrared_left_device_ms": f"{ir_ms:.3f}"})
        for key, (proc, _) in encoders.items():
            try:
                proc.stdin.close()
                proc.wait(timeout=15)
            except Exception:
                pass
        try:
            pipeline.stop()
        except Exception:
            pass

    elapsed = time.monotonic() - start_mono if start_mono else 0
    total_frames = sum(s["count"] for s in stats.values())
    def out_path(key: str) -> Path:
        return mp4_dir / (f"{key}.mkv" if args.ir_codec == "ffv1" and key != "color" else f"{key}.mp4")
    total_mb = sum(out_path(k).stat().st_size for k in encoders) / 1e6
    print(f"\n===== 内联 {args.ir_codec} 采集 ({elapsed:.1f}s) =====")
    for key, name in (("ir_left", "IR1"), ("ir_right", "IR2"), ("color", "Color")):
        s = stats[key]
        span = s["last"] - s["first"]
        drop = s["gaps"] / span * 100 if span > 0 else 0
        sz = out_path(key).stat().st_size / 1e6
        print(f"  {name}: 收到{s['count']} 跨度{span} 丢{s['gaps']}({drop:.2f}%) "
              f"-> {out_path(key).name} {sz:.1f}MB")
    print(f"  输出总 {total_mb:.1f}MB / {elapsed:.0f}s = {total_mb/elapsed:.2f}MB/s "
          f"(原始三路约 110MB/s)")
    report = {
        "session": str(session),
        "duration_s": elapsed,
        "ir_codec": args.ir_codec,
        "frames_by_stream": {k: v["count"] for k, v in stats.items()},
        "gaps_by_stream": {k: v["gaps"] for k, v in stats.items()},
        "out_mb": {k: round(out_path(k).stat().st_size / 1e6, 1) for k in encoders},
        "imu": imu.stats(),
    }
    (session / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
