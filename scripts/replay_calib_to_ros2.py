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
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.imu.imu_reader import fit_counter_timestamps
from scripts.convert_to_kalibr_bag import (
    read_camera_ts,
    read_imu_arrival_timestamps,
    read_imu_bin,
    select_imu_timestamp_fit,
)

G0 = 9.80665
DEG2RAD = 3.141592653589793 / 180.0


def load_fitted_timelines(unit: Path):
    """复用 Kalibr 转换器的时间拟合，保证标定和 VINS 回放同一时基。"""
    imu_samples = read_imu_bin(unit / "imu.bin")
    arrival_ts, arrival_source = read_imu_arrival_timestamps(
        unit / "imu_ts.csv",
        unit / "camera_ts.csv",
        [sample["counter"] for sample in imu_samples],
    )
    imu_fitted, imu_info, imu_source = select_imu_timestamp_fit(
        imu_samples, arrival_ts, arrival_source
    )
    imu_rows = [
        (
            float(ts),
            sample["gx"] * DEG2RAD,
            sample["gy"] * DEG2RAD,
            sample["gz"] * DEG2RAD,
            sample["ax"] * G0,
            sample["ay"] * G0,
            sample["az"] * G0,
        )
        for sample, ts in zip(imu_samples, imu_fitted)
    ]

    camera_raw = read_camera_ts(unit / "camera_ts.csv")
    camera_fitted, camera_info = fit_counter_timestamps(
        [row[2] for row in camera_raw], [row[1] for row in camera_raw]
    )
    camera_rows = [
        (row[0], float(ts)) for row, ts in zip(camera_raw, camera_fitted)
    ]
    return camera_rows, imu_rows, camera_info, imu_info, imu_source


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--imu-shift-ms", type=float, default=0.0,
                    help="IMU 发布戳平移(ms), 补偿相机-IMU 时间偏移 (Kalibr timeshift)")
    ap.add_argument(
        "--imu-preroll-s",
        type=float,
        default=0.1,
        help="首帧图像前至少保留的 IMU 预滚动时间，避免 VINS 启动预积分缺口",
    )
    args = ap.parse_args()
    sess = Path(args.session).resolve()
    unit = sess / "left_hand"

    cam_rows, imu_rows, camera_info, imu_info, imu_source = load_fitted_timelines(unit)
    imu_rows = [(t + args.imu_shift_ms / 1000.0, *rest) for t, *rest in imu_rows]
    if not cam_rows or not imu_rows:
        raise RuntimeError("标定会话没有可回放的相机或 IMU 数据")
    camera_count_before_trim = len(cam_rows)
    first_usable_camera_time = imu_rows[0][0] + max(args.imu_preroll_s, 0.0)
    last_usable_camera_time = imu_rows[-1][0]
    cam_rows = [
        row
        for row in cam_rows
        if first_usable_camera_time <= row[1] <= last_usable_camera_time
    ]
    if not cam_rows:
        raise RuntimeError("相机与 IMU 没有满足预滚动要求的公共时间窗")
    print(
        f"[replay] 相机拟合: {camera_info['rate_hz']:.3f}fps, "
        f"sigma={camera_info['sigma_ms']:.3f}ms; "
        f"IMU拟合({imu_source}): {imu_info['rate_hz']:.3f}Hz, "
        f"sigma={imu_info['sigma_ms']:.3f}ms; "
        f"额外IMU平移={args.imu_shift_ms:+.3f}ms",
        flush=True,
    )
    print(
        f"[replay] 公共时间窗: 图像 {camera_count_before_trim}->{len(cam_rows)} 帧, "
        f"首帧前IMU预滚动={cam_rows[0][1] - imu_rows[0][0]:.3f}s, "
        f"尾帧距IMU结束={imu_rows[-1][0] - cam_rows[-1][1]:.3f}s",
        flush=True,
    )

    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Image as RosImage, Imu as RosImu
    from builtin_interfaces.msg import Time as RosTime

    rclpy.init()
    node = Node("calib_replay")
    pub0 = node.create_publisher(RosImage, "/cam0/image_raw", 10)
    pub1 = node.create_publisher(RosImage, "/cam1/image_raw", 10)
    pubi = node.create_publisher(RosImu, "/imu0", 2000)

    discovery_deadline = time.monotonic() + 5.0
    while time.monotonic() < discovery_deadline:
        counts = (
            pub0.get_subscription_count(),
            pub1.get_subscription_count(),
            pubi.get_subscription_count(),
        )
        if min(counts) > 0:
            break
        rclpy.spin_once(node, timeout_sec=0.05)
    counts = (
        pub0.get_subscription_count(),
        pub1.get_subscription_count(),
        pubi.get_subscription_count(),
    )
    if min(counts) == 0:
        print(
            f"[replay] WARN: DDS订阅未全部匹配: "
            f"cam0={counts[0]} cam1={counts[1]} imu={counts[2]}",
            flush=True,
        )
    else:
        print(
            f"[replay] DDS已匹配: cam0={counts[0]} cam1={counts[1]} imu={counts[2]}",
            flush=True,
        )

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
