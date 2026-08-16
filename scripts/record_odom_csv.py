#!/usr/bin/env python3
"""订阅里程计 topic 并保存轨迹 CSV。

用法:
  python3 scripts/record_odom_csv.py --topic /orbslam3/odom --out traj_orb.csv
  python3 scripts/record_odom_csv.py --topic /vins_estimator/odometry --out traj_vins.csv
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from nav_msgs.msg import Odometry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    rclpy.init()
    topic_suffix = args.topic.strip("/").replace("/", "_") or "root"
    node = Node(f"odom_recorder_{topic_suffix}")
    fp = args.out.open("w", newline="", encoding="utf-8")
    writer = csv.writer(fp)
    writer.writerow(
        [
            "t_sec", "x", "y", "z", "qx", "qy", "qz", "qw",
            "vx", "vy", "vz", "wx", "wy", "wz",
        ]
    )
    n = 0
    t0 = None

    def cb(msg: Odometry):
        nonlocal n, t0
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if t0 is None:
            t0 = t
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        linear = msg.twist.twist.linear
        angular = msg.twist.twist.angular
        writer.writerow(
            [
                f"{t - t0:.6f}", p.x, p.y, p.z, q.x, q.y, q.z, q.w,
                linear.x, linear.y, linear.z,
                angular.x, angular.y, angular.z,
            ]
        )
        fp.flush()
        n += 1

    node.create_subscription(Odometry, args.topic, cb, 50)
    print(f"[odom_csv] 订阅 {args.topic} -> {args.out}")
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        fp.close()
        print(f"[odom_csv] 保存 {n} 个位姿 -> {args.out}")
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
