"""RealSense D405 采集。

后台线程持续取帧，回调(CameraFrame)。时间戳用系统单调时钟，
跟 IMU 同源 → 后续 VIO/对齐不用跨时钟。

注意:
  - D405 无内置 IMU，配合外部军工 IMU 用，时间轴干净。
  - 实时 VIO 用单目彩色(enable_depth=False)降算力。
  - 头部要后处理 SLAM/点云，开深度。
"""

from __future__ import annotations
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from ..timing import OnlineCounterFitter


@dataclass
class CameraFrame:
    ts: float                      # 最优时间戳(秒, monotonic 基准):
                                   # global_time 启用时为曝光时刻, 否则为帧序号拟合后的估计曝光时刻
    color: Optional[np.ndarray]    # BGR8 (H,W,3)
    depth: Optional[np.ndarray]    # Z16 毫米 (H,W) 或 None
    frame_idx: int
    ts_arrival: float = 0.0        # 到达 PC 时刻(诊断 USB 抖动用)
    ts_domain: str = ""            # 时间戳域: global_time / hardware_clock / arrival
    frame_number: int = 0          # 相机设备帧序号(丢帧检测/时间戳拟合用)
    infrared_left: Optional[np.ndarray] = None
    infrared_right: Optional[np.ndarray] = None


class RealSenseCapture:
    def __init__(
        self,
        serial: str = "",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        enable_depth: bool = False,
        stereo_ir: bool = False,
        auto_exposure: bool = True,
        exposure_us: int = 20000,
        gain: int = 48,
        on_frame: Optional[Callable[[CameraFrame], None]] = None,
        name: str = "cam",
    ):
        self.serial = serial
        self.width = width
        self.height = height
        self.fps = fps
        self.enable_depth = enable_depth
        self.stereo_ir = stereo_ir
        self.auto_exposure = auto_exposure
        self.exposure_us = exposure_us
        self.gain = gain
        self.on_frame = on_frame
        self.name = name

        self._pipeline = None
        self._cfg = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.frame_count = 0
        self.recent_dt = []
        self._last_ts = 0.0

        # 在线时间戳去抖(无 global_time 时用帧序号拟合估计曝光时刻)
        self._ts_fitter = OnlineCounterFitter(counter_wrap=None, window_size=60, fit_every=10)

    def start(self) -> bool:
        try:
            import pyrealsense2 as rs
        except ImportError:
            raise RuntimeError("需要 pyrealsense2: pip install pyrealsense2")

        self._rs = rs
        context = rs.context()
        devices = list(context.query_devices())
        device = next(
            (
                d for d in devices
                if not self.serial
                or d.get_info(rs.camera_info.serial_number) == self.serial
            ),
            None,
        )
        if device is None:
            print(f"[{self.name}] 找不到相机(serial={self.serial!r})")
            return False

        sensor = device.first_depth_sensor()
        try:
            sensor.set_option(rs.option.enable_auto_exposure, 1.0 if self.auto_exposure else 0.0)
            if not self.auto_exposure:
                sensor.set_option(rs.option.exposure, float(self.exposure_us))
                sensor.set_option(rs.option.gain, float(self.gain))
                print(
                    f"[{self.name}] 相机固定曝光 {self.exposure_us} us, "
                    f"gain={self.gain}"
                )
        except Exception as e:
            print(f"[{self.name}] 设置相机曝光失败: {e}")
            return False

        self._pipeline = rs.pipeline(context)
        self._cfg = rs.config()
        if self.serial:
            self._cfg.enable_device(self.serial)
        if self.stereo_ir:
            self._cfg.enable_stream(
                rs.stream.infrared, 1, self.width, self.height, rs.format.y8, self.fps
            )
            self._cfg.enable_stream(
                rs.stream.infrared, 2, self.width, self.height, rs.format.y8, self.fps
            )
        else:
            self._cfg.enable_stream(
                rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps
            )
        if self.enable_depth and not self.stereo_ir:
            self._cfg.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)

        try:
            self._pipeline.start(self._cfg)
        except Exception as e:
            print(f"[{self.name}] 启动失败(serial={self.serial!r}): {e}")
            return False

        # 与标定 pipeline 一致: 帧序号拟合去抖, 不用 global_time
        self._clk_off = None

        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"cam-{self.name}", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        # 在 Windows 上, 如果 wait_for_frames 正在阻塞, pipeline.stop()
        # 可能挂死。先等采集线程自己退出, 超时则直接放弃(线程是 daemon)。
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._pipeline:
            try:
                # 只有线程已退出或不需要再 stop 时才安全调用
                if self._thread is None or not self._thread.is_alive():
                    self._pipeline.stop()
            except Exception:
                pass
            self._pipeline = None

    def _loop(self):
        rs = self._rs
        while self._running:
            try:
                frames = self._pipeline.wait_for_frames(timeout_ms=1000)
            except Exception:
                continue

            infrared_left = None
            infrared_right = None
            if self.stereo_ir:
                left_frame = frames.get_infrared_frame(1)
                right_frame = frames.get_infrared_frame(2)
                if not left_frame or not right_frame:
                    continue
                if left_frame.get_frame_number() != right_frame.get_frame_number():
                    continue
                infrared_left = np.asanyarray(left_frame.get_data())
                infrared_right = np.asanyarray(right_frame.get_data())
                # Keep the existing preview/recording path useful: show the
                # left rectified infrared image as the primary image.
                color_frame = left_frame
                color = infrared_left
            else:
                color_frame = frames.get_color_frame()
                if not color_frame:
                    continue
                color = np.asanyarray(color_frame.get_data())

            depth = None
            if self.enable_depth:
                depth_frame = frames.get_depth_frame()
                if depth_frame:
                    depth = np.asanyarray(depth_frame.get_data())

            now = time.monotonic()
            self.frame_count += 1
            self.recent_dt.append(now - self._last_ts) if self._last_ts else None
            self._last_ts = now
            # 限制统计长度
            if len(self.recent_dt) > 600:
                self.recent_dt = self.recent_dt[-600:]

            # 时间戳选择:
            # 1. global_time 域 = 曝光时刻(主机 system 钟, 毫秒), 转成 monotonic;
            # 2. 其他域回退到帧序号拟合, 用到达时刻估计曝光时刻并去抖。
            ts = now
            domain = "arrival"
            try:
                if color_frame.get_frame_timestamp_domain() == rs.timestamp_domain.global_time:
                    off = time.time() - now
                    self._clk_off = (
                        off if self._clk_off is None
                        else self._clk_off * 0.98 + off * 0.02
                    )
                    ts = color_frame.get_timestamp() / 1000.0 - self._clk_off
                    domain = "global_time"
                else:
                    # 没有 global_time 时, 用帧序号在线拟合去抖
                    ts = self._ts_fitter.feed(color_frame.get_frame_number(), now)
                    domain = "fitted_arrival"
            except Exception:
                pass

            f = CameraFrame(
                ts=ts,
                color=color,
                depth=depth,
                frame_idx=self.frame_count,
                ts_arrival=now,
                ts_domain=domain,
                frame_number=color_frame.get_frame_number(),
                infrared_left=infrared_left,
                infrared_right=infrared_right,
            )
            if self.on_frame:
                try:
                    self.on_frame(f)
                except Exception:
                    pass

    def get_intrinsics(self):
        """返回 RGB 相机内参 (K, D)。未启动时返回 None。"""
        if self._pipeline is None:
            return None
        try:
            import pyrealsense2 as rs
            profile = self._pipeline.get_active_profile()
            stream = profile.get_stream(rs.stream.color)
            color_profile = rs.video_stream_profile(stream)
            intr = color_profile.get_intrinsics()
            K = np.array([
                [intr.fx, 0.0, intr.ppx],
                [0.0, intr.fy, intr.ppy],
                [0.0, 0.0, 1.0],
            ], dtype=np.float64)
            D = np.array(intr.coeffs, dtype=np.float64)
            return K, D
        except Exception as e:
            print(f"[{self.name}] 获取相机内参失败: {e}")
            return None

    def stats(self) -> dict:
        dts = [d for d in self.recent_dt if d > 0]
        dt_ms = [d * 1000 for d in dts]
        return {
            "frames": self.frame_count,
            "rate_hz": (len(dts) / sum(dts)) if dts and sum(dts) > 0 else 0.0,
            "dt_min_ms": min(dt_ms) if dt_ms else 0.0,
            "dt_max_ms": max(dt_ms) if dt_ms else 0.0,
            "dt_jitter_ms": (max(dt_ms) - min(dt_ms)) if dt_ms else 0.0,
        }
