"""OpenVINS 实时 ROS2 发布桥。

Ubuntu 主机上直接发布 /cam0/image_raw 和 /imu0 话题，
OpenVINS 通过 ROS2 订阅这些话题。

注意:
  - IMU 加速度从 g 转换为 m/s^2 (ROS / OpenVINS 标准单位)
  - 发布前转 MONO8 (当前配置 1280x720), 与 OpenVINS 输入一致并减少 DDS 带宽
  - IMU 高频小包, 同步发布
"""

from __future__ import annotations
import array
import math
import queue
import threading
import time
from typing import Optional

import numpy as np

from .base import VIOBackend, Pose
from ..imu.imu_reader import ImuSample
from ..imu.vins_transform import load_vins_imu_rotation
from ..camera.realsense_capture import CameraFrame

_STANDARD_GRAVITY = 9.80665


def _summarize_camera_publish_timings(samples: list[dict]) -> dict:
    """Summarize a bounded camera-publisher timing window in milliseconds."""
    if not samples:
        return {}
    result = {}
    for field in ("prepare0", "publish0", "prepare1", "publish1", "cycle"):
        values = np.asarray([sample[field] for sample in samples], dtype=np.float64)
        result[f"ros_cam_{field}_p50_ms"] = float(np.percentile(values, 50))
        result[f"ros_cam_{field}_p95_ms"] = float(np.percentile(values, 95))
        result[f"ros_cam_{field}_max_ms"] = float(np.max(values))
    return result


def _rotate_imu_to_vins(
    gyro_deg_s: np.ndarray,
    accel_g: np.ndarray,
    rotation: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    transform = load_vins_imu_rotation() if rotation is None else rotation
    gyro_rad_s = transform @ np.radians(gyro_deg_s)
    accel_m_s2 = transform @ (accel_g * _STANDARD_GRAVITY)
    return gyro_rad_s, accel_m_s2


def _camera_has_imu_lead(latest_imu_t: float, camera_t: float, guard_s: float) -> bool:
    """Return whether enough newer IMU data was published for this image."""
    return latest_imu_t >= camera_t + guard_s


def _put_latest(target: queue.Queue, item: object) -> int:
    """Put without blocking, replacing queued stale data when necessary."""
    dropped = 0
    while True:
        try:
            target.put_nowait(item)
            return dropped
        except queue.Full:
            try:
                target.get_nowait()
                dropped += 1
            except queue.Empty:
                continue


def _preview_rate_due(t: float, last_t: float, period_s: float) -> tuple[bool, float]:
    """Rate-limit epoch timestamps without losing every other boundary frame."""
    if not math.isfinite(last_t) or t < last_t:
        return True, t
    elapsed = t - last_t
    # Epoch-valued doubles have sub-microsecond ULPs.  A strict comparison to
    # 1/30 can classify an exact 30 Hz boundary as early and collapse output
    # to roughly 15 Hz.  Keep the ideal phase and admit that rounding margin.
    tolerance = max(1e-9, 2.0 * math.ulp(max(abs(t), abs(last_t))))
    if elapsed + tolerance < period_s:
        return False, last_t
    periods = max(1, math.floor((elapsed + tolerance) / period_s))
    return True, last_t + periods * period_s


def _as_mono8(image: np.ndarray) -> np.ndarray:
    """Return contiguous mono8 data matching OpenVINS' actual input."""
    if image.ndim == 2:
        return np.ascontiguousarray(image, dtype=np.uint8)
    import cv2
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


class OpenVINSROS2Bridge(VIOBackend):
    name = "openvins_ros2"

    def __init__(
        self,
        name: str = "openvins_ros2",
        cam_topic: str = "/cam0/image_raw",
        cam1_topic: str = "/cam1/image_raw",
        imu_topic: str = "/imu0",
        stereo: bool = False,
        queue_size: int = 100,
        qos_reliable: bool = True,
        epoch_offset: float = 0.0,
        cam_latency_ms: float = 0.0,
        imu_level_calibration: str = "",
        preview_topic: str = "",
        preview_hz: float = 30.0,
    ):
        if preview_hz <= 0.0:
            raise ValueError("preview_hz must be positive")
        self.name = name
        self._epoch_offset = epoch_offset
        self._cam_latency_s = cam_latency_ms / 1000.0
        self._stereo = stereo
        self._imu_rotation = load_vins_imu_rotation(imu_level_calibration)
        self._imu_first_counter = None
        self._imu_first_ts = None
        self._imu_period = 1.0 / 400.0

        import rclpy
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
        from sensor_msgs.msg import Image, Imu

        if not rclpy.ok():
            rclpy.init(args=None)

        self._node = Node(f"ov_bridge_{name}")
        qos = QoSProfile(
            depth=queue_size,
            reliability=ReliabilityPolicy.RELIABLE if qos_reliable else ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
        )
        self._cam_pub = self._node.create_publisher(Image, cam_topic, qos)
        self._cam1_pub = (
            self._node.create_publisher(Image, cam1_topic, qos) if stereo else None
        )
        self._imu_pub = self._node.create_publisher(Imu, imu_topic, qos)
        preview_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
        )
        self._preview_pub = (
            self._node.create_publisher(Image, preview_topic, preview_qos)
            if preview_topic
            else None
        )

        # 图像异步队列: 采集线程放帧, 后台线程取帧发布
        self._cam_queue: queue.Queue = queue.Queue(maxsize=3)
        self._cam_warmup_discarded = 0
        self._cam_queue_dropped = 0
        self._cam_published = 0
        self._cam_timing_lock = threading.Lock()
        self._cam_timing_window: list[dict] = []
        self._cam_timing_stats: dict = {}
        self._imu_published = 0
        self._preview_queue: queue.Queue = queue.Queue(maxsize=1)
        self._preview_period_s = 1.0 / preview_hz
        self._preview_last_enqueued_t = float("-inf")
        self._preview_published = 0
        self._preview_queue_dropped = 0
        self._stop = threading.Event()
        self._imu_guard_s = 0.010
        self._latest_imu_t = float("-inf")
        self._imu_ready = threading.Condition()

        # bridge 只发布不订阅, publish() 同步调用无需 spin
        self._cam_thread = threading.Thread(target=self._cam_loop, name=f"ov-cam-{name}", daemon=True)
        self._cam_thread.start()
        self._preview_thread = None
        if self._preview_pub is not None:
            self._preview_thread = threading.Thread(
                target=self._preview_loop,
                name=f"preview-{name}",
                daemon=True,
            )
            self._preview_thread.start()
        camera_topics = f"{cam_topic} + {cam1_topic}" if stereo else cam_topic
        preview_description = f" + {preview_topic} (latest-only)" if preview_topic else ""
        print(
            f"[{self.name}] ROS2 bridge publishing {imu_topic} + "
            f"{camera_topics} (async images){preview_description}"
        )

    def _spin(self):
        import rclpy
        try:
            rclpy.spin(self._node)
        except Exception as e:
            print(f"[{self.name}] rclpy.spin 退出: {e}")

    def _cam_loop(self):
        """后台线程: 从队列取帧并发布。"""
        from sensor_msgs.msg import Image

        while not self._stop.is_set():
            try:
                item = self._cam_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                continue

            t, h, w, data0, data1 = item
            cycle_started = time.perf_counter()
            # DDS does not preserve ordering across the image and IMU topics.
            # Publish an image only after later IMU samples have already been
            # handed to DDS, so OpenVINS can always propagate to image time.
            with self._imu_ready:
                ready = self._imu_ready.wait_for(
                    lambda: self._stop.is_set()
                    or _camera_has_imu_lead(self._latest_imu_t, t, self._imu_guard_s),
                    timeout=0.25,
                )
                if self._stop.is_set():
                    break
                if not ready:
                    self._cam_warmup_discarded += 1
                    continue

            sec, nanosec = self._sec_nanos(t)

            def make_message(frame_id: str, data: bytes) -> Image:
                msg = Image()
                msg.header.stamp.sec = sec
                msg.header.stamp.nanosec = nanosec
                msg.header.frame_id = frame_id
                msg.height = int(h)
                msg.width = int(w)
                msg.encoding = "mono8"
                msg.is_bigendian = 0
                msg.step = int(w)
                # rclpy 的 msg.data = bytes 会逐元素做 Python 侧校验，720p
                # 双目每帧约 1.8 MB 时只能发布约 14 fps。原地写入 typed
                # array 走连续缓冲区路径，与已修复的离线回放保持一致。
                msg.data[:] = array.array("B", data)
                return msg

            prepare0_started = time.perf_counter()
            msg0 = make_message("cam0", data0)
            prepare0_ms = (time.perf_counter() - prepare0_started) * 1000.0
            publish0_started = time.perf_counter()
            self._cam_pub.publish(msg0)
            publish0_ms = (time.perf_counter() - publish0_started) * 1000.0
            prepare1_ms = 0.0
            publish1_ms = 0.0
            if self._stereo and self._cam1_pub is not None and data1 is not None:
                prepare1_started = time.perf_counter()
                msg1 = make_message("cam1", data1)
                prepare1_ms = (time.perf_counter() - prepare1_started) * 1000.0
                publish1_started = time.perf_counter()
                self._cam1_pub.publish(msg1)
                publish1_ms = (time.perf_counter() - publish1_started) * 1000.0
            self._cam_published += 1
            timing = {
                "prepare0": prepare0_ms,
                "publish0": publish0_ms,
                "prepare1": prepare1_ms,
                "publish1": publish1_ms,
                "cycle": (time.perf_counter() - cycle_started) * 1000.0,
            }
            with self._cam_timing_lock:
                self._cam_timing_window.append(timing)
                if len(self._cam_timing_window) >= 30:
                    self._cam_timing_stats = _summarize_camera_publish_timings(
                        self._cam_timing_window
                    )
                    self._cam_timing_window.clear()

    def _preview_loop(self):
        """Publish downsampled RGB for the operator without VINS backpressure."""
        from sensor_msgs.msg import Image

        while not self._stop.is_set():
            try:
                item = self._preview_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                continue
            t, h, w, data = item
            sec, nanosec = self._sec_nanos(t)
            msg = Image()
            msg.header.stamp.sec = sec
            msg.header.stamp.nanosec = nanosec
            msg.header.frame_id = "rgb_preview"
            msg.height = int(h)
            msg.width = int(w)
            msg.encoding = "bgr8"
            msg.is_bigendian = 0
            msg.step = int(w) * 3
            msg.data[:] = array.array("B", data)
            self._preview_pub.publish(msg)
            self._preview_published += 1

    def _sec_nanos(self, t: float):
        sec = int(t)
        nanosec = int((t - sec) * 1e9)
        return sec, nanosec

    def feed_imu(self, sample: ImuSample) -> Optional[Pose]:
        from sensor_msgs.msg import Imu

        t = sample.ts + self._epoch_offset  # Unix epoch for ROS2
        sec, nanosec = self._sec_nanos(t)

        msg = Imu()
        msg.header.stamp.sec = sec
        msg.header.stamp.nanosec = nanosec
        msg.header.frame_id = "imu0"

        gyro, accel = _rotate_imu_to_vins(
            np.array([sample.gx, sample.gy, sample.gz], dtype=np.float64),
            np.array([sample.ax, sample.ay, sample.az], dtype=np.float64),
            self._imu_rotation,
        )
        msg.linear_acceleration.x = float(accel[0])
        msg.linear_acceleration.y = float(accel[1])
        msg.linear_acceleration.z = float(accel[2])

        msg.angular_velocity.x = float(gyro[0])
        msg.angular_velocity.y = float(gyro[1])
        msg.angular_velocity.z = float(gyro[2])

        self._imu_pub.publish(msg)
        self._imu_published += 1
        with self._imu_ready:
            self._latest_imu_t = max(self._latest_imu_t, t)
            self._imu_ready.notify_all()
        return None

    def feed_camera(self, frame: CameraFrame) -> Optional[Pose]:
        if self._stereo:
            if frame.infrared_left is None or frame.infrared_right is None:
                return None
            mono0 = _as_mono8(frame.infrared_left)
            mono1 = _as_mono8(frame.infrared_right)
            if mono0.shape != mono1.shape:
                return None
        else:
            if frame.color is None:
                return None
            mono0 = _as_mono8(frame.color)
            mono1 = None
        h, w = mono0.shape
        t = frame.ts + self._epoch_offset - self._cam_latency_s

        # The camera starts before the serial IMU, so discard images only
        # until the first post-warmup IMU sample exists. Once IMU transport is
        # active, enqueue the image even when IMU has not yet reached the
        # 10 ms lead: _cam_loop is deliberately responsible for waiting for
        # those future samples. Requiring the lead here as well deadlocks a
        # live camera whose fitted timestamp is close to its arrival time.
        with self._imu_ready:
            if not np.isfinite(self._latest_imu_t):
                self._cam_warmup_discarded += 1
                return None

        # 非阻塞入队; 队列满时丢弃 (采集线程不受阻)
        item = (
            t,
            h,
            w,
            mono0.tobytes(),
            mono1.tobytes() if mono1 is not None else None,
        )
        try:
            self._cam_queue.put(item, block=False)
        except queue.Full:
            self._cam_queue_dropped += 1
        preview_due = False
        preview_phase_t = t
        if self._preview_pub is not None and frame.color is not None:
            preview_due, preview_phase_t = _preview_rate_due(
                t, self._preview_last_enqueued_t, self._preview_period_s
            )
        if preview_due:
            # Half-resolution nearest-neighbour preview is sufficient for the
            # operator and keeps DDS/Rerun work isolated from calibrated IR.
            preview = np.ascontiguousarray(frame.color[::2, ::2])
            preview_item = (
                t,
                int(preview.shape[0]),
                int(preview.shape[1]),
                preview.tobytes(),
            )
            self._preview_queue_dropped += _put_latest(
                self._preview_queue, preview_item
            )
            self._preview_last_enqueued_t = preview_phase_t
        return None

    def latest(self) -> Optional[Pose]:
        return None

    def transport_stats(self) -> dict:
        """Return cumulative bridge counters for live acceptance checks."""
        with self._cam_timing_lock:
            timing_stats = dict(self._cam_timing_stats)
        stats = {
            "ros_imu_pub": self._imu_published,
            "ros_cam_pub": self._cam_published,
            "ros_cam_warmup_discard": self._cam_warmup_discarded,
            "ros_cam_drop": self._cam_queue_dropped,
            "ros_cam_queue": self._cam_queue.qsize(),
            "preview_pub": self._preview_published,
            "preview_drop": self._preview_queue_dropped,
            "preview_queue": self._preview_queue.qsize(),
        }
        stats.update(timing_stats)
        return stats

    def close(self):
        self._stop.set()
        with self._imu_ready:
            self._imu_ready.notify_all()
        _put_latest(self._cam_queue, None)
        _put_latest(self._preview_queue, None)
        self._cam_thread.join(timeout=1.0)
        if self._preview_thread is not None:
            self._preview_thread.join(timeout=1.0)
        try:
            self._node.destroy_node()
        except Exception:
            pass
        if self._cam_warmup_discarded or self._cam_queue_dropped:
            print(
                f"[{self.name}] 启动预热等待 {self._cam_warmup_discarded} 帧, "
                f"运行期真实丢帧 {self._cam_queue_dropped} 帧, "
                f"发布 {self._cam_published} 帧"
            )
        if self._preview_queue_dropped:
            print(
                f"[{self.name}] RGB预览latest-only替换 "
                f"{self._preview_queue_dropped} 帧, 发布 {self._preview_published} 帧"
            )
