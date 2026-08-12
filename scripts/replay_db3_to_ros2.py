#!/usr/bin/env python3
"""回放采集会话到 ROS2 topic，供后端 SLAM 离线跑(流式, 不预载)。

数据源:
  - 会话里的 .db3 (RealSense rosbag2 sqlite, 图像 + 设备时间戳 metadata)
  - external_imu/imu.bin (KT-EX9, ts 为 monotonic 拟合值)

发布:
  /cam0/image_raw  左IR (mono8, stereo/rgbd) 或 RGB灰度 (mono8, color_ir)
  /cam1/image_raw  右IR (mono8, stereo) / Depth 16UC1 (rgbd) / 左IR (mono8, color_ir)
  /imu0            sensor_msgs/Imu (rad/s, m/s^2)

时间线: 图像用设备 global_time; IMU = monotonic + epoch 偏移
(d405_frames.csv 推出) + --imu-shift-ms (Kalibr 时间偏移补偿)。

用法:
  python3 scripts/replay_db3_to_ros2.py --session recordings/xxx --mode stereo
"""

from __future__ import annotations

import argparse
import csv
import re
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

G0 = 9.80665
DEG2RAD = 3.141592653589793 / 180.0
IMU_PACK_FMT = "<dI7f"
IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)

TOPIC_PREFIX = "/device_0/sensor_0/"
STREAM_TOPICS = {
    "ir_left": TOPIC_PREFIX + "Infrared_1/image/data",
    "ir_left_meta": TOPIC_PREFIX + "Infrared_1/image/metadata",
    "ir_right": TOPIC_PREFIX + "Infrared_2/image/data",
    "ir_right_meta": TOPIC_PREFIX + "Infrared_2/image/metadata",
    "depth": TOPIC_PREFIX + "Depth_0/image/data",
    "depth_meta": TOPIC_PREFIX + "Depth_0/image/metadata",
    "color": TOPIC_PREFIX + "Color_0/image/data",
    "color_meta": TOPIC_PREFIX + "Color_0/image/metadata",
}

# 每个模式的 cam0/cam1 流分配
MODE_STREAMS = {
    "stereo": ("ir_left", "ir_right"),
    "rgbd": ("ir_left", "depth"),
    "color_ir": ("color", "ir_left"),
}

META_TS_RE = re.compile(r"timestamp=([0-9.]+)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="回放 db3+imu.bin 到 ROS2 topic(流式)")
    p.add_argument("--session", type=Path, required=True, help="采集会话目录")
    p.add_argument("--mode", choices=sorted(MODE_STREAMS), required=True)
    p.add_argument("--rate", type=float, default=1.0, help="回放倍速(1=实时)")
    p.add_argument("--imu-shift-ms", type=float, default=0.0,
                   help="IMU 发布戳平移(Kalibr t_imu=t_cam-7.36ms -> +7.36ms)")
    p.add_argument("--imu-align-s", type=float, default=0.0,
                   help="额外 IMU 平移(s): 补偿 warmup 帧导致图像/IMU 起始错位")
    p.add_argument("--skip-s", type=float, default=0.0, help="跳过开头秒数")
    p.add_argument("--duration-s", type=float, default=0.0,
                   help="仅回放指定秒数；0 表示回放到会话末尾")
    return p.parse_args()


def load_epoch_minus_mono(session: Path) -> float:
    rows = list(csv.DictReader((session / "d405_frames.csv").open()))
    offsets = [
        float(r["color_device_ms"]) / 1000.0 - float(r["color_mono"])
        for r in rows
        if r.get("color_device_ms") and r.get("color_mono")
    ]
    if not offsets:
        raise RuntimeError("d405_frames.csv 缺少 color_device_ms/color_mono")
    offsets.sort()
    return offsets[len(offsets) // 2]


def compute_auto_align(session: Path) -> float:
    """自动计算 IMU 平移量(s), 补偿 bag 里 warmup 帧与正式采集的错位。

    原理: d405_frames.csv 第一行是正式采集首帧 (IR_left_device_ms = epoch 时间),
    而 bag 里图像从 warmup 开始 (metadata 时间戳更早)。
    imu.bin 的 mono 时间 + epoch_minus_mono = IMU epoch。
    要让 IMU 对齐到正式采集首帧, IMU 需平移:
        align = 正式采集IR首帧epoch - (imu.bin首帧mono + epoch_minus_mono)
    返回 0 表示无法计算。
    """
    try:
        rows = list(csv.DictReader((session / "d405_frames.csv").open()))
        r0 = next((r for r in rows if r.get("infrared_left_device_ms")), None)
        if r0 is None:
            return 0.0
        formal_ir_epoch = float(r0["infrared_left_device_ms"]) / 1000.0
    except (OSError, ValueError, StopIteration):
        return 0.0

    eom = load_epoch_minus_mono(session)
    try:
        with (session / "external_imu" / "imu.bin").open("rb") as f:
            chunk = f.read(IMU_PACK_SIZE)
            if len(chunk) < IMU_PACK_SIZE:
                return 0.0
            imu_first_mono = struct.unpack(IMU_PACK_FMT, chunk)[0]
    except OSError:
        return 0.0

    align = formal_ir_epoch - (imu_first_mono + eom)
    return align


def bag_event_iter(db3: Path, mode: str):
    """流式产出 (t_epoch_s, stream_key, Image msg)。metadata 在 data 附近, 顺序配对。"""
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Image as RosImage
    from std_msgs.msg import String

    s0, s1 = MODE_STREAMS[mode]
    wanted = {s0, s0 + "_meta", s1, s1 + "_meta"}
    name_of = {v: k for k, v in STREAM_TOPICS.items() if k in wanted}

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("", ""),
    )

    pending_data: dict[str, RosImage] = {}
    pending_ts: dict[str, float] = {}
    while reader.has_next():
        topic, data, _bag_t = reader.read_next()
        key = name_of.get(topic)
        if key is None:
            continue
        if key.endswith("_meta"):
            m = META_TS_RE.search(deserialize_message(data, String).data)
            if m:
                base = key[:-5]
                pending_ts[base] = float(m.group(1)) / 1000.0
                if base in pending_data:
                    yield pending_ts.pop(base), base, pending_data.pop(base)
        else:
            pending_data[key] = deserialize_message(data, RosImage)
            if key in pending_ts:
                yield pending_ts.pop(key), key, pending_data.pop(key)


def imu_event_iter(session: Path, epoch_minus_mono: float, shift_ms: float):
    """流式产出 (t_pub_epoch_s, gx,gy,gz,ax,ay,az) (SI 单位)。"""
    with (session / "external_imu" / "imu.bin").open("rb") as f:
        while True:
            chunk = f.read(IMU_PACK_SIZE)
            if len(chunk) < IMU_PACK_SIZE:
                break
            ts, _cnt, gx, gy, gz, ax, ay, az, _temp = struct.unpack(IMU_PACK_FMT, chunk)
            yield (ts + epoch_minus_mono + shift_ms / 1000.0,
                   gx * DEG2RAD, gy * DEG2RAD, gz * DEG2RAD,
                   ax * G0, ay * G0, az * G0)


def main() -> int:
    args = parse_args()
    session = args.session.resolve()
    db3s = list(session.glob("*.db3"))
    if not db3s:
        print(f"[ERROR] 会话里没有 db3: {session}")
        return 1

    epoch_minus_mono = load_epoch_minus_mono(session)
    print(f"[replay] epoch-monotonic 偏移: {epoch_minus_mono:.6f}s", flush=True)

    # IMU 平移: 若未显式指定, 自动对齐 warmup
    align_s = args.imu_align_s
    if str(args.imu_align_s).lower() == "auto":
        align_s = compute_auto_align(session)
        if align_s != 0.0:
            print(f"[replay] 自动对齐: IMU 平移 {align_s:+.3f}s (补偿 warmup)", flush=True)
    else:
        align_s = float(args.imu_align_s)

    img_it = iter(bag_event_iter(db3s[0], args.mode))
    imu_it = iter(imu_event_iter(session, epoch_minus_mono,
                                 args.imu_shift_ms + align_s * 1000.0))

    img_ev = next(img_it, None)
    imu_ev = next(imu_it, None)
    if img_ev is None or imu_ev is None:
        print("[ERROR] 图像或 IMU 事件为空")
        return 1

    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Image as RosImage, Imu as RosImu
    from builtin_interfaces.msg import Time as RosTime

    rclpy.init()
    node = Node("db3_replay")
    pub_cam0 = node.create_publisher(RosImage, "/cam0/image_raw", 10)
    pub_cam1 = node.create_publisher(RosImage, "/cam1/image_raw", 10)
    pub_imu = node.create_publisher(RosImu, "/imu0", 2000)

    # 新建 publisher 后立即发首帧会让 DDS 发现时序决定开头丢多少数据，进而改变
    # VINS 的初始化窗口。回放最多等 5 秒让三个输入都匹配；无人订阅时仍继续，
    # 保留脚本单独检查数据的用法。
    discovery_deadline = time.monotonic() + 5.0
    while time.monotonic() < discovery_deadline:
        counts = (pub_cam0.get_subscription_count(),
                  pub_cam1.get_subscription_count(),
                  pub_imu.get_subscription_count())
        if min(counts) > 0:
            break
        rclpy.spin_once(node, timeout_sec=0.05)
    counts = (pub_cam0.get_subscription_count(),
              pub_cam1.get_subscription_count(),
              pub_imu.get_subscription_count())
    if min(counts) > 0:
        print(f"[replay] DDS 已匹配: cam0={counts[0]} cam1={counts[1]} imu={counts[2]}",
              flush=True)
    else:
        print(f"[replay] WARN: DDS 订阅未全部匹配: cam0={counts[0]} "
              f"cam1={counts[1]} imu={counts[2]}", flush=True)

    def to_stamp(t_epoch: float) -> RosTime:
        sec = int(t_epoch)
        return RosTime(sec=sec, nanosec=int((t_epoch - sec) * 1e9))

    n_img = n_imu = 0
    t0_data = None
    t0_wall = None
    t_skip_end = None

    # 该模式下哪个流进 cam0 / cam1
    stream_to_cam = dict(zip(MODE_STREAMS[args.mode], ("cam0", "cam1")))

    def publish_img(t, key, msg):
        nonlocal n_img
        msg.header.stamp = to_stamp(t)
        cam = stream_to_cam.get(key, "cam1")
        msg.header.frame_id = cam
        if key == "depth":
            msg.encoding = "16UC1"  # 袋里是 mono16
        elif key == "color":
            # 袋里是 YUYV (2B/px): 取 Y 通道 -> mono8, 供特征跟踪
            msg.data = msg.data[::2]
            msg.step = msg.width
            msg.encoding = "mono8"
            msg.is_bigendian = 0
        else:
            msg.encoding = "mono8"  # 袋里是 8UC1 (ir), 统一 mono8
        (pub_cam0 if cam == "cam0" else pub_cam1).publish(msg)
        n_img += 1

    def publish_imu(ev):
        nonlocal n_imu
        t, gx, gy, gz, ax, ay, az = ev
        # IMU 坐标系变换: 重力 y -> z 向下
        # R = [0.99980212, -0.01423891, -0.01389161;
        #      -0.01423891, -0.02458715, -0.99959628;
        #       0.01389161,  0.99959628, -0.02478503]
        gx_n = 0.99980212*gx - 0.01423891*gy - 0.01389161*gz
        gy_n = -0.01423891*gx - 0.02458715*gy - 0.99959628*gz
        gz_n = 0.01389161*gx + 0.99959628*gy - 0.02478503*gz
        ax_n = 0.99980212*ax - 0.01423891*ay - 0.01389161*az
        ay_n = -0.01423891*ax - 0.02458715*ay - 0.99959628*az
        az_n = 0.01389161*ax + 0.99959628*ay - 0.02478503*az
        im = RosImu()
        im.header.stamp = to_stamp(t)
        im.header.frame_id = "imu0"
        im.angular_velocity.x = gx_n
        im.angular_velocity.y = gy_n
        im.angular_velocity.z = gz_n
        im.linear_acceleration.x = ax_n
        im.linear_acceleration.y = ay_n
        im.linear_acceleration.z = az_n
        pub_imu.publish(im)
        n_imu += 1

    print("[replay] 开始回放...", flush=True)
    try:
        while img_ev is not None or imu_ev is not None:
            if imu_ev is None or (img_ev is not None and img_ev[0] <= imu_ev[0]):
                t = img_ev[0]
                ev = img_ev
                img_ev = next(img_it, None)
                kind = "img"
            else:
                t = imu_ev[0]
                ev = imu_ev
                imu_ev = next(imu_it, None)
                kind = "imu"

            if t0_data is None:
                t0_data = t
                t_skip_end = t + args.skip_s
            if t < t_skip_end:
                continue
            if t0_wall is None:
                t0_wall = time.monotonic()
                t0_data = t
            if args.duration_s > 0 and t - t0_data >= args.duration_s:
                break

            target = t0_wall + (t - t0_data) / max(args.rate, 1e-6)
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, 0.5))

            if kind == "img":
                publish_img(ev[0], ev[1], ev[2])
            else:
                publish_imu(ev)
                if n_imu % 4000 == 0:
                    print(f"[replay] t={t - t0_data:7.1f}s img={n_img} imu={n_imu}", flush=True)
    except KeyboardInterrupt:
        print("\n[replay] 中断")
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    print(f"[replay] 完成: img={n_img} imu={n_imu}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
