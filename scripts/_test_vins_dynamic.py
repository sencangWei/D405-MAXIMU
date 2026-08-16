#!/usr/bin/env python3
"""健壮的 VINS 动态测试: 管理 VINS + 订阅odom + 回放 + 报告轨迹."""
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
CONFIG = os.environ.get("VINS_CONFIG",
    "/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml")
OUT = os.environ.get("VINS_OUT", "/tmp/vins_test_odom.csv")
VINS_LOG = os.environ.get("VINS_LOG", "/tmp/vins_t.log")
REPLAY_LOG = os.environ.get("REPLAY_LOG", "/tmp/replay_t.log")
# 可指定压缩回放变体 (replay_db3_hevc_to_ros2.py) 验证 HEVC 有损压缩对精度的影响
REPLAY_SCRIPT = os.environ.get("REPLAY_SCRIPT", "scripts/replay_db3_to_ros2.py")
IMU_LEVEL_CALIBRATION = os.environ.get("VINS_IMU_LEVEL_CALIBRATION", "")
TEST_TIMEOUT_S = float(os.environ.get("VINS_TEST_TIMEOUT_S", "360"))
DRAIN_TIMEOUT_S = float(os.environ.get("VINS_DRAIN_TIMEOUT_S", "30"))
DRAIN_QUIET_S = float(os.environ.get("VINS_DRAIN_QUIET_S", "5"))


def main():
    sess = sys.argv[1]
    # 启动 VINS
    vins_log = open(VINS_LOG, "w")
    vins = subprocess.Popen(
        ["ros2", "run", "vins_fusion_ros2", "vins_fusion_ros2_node",
         "--ros-args", "-p", "use_sim_time:=false", "-p", f"config_file:={CONFIG}"],
        stdout=vins_log, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid)
    time.sleep(5)
    if vins.poll() is not None:
        vins_log.close()
        print(f"VINS 启动失败: 退出码 {vins.returncode}")
        print(Path(VINS_LOG).read_text(errors="replace"))
        return 2

    # 订阅 /odometry 写文件
    rclpy.init()
    node = Node("odom_sink")
    rows = []
    def cb(m):
        rows.append([m.header.stamp.sec + m.header.stamp.nanosec*1e-9,
                     m.pose.pose.position.x, m.pose.pose.position.y, m.pose.pose.position.z])
    node.create_subscription(Odometry, "/odometry", cb, 100)

    # 回放 (子进程)
    # shift 默认 0: 与配置固定 td=-0.0117 (08-08 Kalibr) 配对, 回放不再改 IMU 时间戳。
    # 旧默认 7.36 (08-04 陈旧标定) + 固定 td 会双重补偿 → 发散 (见 dual-ir-divergence-rootcause)。
    replay_log = open(REPLAY_LOG, "w")
    replay_command = [
        "python3", REPLAY_SCRIPT, "--session", sess,
        "--mode", os.environ.get("VINS_MODE", "stereo"),
        "--rate", sys.argv[3] if len(sys.argv)>3 else "1.0",
        "--skip-s", sys.argv[2] if len(sys.argv)>2 else "1.5",
        "--imu-align-s", sys.argv[5] if len(sys.argv)>5 else "0",
        "--imu-shift-ms", sys.argv[4] if len(sys.argv)>4 else "0",
    ]
    if IMU_LEVEL_CALIBRATION:
        replay_command.extend(["--imu-level-calibration", IMU_LEVEL_CALIBRATION])
    replay = subprocess.Popen(
        replay_command,
        cwd=str(ROOT), stdout=replay_log, stderr=subprocess.STDOUT)
    t0 = time.monotonic()
    vins_exited = False
    try:
        while time.monotonic() - t0 < TEST_TIMEOUT_S and replay.poll() is None:
            if vins.poll() is not None:
                vins_exited = True
                break
            rclpy.spin_once(node, timeout_sec=0.05)
    except KeyboardInterrupt:
        pass
    if vins_exited:
        print(f"VINS 运行中提前退出: 退出码 {vins.returncode}")
    elif replay.poll() is None:
        print(f"回放超时: {TEST_TIMEOUT_S:.0f}s")
    elif replay.returncode != 0:
        print(f"回放失败: 退出码 {replay.returncode}")
    # 回放结束后等待后端队列真正排空。固定等待数秒会在 30fps 压测时
    # 截掉队尾，使闭环/Z 误差统计落在不同终点。
    drain_start = time.monotonic()
    quiet_since = drain_start
    last_row_count = len(rows)
    while time.monotonic() - drain_start < DRAIN_TIMEOUT_S:
        rclpy.spin_once(node, timeout_sec=0.05)
        if len(rows) != last_row_count:
            last_row_count = len(rows)
            quiet_since = time.monotonic()
        elif rows and time.monotonic() - quiet_since >= DRAIN_QUIET_S:
            break
    replay.kill()
    replay_log.close()

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_sec", "x", "y", "z"])
        w.writerows(rows)

    # 报告
    if len(rows) > 3:
        import numpy as np
        p = np.array([[r[1], r[2], r[3]] for r in rows])
        path = np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1))
        ts = np.array([r[0] for r in rows])
        v = np.linalg.norm(np.diff(p, axis=0), axis=1) / np.maximum(np.diff(ts), 1e-6)
        print(f"轨迹: {len(rows)}点 路径{path:.1f}m 中位速度{np.median(v):.2f}m/s")
    else:
        print(f"轨迹太少: {len(rows)}")

    # 清理
    try:
        os.killpg(os.getpgid(vins.pid), signal.SIGKILL)
    except Exception:
        vins.kill()
    node.destroy_node()
    rclpy.shutdown()
    # 查 VINS 日志
    vins_log.close()
    log = open(VINS_LOG).read()
    jumps = [l for l in log.split("\n") if "jump" in l or "Residual" in l or "Terminating" in l]
    print("VINS 警告:", jumps[:5] if jumps else "无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
