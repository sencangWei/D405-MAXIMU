#!/usr/bin/env python3
"""后处理: 采集会话 db3 → 三路 MP4 (NVENC 硬件编码) + 时间戳 sidecar。

架构背景 (丢帧最优 + 体积最小):
  采集: 原始三路写 bag (enable_record_to_file), 不做内联编码 -> 丢帧最低 (0.3-1.3%)
  本脚本: 离线把 bag 转 MP4, 对采集无影响 -> NVENC 353fps, 体积 ~165x 缩小
  IR 用 HEVC 高质量 (SLAM 喂流, 接近无损), RGB 用 H.264 (给人看, 极低码率)

输出 (session/mp4/):
  ir_left.mp4   HEVC  yuv420p  左IR   (SLAM cam0)
  ir_right.mp4  HEVC  yuv420p  右IR   (SLAM cam1)
  rgb.mp4       H264  yuv420p  彩色   (观看)
  timestamps.csv  每流每帧: frame_index, frame_number, device_ts_ms (全局时间)

用法:
  source /opt/ros/humble/setup.bash
  python3 scripts/bag_to_mp4_nvenc.py --session recordings/d405_720p_rgb_stereo_ir_xxx
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

W, H, FPS = 1280, 720, 30

TOPIC_PREFIX = "/device_0/sensor_0/"
STREAM_TOPICS = {
    "ir_left":  TOPIC_PREFIX + "Infrared_1/image/data",
    "ir_right": TOPIC_PREFIX + "Infrared_2/image/data",
    "color":    TOPIC_PREFIX + "Color_0/image/data",
}
META_TOPICS = {k: t.replace("/data", "/metadata") for k, t in STREAM_TOPICS.items()}
META_TS_RE = re.compile(r"timestamp=([0-9.]+)")
META_FN_RE = re.compile(r"Frame number=(\d+)")


def start_nvenc(path: Path, pix_fmt_in: str, codec: str, cq: int):
    """启动一个 NVENC 编码子进程, 返回 (proc, stdin). 输入 rawvideo 写 stdin."""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", pix_fmt_in, "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-vf", "format=yuv420p",
        "-c:v", codec, "-preset", "p6", "-tune", "ll", "-rc", "vbr",
        "-cq", str(cq), "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-f", "mp4", str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    return proc


def feed_loop(reader, key, want_enc):
    """从 bag 流式产出该流所有 (data_bytes, meta_fields) 对."""
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Image as RosImage
    from std_msgs.msg import String

    data_topic = STREAM_TOPICS[key]
    meta_topic = META_TOPICS[key]
    pending_data = None
    n = 0
    # 顺序读取, 数据/元数据相邻
    while reader.has_next():
        topic, data, _bt = reader.read_next()
        if topic == meta_topic:
            meta_str = deserialize_message(data, String).data
            m = META_TS_RE.search(meta_str)
            ts_ms = float(m.group(1)) if m else float("nan")
            fm = META_FN_RE.search(meta_str)
            fn = int(fm.group(1)) if fm else -1
            if pending_data is not None:
                yield pending_data, ts_ms, fn
                pending_data = None
                n += 1
                if n >= want_enc:
                    return
        elif topic == data_topic:
            pending_data = deserialize_message(data, RosImage)


def main() -> int:
    ap = argparse.ArgumentParser(description="bag → 三路 MP4 (NVENC) + 时间戳 sidecar")
    ap.add_argument("--session", type=Path, required=True)
    ap.add_argument("--ir-cq", type=int, default=18, help="IR HEVC 质量 (越小越好, 18≈无损)")
    ap.add_argument("--rgb-cq", type=int, default=24, help="RGB H264 质量")
    ap.add_argument("--no-rgb", action="store_true", help="跳过 RGB (只要双 IR 给 SLAM)")
    args = ap.parse_args()

    session = args.session.resolve()
    db3s = list(session.glob("*.db3"))
    if not db3s:
        print(f"[ERROR] 会话里没有 db3: {session}")
        return 1
    db3 = db3s[0]

    import rosbag2_py
    mp4_dir = session / "mp4"
    mp4_dir.mkdir(parents=True, exist_ok=True)
    sidecar = mp4_dir / "timestamps.csv"
    encoders = {}
    streams = ["ir_left", "ir_right"] + ([] if args.no_rgb else ["color"])

    # 色流编码: 从 bag 图像消息读出编码, 决定 rawvideo pix_fmt
    color_pix_fmt = "yuyv422"

    # 每个流单独开一个 reader (sqlite 顺序读, 各自独立游标)
    readers = {}
    for key in streams:
        r = rosbag2_py.SequentialReader()
        r.open(rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
               rosbag2_py.ConverterOptions("", ""))
        readers[key] = r

    # 先跑一遍 color 编码探测 (取第一帧 Image 消息的 encoding)
    if "color" in streams:
        probe = rosbag2_py.SequentialReader()
        probe.open(rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
                   rosbag2_py.ConverterOptions("", ""))
        from rclpy.serialization import deserialize_message
        from sensor_msgs.msg import Image as RosImage
        while probe.has_next():
            topic, data, _ = probe.read_next()
            if topic == STREAM_TOPICS["color"]:
                enc = deserialize_message(data, RosImage).encoding
                enc = enc.lower()
                print(f"[convert] Color 袋内编码: {enc!r}")
                if enc in ("yuyv", "yuy2", "yuv2"):
                    color_pix_fmt = "yuyv422"
                elif enc in ("bgr8", "bgra"):
                    color_pix_fmt = "bgr24"
                elif enc in ("rgb8", "rgba"):
                    color_pix_fmt = "rgb24"
                else:
                    print(f"[WARN] 未知色流编码 {enc}, 按 yuyv422 处理")
                break
        del probe

    # 启动编码器
    for key in streams:
        is_ir = key.startswith("ir_")
        pix = "gray" if is_ir else color_pix_fmt
        codec = "hevc_nvenc" if is_ir else "h264_nvenc"
        cq = args.ir_cq if is_ir else args.rgb_cq
        path = mp4_dir / (key + ".mp4")
        proc = start_nvenc(path, pix, codec, cq)
        encoders[key] = (proc, path)
        print(f"[convert] {key:9s} -> {path.name} ({codec}, cq{cq})")

    csv_path = sidecar
    t0 = time.time()
    total_frames = {k: 0 for k in streams}
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stream", "frame_index", "frame_number", "device_ts_ms"])
        for key in streams:
            proc, path = encoders[key]
            reader = readers[key]
            for data_bytes, ts_ms, fn in feed_loop(reader, key, 1 << 30):
                if data_bytes.step != 0 and data_bytes.width == W and data_bytes.height == H:
                    n_bytes = W * H * (2 if key == "color" and color_pix_fmt == "yuyv422" else
                                       3 if key == "color" else 1)
                    raw = bytes(data_bytes.data)[:n_bytes]
                else:
                    raw = bytes(data_bytes.data)
                proc.stdin.write(raw)
                w.writerow([key, total_frames[key], fn, f"{ts_ms:.3f}"])
                total_frames[key] += 1
            proc.stdin.close()
            proc.wait(timeout=30)
            print(f"[convert] {key}: {total_frames[key]} 帧 -> {path} ({path.stat().st_size/1e6:.1f}MB)")

    elapsed = time.time() - t0
    total_mb = sum(encoders[k][1].stat().st_size for k in streams) / 1e6
    raw_mb = db3.stat().st_size / 1e6
    print(f"\n[convert] 完成: {sum(total_frames.values())} 帧, 用时 {elapsed:.1f}s "
          f"({sum(total_frames.values())/elapsed:.0f} fps 编码)")
    print(f"[convert] MP4 总 {total_mb:.1f}MB vs bag {raw_mb:.1f}MB = "
          f"{raw_mb/max(total_mb,1e-6):.0f}x 缩小")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
