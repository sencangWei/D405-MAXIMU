#!/usr/bin/env python3
"""回放 collect_calib_data 录制的标定会话(帧+IMU)到 ROS2 topic, 供 VINS 跑。

数据源:
  left_hand/frames/*.jpg      左IR (cam0)
  left_hand/frames_right/*.jpg 右IR (cam1)
  left_hand/camera_ts.csv     (idx, frame_number, ts_mono, ...)
  left_hand/imu.bin + imu_ts.csv

用法:
  source /opt/ros/humble/setup.bash
  python3 scripts/replay_calib_to_ros2.py --session recordings/calib_xxx --rate 1.0
"""
from __future__ import annotations

import argparse
import csv
import struct
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IMU_PACK_FMT = "<dI7f"
IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)
G0 = 9.80665
DEG2RAD = 3.141592653589793 / 180.0


def load_imu(session: Path, use_rx_mono: bool = False):
    """返回 [(ts, gx,gy,gz,ax,ay,az), ...] (度->弧度, g->m/s2).

    ts 基准: imu.bin 里的 fitted counter ts, 或用 imu_ts.csv 的 rx_mono
    (与相机 camera_ts.csv 的 ts_mono 同一 monotonic 时钟).
    """
    out = []
    # 读 imu_ts.csv 拿 rx_mono (若需要)
    rx_map = {}
    if use_rx_mono:
        with (session / "imu_ts.csv").open(newline="") as f:
            for r in csv.DictReader(f):
                try:
                    rx_map[int(r["counter"])] = float(r["rx_mono"])
                except (KeyError, ValueError):
                    pass
    with (session / "imu.bin").open("rb") as f:
        while True:
            c = f.read(IMU_PACK_SIZE)
            if len(c) < IMU_PACK_SIZE:
                break
            ts, cnt, gx, gy, gz, ax, ay, az, _t = struct.unpack(IMU_PACK_FMT, c)
            if use_rx_mono and cnt in rx_map:
                ts = rx_map[cnt]
            out.append((ts, gx * DEG2RAD, gy * DEG2RAD, gz * DEG2RAD,
                        ax * G0, ay * G0, az * G0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--imu-shift-ms", type=float, default=0.0,
                    help="IMU 发布戳平移(ms), 补偿相机-IMU 时间偏移 (Kalibr timeshift)")
    args = ap.parse_args()
    sess = Path(args.session).resolve()
    unit = sess / "left_hand"

    # 图像时间戳
    cam_rows = []
    with (unit / "camera_ts.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            cam_rows.append((int(r["idx"]), float(r["ts_mono"])))
    # 用 rx_mono (与相机同一 monotonic 时钟), 再按 Kalibr timeshift 平移
    imu_rows = load_imu(unit, use_rx_mono=True)
    imu_rows = [(t + args.imu_shift_ms / 1000.0, *rest) for t, *rest in imu_rows]

    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Image as RosImage, Imu as RosImu
    from builtin_interfaces.msg import Time as RosTime

    rclpy.init()
    node = Node("calib_replay")
    pub0 = node.create_publisher(RosImage, "/cam0/image_raw", 10)
    pub1 = node.create_publisher(RosImage, "/cam1/image_raw", 10)
    pubi = node.create_publisher(RosImu, "/imu0", 2000)

    def to_stamp(t):
        return RosTime(sec=int(t), nanosec=int((t - int(t)) * 1e9))

    def pub_img(t, key, path):
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return
        h, w = img.shape
        msg = RosImage()
        msg.header.stamp = to_stamp(t)
        msg.header.frame_id = "cam0" if key == "l" else "cam1"
        msg.height, msg.width = h, w
        msg.step = w
        msg.encoding = "mono8"
        msg.is_bigendian = False
        msg.data = bytearray(img.tobytes())  # 必须 bytearray, 否则 FastRTPS 崩
        (pub0 if key == "l" else pub1).publish(msg)

    def pub_imu(ts, gx, gy, gz, ax, ay, az):
        # 重力 Y->Z 变换 (与 replay_db3_to_ros2.py 一致)
        gx_n = 0.99980212*gx - 0.01423891*gy - 0.01389161*gz
        gy_n = -0.01423891*gx - 0.02458715*gy - 0.99959628*gz
        gz_n = 0.01389161*gx + 0.99959628*gy - 0.02478503*gz
        ax_n = 0.99980212*ax - 0.01423891*ay - 0.01389161*az
        ay_n = -0.01423891*ax - 0.02458715*ay - 0.99959628*az
        az_n = 0.01389161*ax + 0.99959628*ay - 0.02478503*az
        im = RosImu()
        im.header.stamp = to_stamp(ts)
        im.angular_velocity.x = gx_n
        im.angular_velocity.y = gy_n
        im.angular_velocity.z = gz_n
        im.linear_acceleration.x = ax_n
        im.linear_acceleration.y = ay_n
        im.linear_acceleration.z = az_n
        pubi.publish(im)

    # 合并图像+IMU 按时间
    events = []
    for idx, ts in cam_rows:
        events.append((ts, "img", idx))
    for ts, *imu in imu_rows:
        events.append((ts, "imu", imu))
    events.sort(key=lambda e: e[0])

    t0_data = events[0][0]
    t0_wall = time.monotonic()
    n = 0
    for ev in events:
        target = t0_wall + (ev[0] - t0_data) / max(args.rate, 1e-6)
        d = target - time.monotonic()
        if d > 0:
            time.sleep(min(d, 0.2))
        if ev[1] == "img":
            pub_img(ev[0], "l", unit / "frames" / f"{ev[2]:06d}.jpg")
            pub_img(ev[0], "r", unit / "frames_right" / f"{ev[2]:06d}.jpg")
            n += 1
            if n % 200 == 0:
                print(f"[replay] img={n} t={ev[0]-t0_data:.0f}s", flush=True)
        else:
            pub_imu(ev[0], *ev[2])

    print(f"[replay] 完成: {n} 帧")
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
