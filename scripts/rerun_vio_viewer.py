#!/usr/bin/env python3
"""独立 Rerun 查看器，复用产品原有的完整实时可视化界面。

订阅 IMU 快速传播位姿、后端原始/回环校正位姿、RGB 操作员预览和 IMU。
快速位姿、RGB和IMU采用BEST_EFFORT/KEEP_LAST(depth=1)；只为精确配对的两条
后端位姿保留32条BEST_EFFORT历史。显示负载不会反压VINS主链。
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.vio.base import Pose
from ego_vio.visualizer.rerun_viz import RerunVisualizer


RERUN_PORT = 9876
RERUN_MEMORY_LIMIT = "2GB"
RERUN_DROP_AT_LATENCY = "250ms"


class SlamHealthDisplay:
    """Keep the latest valid watchdog state for the Rerun stats panel."""

    def __init__(self) -> None:
        self.state = "STARTING"
        self.product_usable = "false"
        self.failures = "none"

    def update(self, payload_text: str) -> None:
        try:
            payload = json.loads(payload_text)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        state = payload.get("state")
        if isinstance(state, str) and state:
            self.state = state
        usable = payload.get("product_usable")
        if isinstance(usable, bool):
            self.product_usable = str(usable).lower()
        failures = payload.get("failures")
        if isinstance(failures, list):
            self.failures = ",".join(str(value) for value in failures) or "none"


def _managed_rerun_command(executable: str, port: int = RERUN_PORT) -> list[str]:
    return [
        executable,
        f"--port={port}",
        f"--memory-limit={RERUN_MEMORY_LIMIT}",
        f"--drop-at-latency={RERUN_DROP_AT_LATENCY}",
        "--expect-data-soon",
    ]


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def _start_managed_rerun(port: int = RERUN_PORT) -> subprocess.Popen:
    if _port_is_open(port):
        raise RuntimeError(
            f"Rerun端口{port}已被占用；请先停止上一次product-live，禁止复用旧窗口"
        )
    executable = shutil.which("rerun")
    if executable is None:
        raise RuntimeError("找不到rerun可执行程序")
    process = subprocess.Popen(_managed_rerun_command(executable, port))
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"Rerun启动失败，退出码={process.returncode}")
        if _port_is_open(port):
            return process
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=2.0)
    raise RuntimeError("Rerun启动超时，10秒内未监听端口")


def _stop_managed_rerun(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _normalize_quaternion(quaternion: np.ndarray) -> np.ndarray:
    q = np.asarray(quaternion, dtype=np.float64).reshape(4)
    norm = float(np.linalg.norm(q))
    if norm < 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
    return q / norm


def _quaternion_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Multiply xyzw quaternions and return a normalized result."""
    left = _normalize_quaternion(left)
    right = _normalize_quaternion(right)
    lv, lw = left[:3], left[3]
    rv, rw = right[:3], right[3]
    return _normalize_quaternion(
        np.concatenate(
            (lw * rv + rw * lv + np.cross(lv, rv), [lw * rw - np.dot(lv, rv)])
        )
    )


def _quaternion_rotate(quaternion: np.ndarray, vector: np.ndarray) -> np.ndarray:
    q = _normalize_quaternion(quaternion)
    v = np.asarray(vector, dtype=np.float64).reshape(3)
    cross = 2.0 * np.cross(q[:3], v)
    return v + q[3] * cross + np.cross(q[:3], cross)


class LoopCorrection:
    """Pair slow raw/rectified poses and rectify the latest IMU prediction."""

    def __init__(self, max_pending: int = 32) -> None:
        self._raw = {}
        self._corrected = {}
        self._max_pending = max_pending
        self.rotation = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
        self.translation = np.zeros(3, dtype=np.float64)
        self.valid = False
        self.last_pair_stamp = None
        self.last_pair_received_mono = None
        self.paired_updates = 0
        self.rejected_out_of_order = 0

    def add_raw(self, stamp, position: np.ndarray, orientation: np.ndarray) -> None:
        self._raw[stamp] = (
            np.asarray(position, dtype=np.float64).reshape(3),
            _normalize_quaternion(orientation),
        )
        self._try_pair(stamp)
        self._prune()

    def add_corrected(
        self, stamp, position: np.ndarray, orientation: np.ndarray
    ) -> None:
        self._corrected[stamp] = (
            np.asarray(position, dtype=np.float64).reshape(3),
            _normalize_quaternion(orientation),
        )
        self._try_pair(stamp)
        self._prune()

    def apply(
        self, position: np.ndarray, orientation: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        position = np.asarray(position, dtype=np.float64).reshape(3)
        orientation = _normalize_quaternion(orientation)
        return (
            _quaternion_rotate(self.rotation, position) + self.translation,
            _quaternion_multiply(self.rotation, orientation),
        )

    @property
    def pending_raw(self) -> int:
        return len(self._raw)

    @property
    def pending_corrected(self) -> int:
        return len(self._corrected)

    def _try_pair(self, stamp) -> None:
        if stamp not in self._raw or stamp not in self._corrected:
            return
        raw_t, raw_q = self._raw.pop(stamp)
        corrected_t, corrected_q = self._corrected.pop(stamp)
        if self.last_pair_stamp is not None and stamp <= self.last_pair_stamp:
            self.rejected_out_of_order += 1
            return
        raw_q_inverse = np.array(
            [-raw_q[0], -raw_q[1], -raw_q[2], raw_q[3]], dtype=np.float64
        )
        self.rotation = _quaternion_multiply(corrected_q, raw_q_inverse)
        self.translation = corrected_t - _quaternion_rotate(self.rotation, raw_t)
        self.valid = True
        self.last_pair_stamp = stamp
        self.last_pair_received_mono = time.monotonic()
        self.paired_updates += 1

    def _prune(self) -> None:
        for pending in (self._raw, self._corrected):
            while len(pending) > self._max_pending:
                pending.pop(next(iter(pending)))


def _shutdown_rclpy_once(rclpy_module) -> None:
    """Avoid a second shutdown after ROS handles SIGINT itself."""
    if rclpy_module.ok():
        rclpy_module.shutdown()


def _message_time(msg) -> float:
    stamp = msg.header.stamp
    return float(stamp.sec) + float(stamp.nanosec) * 1e-9


def _message_key(msg) -> tuple[int, int]:
    stamp = msg.header.stamp
    return int(stamp.sec), int(stamp.nanosec)


def _message_pose(msg) -> tuple[np.ndarray, np.ndarray]:
    position = msg.pose.pose.position
    orientation = msg.pose.pose.orientation
    return (
        np.array([position.x, position.y, position.z], dtype=np.float64),
        np.array(
            [orientation.x, orientation.y, orientation.z, orientation.w],
            dtype=np.float64,
        ),
    )


def _decode_image(msg):
    """Return a contiguous image while respecting ROS row stride and encoding."""
    encoding = msg.encoding.lower()
    raw = np.frombuffer(msg.data, dtype=np.uint8)
    if encoding in ("mono8", "8uc1"):
        return np.ascontiguousarray(
            raw.reshape(msg.height, msg.step)[:, :msg.width]
        )
    if encoding in ("bgr8", "rgb8"):
        rows = raw.reshape(msg.height, msg.step)[:, :msg.width * 3]
        image = rows.reshape(msg.height, msg.width, 3)
        if encoding == "rgb8":
            image = image[:, :, ::-1]
        return np.ascontiguousarray(image)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--odom-topic", default="/ov_msckf/odomimu")
    parser.add_argument("--raw-odom-topic", default="/odometry")
    parser.add_argument("--propagated-topic", default="/imu_propagate")
    parser.add_argument("--image-topic", default="/cam0/image_raw")
    parser.add_argument("--health-topic", default="/slam/health")
    args = parser.parse_args()

    import rclpy
    from nav_msgs.msg import Odometry
    from rclpy.executors import ExternalShutdownException
    from rclpy.node import Node
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import Image as ImageMsg
    from sensor_msgs.msg import Imu as ImuMsg
    from std_msgs.msg import String

    # Own the viewer process so Ctrl-C tears it down with this ROS observer.
    # A bounded store and latency drop keep software rendering from exhausting
    # RAM or accumulating seconds of obsolete frames on this CPU-only host.
    rerun_process = None
    if args.headless:
        viz = RerunVisualizer(["left_hand"], spawn=False)
    else:
        rerun_process = _start_managed_rerun()
        try:
            viz = RerunVisualizer(
                ["left_hand"],
                spawn=False,
                connect_addr=f"127.0.0.1:{RERUN_PORT}",
            )
        except Exception:
            _stop_managed_rerun(rerun_process)
            raise

    rclpy.init(args=None)
    node = Node("rerun_vio_viewer")
    counts = {
        "pose": 0,
        "backend_raw": 0,
        "backend_corrected": 0,
        "propagated": 0,
        "image": 0,
        "imu": 0,
    }
    started_at = time.monotonic()
    last_fast_message_ts = None
    last_fast_received_mono = None
    correction = LoopCorrection()
    health = SlamHealthDisplay()

    def log_pose(ts: float, position: np.ndarray, orientation: np.ndarray) -> None:
        pose = Pose(
            ts=ts,
            t=position,
            q=orientation,
        )
        viz.log_pose("left_hand", pose, img_width=640, img_height=360)
        counts["pose"] += 1

    def on_raw_odom(msg: Odometry) -> None:
        position, orientation = _message_pose(msg)
        correction.add_raw(_message_key(msg), position, orientation)
        counts["backend_raw"] += 1

    def on_corrected_odom(msg: Odometry) -> None:
        position, orientation = _message_pose(msg)
        correction.add_corrected(_message_key(msg), position, orientation)
        counts["backend_corrected"] += 1
        # The corrected backend is the signed-off pose.  Do not substitute the
        # noisier IMU propagation path merely to hide backend latency.
        log_pose(_message_time(msg), position, orientation)

    def on_propagated_odom(msg: Odometry) -> None:
        nonlocal last_fast_message_ts, last_fast_received_mono
        counts["propagated"] += 1
        last_fast_message_ts = _message_time(msg)
        last_fast_received_mono = time.monotonic()

    def on_image(msg: ImageMsg) -> None:
        try:
            cv_img = _decode_image(msg)
        except (ValueError, TypeError):
            return
        if cv_img is None:
            return
        ts = _message_time(msg)
        viz.log_image("left_hand", cv_img, ts, max_hz=30.0)
        counts["image"] += 1

    def on_imu(msg: ImuMsg) -> None:
        accel = msg.linear_acceleration
        gyro = msg.angular_velocity
        viz.log_imu_si(
            "left_hand",
            _message_time(msg),
            (accel.x, accel.y, accel.z),
            (gyro.x, gyro.y, gyro.z),
            max_hz=50.0,
        )
        counts["imu"] += 1

    def log_stats() -> None:
        elapsed = max(time.monotonic() - started_at, 1e-6)
        fast_age_ms = (
            max(0.0, time.time() - last_fast_message_ts) * 1000.0
            if last_fast_message_ts is not None
            else None
        )
        fast_rx_age_ms = (
            (time.monotonic() - last_fast_received_mono) * 1000.0
            if last_fast_received_mono is not None
            else None
        )
        correction_age_ms = (
            (time.monotonic() - correction.last_pair_received_mono) * 1000.0
            if correction.last_pair_received_mono is not None
            else None
        )
        viz.log_stats(
            {
                "left_hand": {
                    "backend_hz": f'{counts["backend_corrected"] / elapsed:.1f}',
                    "fast_imu_pose_hz": f'{counts["propagated"] / elapsed:.1f}',
                    "fast_age_ms": f"{fast_age_ms:.1f}" if fast_age_ms is not None else "waiting",
                    "fast_rx_age_ms": (
                        f"{fast_rx_age_ms:.1f}"
                        if fast_rx_age_ms is not None
                        else "waiting"
                    ),
                    "rgb_rx_hz": f'{counts["image"] / elapsed:.1f}',
                    "imu_rx_hz": f'{counts["imu"] / elapsed:.1f}',
                    "loop_correction": "paired" if correction.valid else "identity",
                    "correction_rx_age_ms": (
                        f"{correction_age_ms:.1f}"
                        if correction_age_ms is not None
                        else "waiting"
                    ),
                    "correction_pairs": str(correction.paired_updates),
                    "correction_old_rejected": str(correction.rejected_out_of_order),
                    "correction_pending": (
                        f"raw={correction.pending_raw},rect={correction.pending_corrected}"
                    ),
                    "display": "odometry_rect/latest-only",
                    "slam_state": health.state,
                    "product_usable": health.product_usable,
                    "slam_failures": health.failures,
                }
            },
            time.time(),
        )

    visualization_qos = QoSProfile(
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        history=HistoryPolicy.KEEP_LAST,
    )
    # Exact stamps are required to recover the loop correction.  A short
    # BEST_EFFORT history tolerates callback scheduling jitter without making
    # this observer reliable or capable of backpressuring either publisher.
    backend_pair_qos = QoSProfile(
        depth=32,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        history=HistoryPolicy.KEEP_LAST,
    )
    node.create_subscription(
        Odometry, args.raw_odom_topic, on_raw_odom, backend_pair_qos
    )
    node.create_subscription(
        Odometry, args.odom_topic, on_corrected_odom, backend_pair_qos
    )
    node.create_subscription(
        Odometry, args.propagated_topic, on_propagated_odom, visualization_qos
    )
    node.create_subscription(ImageMsg, args.image_topic, on_image, visualization_qos)
    node.create_subscription(ImuMsg, "/imu0", on_imu, visualization_qos)
    node.create_subscription(
        String,
        args.health_topic,
        lambda message: health.update(message.data),
        visualization_qos,
    )
    node.create_timer(1.0, log_stats)
    print(
        f"[viewer] 原版完整界面：{args.odom_topic}（回环校正后端主轨迹）"
        f" + {args.propagated_topic}（仅诊断，不用于绘制）"
        f" + {args.image_topic} + /imu0；RGB目标30Hz，BEST_EFFORT latest-only"
    )

    try:
        # Keep spin in the main thread so callback/API failures terminate the
        # viewer and are reported by the product wrapper.
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        _shutdown_rclpy_once(rclpy)
        try:
            viz.rr.disconnect()
        finally:
            _stop_managed_rerun(rerun_process)
        print(
            f'[viewer] 停止, pose={counts["pose"]} '
            f'fast={counts["propagated"]} backend={counts["backend_corrected"]} '
            f'img={counts["image"]} imu={counts["imu"]}'
        )


if __name__ == "__main__":
    main()
