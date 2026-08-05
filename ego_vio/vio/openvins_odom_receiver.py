"""接收 WSL OpenVINS 发来的位姿，交给 Windows 本地 Rerun 显示。

WSL 端运行 odom_socket_bridge.py，订阅 /ov_msckf/odomimu，
把 Pose 通过 TCP 发到 Windows 的 12346 端口。

协议:
  [4 bytes header_len][4 bytes payload_len][JSON header]
header 字段:
  - type: "pose"
  - ts: float, 秒
  - tx, ty, tz: 平移(米)
  - qx, qy, qz, qw: 四元数 xyzw
"""

from __future__ import annotations
import json
import queue
import socket
import struct
import threading
from typing import Callable, Optional

import numpy as np

from .base import Pose


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 12346


def _pack_message(header: dict) -> bytes:
    header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack(">II", len(header_bytes), 0) + header_bytes


class OpenVINSOdomReceiver:
    """TCP server，接收来自 WSL 的位姿。"""

    def __init__(
        self,
        on_pose: Callable[[Pose], None],
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        epoch_offset: float = 0.0,
    ):
        self.on_pose = on_pose
        self.host = host
        self.port = port
        self._epoch_offset = epoch_offset
        self._queue: queue.Queue[Pose] = queue.Queue(maxsize=200)
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(1)
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, name="ov-odom-accept", daemon=True)
        self._dispatch_thread = threading.Thread(target=self._dispatch_loop, name="ov-odom-dispatch", daemon=True)
        self._accept_thread.start()
        self._dispatch_thread.start()
        print(f"[odom_receiver] listening on {host}:{port}")

    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._server.accept()
            except OSError:
                break
            print(f"[odom_receiver] WSL connected from {addr}")
            threading.Thread(target=self._handle_conn, args=(conn,), name="ov-odom-conn", daemon=True).start()

    def _handle_conn(self, conn: socket.socket):
        def recv_n(n: int):
            buf = b""
            while len(buf) < n:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            return buf

        while self._running:
            meta = recv_n(8)
            if meta is None:
                break
            hlen, _ = struct.unpack(">II", meta)
            header_bytes = recv_n(hlen)
            if header_bytes is None:
                break
            header = json.loads(header_bytes.decode("utf-8"))
            if header.get("type") == "pose":
                pose = Pose(
                    ts=float(header["ts"]) - self._epoch_offset,
                    t=np.array([float(header["tx"]), float(header["ty"]), float(header["tz"])]),
                    q=np.array([float(header["qx"]), float(header["qy"]), float(header["qz"]), float(header["qw"])]),
                )
                try:
                    self._queue.put(pose, block=False)
                except queue.Full:
                    pass
        conn.close()

    def _dispatch_loop(self):
        while self._running:
            try:
                pose = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.on_pose(pose)
            except Exception:
                pass

    def close(self):
        self._running = False
        try:
            self._server.close()
        except Exception:
            pass
        self._accept_thread.join(timeout=1.0)
        self._dispatch_thread.join(timeout=1.0)
