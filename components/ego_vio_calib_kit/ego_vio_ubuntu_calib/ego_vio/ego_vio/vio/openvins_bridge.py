"""OpenVINS 实时 socket 桥接后端。

Windows 端采集的数据通过 TCP 发到 WSL 里的 ROS2 输入节点,
由该节点发布 /cam0/image_raw 和 /imu0, 供 OpenVINS subscribe 使用。

协议(二进制, 大端):
  [4 bytes header_len][4 bytes payload_len][JSON header][payload]

header 字段:
  - type: "imu" | "image"
  - ts: float, 秒(与 Windows monotonic 时间对齐)
  IMU 额外: ax,ay,az (g), gx,gy,gz (°/s)
  图像额外: frame_number, width, height, encoding="bgr8"

注意: 这里只把数据送出去, 位姿由 WSL 端 OpenVINS 计算并通过
rerun_live_bridge.py 显示; Windows 本地 Rerun 只显示图像。
"""

from __future__ import annotations
import json
import queue
import socket
import struct
import threading
from typing import Optional

import numpy as np

from .base import VIOBackend, Pose
from ..imu.imu_reader import ImuSample
from ..camera.realsense_capture import CameraFrame


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 12345


def _pack_message(header: dict, payload: bytes = b"") -> bytes:
    """把 header 和 payload 打包成带长度前缀的帧。"""
    header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack(">II", len(header_bytes), len(payload)) + header_bytes + payload


class OpenVINSSocketBridge(VIOBackend):
    name = "openvins_socket"

    def __init__(
        self,
        name: str = "openvins_socket",
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        queue_size: int = 120,
        epoch_offset: float = 0.0,
        cam_latency_ms: float = 0.0,
    ):
        self.name = name
        self.host = host
        self.port = port
        self._epoch_offset = epoch_offset
        # cam_latency_ms 只用于 OpenVINS 输入: 把 PC 到达时间拉回接近曝光时刻,
        # 与 Kalibr bag 的 cam_offset 一致。
        self._cam_latency_s = cam_latency_ms / 1000.0
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=queue_size)
        self._sock: Optional[socket.socket] = None
        self._connected_once = False
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._sender_loop, name=f"ov-bridge-{name}", daemon=True)
        self._thread.start()

    # ---------- 连接/发送线程 ----------
    def _ensure_connected(self) -> bool:
        if self._sock is not None:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect((self.host, self.port))
            sock.settimeout(None)
            self._sock = sock
            self._connected_once = True
            print(f"[{self.name}] 已连接 WSL bridge {self.host}:{self.port}")
            return True
        except Exception as e:
            if not self._connected_once:
                print(f"[{self.name}] 连接 WSL bridge 失败(将在后台重连): {e}")
            self._sock = None
            return False

    def _sender_loop(self):
        while not self._stop_evt.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if not self._ensure_connected():
                # 连接失败时丢弃该包, 避免队列堆积
                continue
            try:
                self._sock.sendall(item)
            except Exception as e:
                print(f"[{self.name}] 发送失败: {e}")
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def _enqueue(self, data: bytes) -> bool:
        try:
            self._queue.put(data, block=False)
            return True
        except queue.Full:
            return False

    # ---------- VIOBackend 接口 ----------
    def feed_imu(self, sample: ImuSample) -> Optional[Pose]:
        header = {
            "type": "imu",
            "ts": sample.ts + self._epoch_offset,
            "ax": float(sample.ax),
            "ay": float(sample.ay),
            "az": float(sample.az),
            "gx": float(sample.gx),
            "gy": float(sample.gy),
            "gz": float(sample.gz),
        }
        if not self._enqueue(_pack_message(header)):
            # 队列满时偶尔丢弃 IMU 不会明显影响 VIO
            pass
        # 本地不产 pose; pose 由 WSL OpenVINS 输出
        return None

    def feed_camera(self, frame: CameraFrame) -> Optional[Pose]:
        if frame.color is None:
            return None
        h, w = frame.color.shape[:2]
        header = {
            "type": "image",
            "ts": float(frame.ts + self._epoch_offset - self._cam_latency_s),
            "frame_number": int(frame.frame_number),
            "width": int(w),
            "height": int(h),
            "encoding": "bgr8",
        }
        payload = frame.color.tobytes()
        if not self._enqueue(_pack_message(header, payload)):
            print(f"[{self.name}] 图像发送队列满, 丢帧")
        return None

    def latest(self) -> Optional[Pose]:
        # OpenVINS 不在这里回传 pose
        return None

    def close(self):
        self._stop_evt.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._thread.join(timeout=2.0)
