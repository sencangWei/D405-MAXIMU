#!/usr/bin/env python3
"""Windows 端：接收 Ubuntu OpenVINS 发来的 pose，用 Rerun 显示轨迹。

用法:
  python scripts/rerun_odom_viewer.py
"""
import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    import rerun as rr

    rr.init("ego_vio_trajectory", spawn=True)
    rr.log("world", rr.ViewCoordinates.RDF, static=True)
    rr.log("world/traj", rr.LineStrips3D([[[0, 0, 0]]]), static=True)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 12346))
    server.listen(1)
    print("[viewer] 监听 0.0.0.0:12346, 等待 Ubuntu 连接...")

    poses = [(0.0, 0.0, 0.0)]
    last_ts = 0.0

    def recv_n(sock, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    conn = None
    n_received = 0

    while True:
        try:
            if conn is None:
                print("[viewer] 等待连接...")
                conn, addr = server.accept()
                print(f"[viewer] 已连接: {addr}")

            meta = recv_n(conn, 8)
            if meta is None:
                print("[viewer] 连接断开, 重连...")
                conn.close()
                conn = None
                continue

            hlen, _ = struct.unpack(">II", meta)
            header_bytes = recv_n(conn, hlen)
            if header_bytes is None:
                conn.close()
                conn = None
                continue

            header = json.loads(header_bytes.decode("utf-8"))
            if header.get("type") == "pose":
                tx, ty, tz = float(header["tx"]), float(header["ty"]), float(header["tz"])
                poses.append((tx, ty, tz))
                if len(poses) > 10000:
                    poses = poses[-5000:]

                now = time.time()
                rr.set_time_nanos("time", int(header["ts"] * 1e9))
                rr.log("world/cam", rr.Transform3D(translation=[tx, ty, tz], mat3x3=np.eye(3)))

                n_received += 1
                if n_received % 50 == 0:
                    # Rebuild line strip
                    pts = np.array(poses, dtype=np.float32).reshape(1, -1, 3)
                    rr.log("world/traj", rr.LineStrips3D(pts))
                    print(f"[viewer] 收到 {n_received} 帧, 当前位置 ({tx:.2f}, {ty:.2f}, {tz:.2f})")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[viewer] 错误: {e}")
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
            time.sleep(1)

    if conn:
        conn.close()
    server.close()
    print("[viewer] 已停止")


if __name__ == "__main__":
    main()
