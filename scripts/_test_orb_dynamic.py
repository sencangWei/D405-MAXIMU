#!/usr/bin/env python3
"""健壮 ORB-SLAM3 RGBD 测试: 管理 ORB节点 + 订阅odom + 回放."""
import csv
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry

ROOT = Path(__file__).resolve().parents[1]
CONFIG = str(ROOT / "config/orbslam3_d405_rgbd_inertial_720p.yaml")
ORB_ROOT = "/home/robot/ego_pipeline/work/toolchains/ORB_SLAM3"
VOCAB = f"{ORB_ROOT}/Vocabulary/ORBvoc.txt"
OUT = "/tmp/orb_test_odom.csv"


def main():
    sess = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "rgbd"
    # 启动 ORB
    orb = subprocess.Popen(
        ["ros2", "run", "ego_orbslam3_ros2", "rgbd_inertial_node",
         "--ros-args", "-p", f"vocabulary:={VOCAB}", "-p", f"settings:={CONFIG}",
         "-p", "viewer:=false"],
        stdout=open("/tmp/orb_t.log", "w"), stderr=subprocess.STDOUT,
        preexec_fn=os.setsid)
    time.sleep(5)

    rclpy.init()
    node = Node("orb_odom_sink")
    rows = []
    def cb(m):
        rows.append([m.header.stamp.sec + m.header.stamp.nanosec*1e-9,
                     m.pose.pose.position.x, m.pose.pose.position.y, m.pose.pose.position.z])
    node.create_subscription(Odometry, "/orbslam3/odom", cb, 100)

    replay = subprocess.Popen(
        ["python3", "scripts/replay_db3_to_ros2.py", "--session", sess,
         "--mode", mode, "--rate", "1.0", "--imu-align-s", "0.0"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL)
    t0 = time.time()
    try:
        while time.time() - t0 < 120 and replay.poll() is None:
            rclpy.spin_once(node, timeout_sec=0.05)
    except KeyboardInterrupt:
        pass
    t1 = time.time()
    while time.time() - t1 < 5:
        rclpy.spin_once(node, timeout_sec=0.05)
    replay.kill()

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_sec", "x", "y", "z"])
        w.writerows(rows)

    if len(rows) > 3:
        import numpy as np
        p = np.array([[r[1], r[2], r[3]] for r in rows])
        path = np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1))
        ts = np.array([r[0] for r in rows])
        v = np.linalg.norm(np.diff(p, axis=0), axis=1) / np.maximum(np.diff(ts), 1e-6)
        print(f"ORB 轨迹: {len(rows)}点 路径{path:.1f}m 中位速度{np.median(v):.2f}m/s")
    else:
        print(f"ORB 轨迹太少: {len(rows)}")

    try:
        os.killpg(os.getpgid(orb.pid), signal.SIGKILL)
    except Exception:
        orb.kill()
    node.destroy_node()
    rclpy.shutdown()
    log = open("/tmp/orb_t.log").read()
    init_lines = [l for l in log.split("\n") if "Initialization" in l or "IMU init" in l or "pose x=" in l]
    print("ORB 初始化:", init_lines[:3] if init_lines else "无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
