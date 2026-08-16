#!/usr/bin/env python3
"""Ubuntu 侧: 订阅 /ov_msckf/odomimu, 把 pose 通过 socket 发到 Windows Rerun。

用法:
  python3 scripts/odom_to_windows.py --host 192.168.113.100 --port 12346
"""
import argparse
import json
import socket
import struct
import sys
import time


def pack_pose(header: dict) -> bytes:
    header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack(">II", len(header_bytes), 0) + header_bytes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="Windows 主机 IP")
    ap.add_argument("--port", type=int, default=12346)
    args = ap.parse_args()

    import sys as _sys
    _ros_py = "/opt/ros/jazzy/lib/python3.12/site-packages"
    if _ros_py not in _sys.path:
        _sys.path.insert(0, _ros_py)

    import rclpy
    from rclpy.node import Node
    from nav_msgs.msg import Odometry

    rclpy.init(args=None)
    node = Node("odom_to_windows")

    sock: socket.socket | None = None
    last_log = 0
    sent = 0

    def ensure_connected() -> bool:
        nonlocal sock
        if sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((args.host, args.port))
            s.settimeout(None)
            sock = s
            print(f"[odom_to_windows] 已连接 Windows {args.host}:{args.port}")
            return True
        except Exception as e:
            print(f"[odom_to_windows] 连接 Windows 失败: {e}")
            sock = None
            return False

    def on_odom(msg: Odometry):
        nonlocal sock, last_log, sent
        stamp = msg.header.stamp
        ts = stamp.sec + stamp.nanosec * 1e-9
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        header = {
            "type": "pose",
            "ts": ts,
            "tx": float(p.x),
            "ty": float(p.y),
            "tz": float(p.z),
            "qx": float(q.x),
            "qy": float(q.y),
            "qz": float(q.z),
            "qw": float(q.w),
        }
        data = pack_pose(header)
        if ensure_connected():
            try:
                sock.sendall(data)
                sent += 1
                now = time.time()
                if now - last_log >= 3.0:
                    print(f"[odom_to_windows] sent={sent}")
                    last_log = now
            except Exception as e:
                print(f"[odom_to_windows] 发送失败: {e}")
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None

    node.create_subscription(Odometry, "/ov_msckf/odomimu", on_odom, 10)
    print(f"[odom_to_windows] 订阅 /ov_msckf/odomimu, 目标 Windows {args.host}:{args.port}")

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
        if sock:
            sock.close()


if __name__ == "__main__":
    sys.exit(main())
