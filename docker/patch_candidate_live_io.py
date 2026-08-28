#!/usr/bin/env python3
"""Patch only the derived image's live display and same-source recorder path."""

from __future__ import annotations

import argparse
from pathlib import Path


def patch_bridge(source: str) -> str:
    imports_old = '''from __future__ import annotations
import array
import math
'''
    imports_new = '''from __future__ import annotations
import array
import json
import math
'''
    if source.count(imports_old) != 1:
        raise RuntimeError("bridge import anchor changed")
    source = source.replace(imports_old, imports_new, 1)
    gripper_import_old = '''from .base import VIOBackend, Pose
from ..imu.imu_reader import ImuSample
'''
    gripper_import_new = '''from .base import VIOBackend, Pose
from ..gripper import ManualGripperCalibration, ManualGripperTracker
from ..imu.imu_reader import ImuSample
'''
    if source.count(gripper_import_old) != 1:
        raise RuntimeError("bridge gripper import anchor changed")
    source = source.replace(gripper_import_old, gripper_import_new, 1)

    helper_anchor = '''_STANDARD_GRAVITY = 9.80665


'''
    helper = '''_STANDARD_GRAVITY = 9.80665


def _gripper_sample_payload(tracker, sample):
    if sample.protocol != "stm32_combined_v1":
        return None
    valid = (
        sample.encoder_ts is not None
        and sample.encoder_response is not None
        and bool(sample.flags & (1 << 1))
        and not bool(sample.flags & ((1 << 2) | (1 << 3)))
    )
    raw_count = (
        int(sample.encoder_response) & 0x3FFF
        if sample.encoder_response is not None
        else 0
    )
    state = tracker.update(raw_count * 360.0 / 16384.0, encoder_valid=valid)
    return {
        "schema": "umi_gripper_live_v1",
        "encoder_ts_mono": sample.encoder_ts,
        "valid": valid,
        "angle_deg": state.angle_deg if valid else None,
        "estimated_no_load_gap_mm": (
            state.estimated_no_load_gap_mm if valid else None
        ),
    }


'''
    if source.count(helper_anchor) != 1:
        raise RuntimeError("bridge helper anchor changed")
    source = source.replace(helper_anchor, helper, 1)

    signature_old = '''        preview_topic: str = "",
        preview_hz: float = 30.0,
    ):
        if preview_hz <= 0.0:
            raise ValueError("preview_hz must be positive")
'''
    signature_new = '''        preview_topic: str = "",
        preview_hz: float = 30.0,
        gripper_topic: str = "/gripper/state",
        gripper_hz: float = 15.0,
    ):
        if preview_hz <= 0.0:
            raise ValueError("preview_hz must be positive")
        if gripper_hz <= 0.0:
            raise ValueError("gripper_hz must be positive")
'''
    if source.count(signature_old) != 1:
        raise RuntimeError("bridge signature anchor changed")
    source = source.replace(signature_old, signature_new, 1)

    publisher_old = '''        from sensor_msgs.msg import Image, Imu

        if not rclpy.ok():
'''
    publisher_new = '''        from sensor_msgs.msg import Image, Imu
        from std_msgs.msg import String

        if not rclpy.ok():
'''
    if source.count(publisher_old) != 1:
        raise RuntimeError("bridge message import anchor changed")
    source = source.replace(publisher_old, publisher_new, 1)

    preview_old = '''        self._preview_pub = (
            self._node.create_publisher(Image, preview_topic, preview_qos)
            if preview_topic
            else None
        )

        # 图像异步队列'''
    preview_new = '''        self._preview_pub = (
            self._node.create_publisher(Image, preview_topic, preview_qos)
            if preview_topic
            else None
        )
        self._gripper_pub = (
            self._node.create_publisher(String, gripper_topic, preview_qos)
            if gripper_topic
            else None
        )
        self._gripper_tracker = (
            ManualGripperTracker(ManualGripperCalibration.load())
            if self._gripper_pub is not None
            else None
        )
        self._gripper_period_s = 1.0 / gripper_hz
        self._gripper_last_published_t = float("-inf")
        self._gripper_published = 0

        # 图像异步队列'''
    if source.count(preview_old) != 1:
        raise RuntimeError("bridge preview publisher anchor changed")
    source = source.replace(preview_old, preview_new, 1)

    imu_old = '''        self._imu_pub.publish(msg)
        self._imu_published += 1
        with self._imu_ready:
'''
    imu_new = '''        self._imu_pub.publish(msg)
        self._imu_published += 1
        if (
            self._gripper_pub is not None
            and self._gripper_tracker is not None
            and t - self._gripper_last_published_t >= self._gripper_period_s
        ):
            payload = _gripper_sample_payload(self._gripper_tracker, sample)
            if payload is not None:
                from std_msgs.msg import String
                state_message = String()
                state_message.data = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                )
                self._gripper_pub.publish(state_message)
                self._gripper_last_published_t = t
                self._gripper_published += 1
        with self._imu_ready:
'''
    if source.count(imu_old) != 1:
        raise RuntimeError("bridge IMU publish anchor changed")
    source = source.replace(imu_old, imu_new, 1)

    stats_old = '''            "preview_queue": self._preview_queue.qsize(),
        }
'''
    stats_new = '''            "preview_queue": self._preview_queue.qsize(),
            "gripper_pub": self._gripper_published,
        }
'''
    if source.count(stats_old) != 1:
        raise RuntimeError("bridge transport stats anchor changed")
    return source.replace(stats_old, stats_new, 1)


def patch_capture(source: str) -> str:
    helper_anchor = '''def preview_mosaic(frame_map: dict) -> np.ndarray:
'''
    helper = '''def color_frame_to_bgr(frame) -> np.ndarray:
    """Decode the recorder queue's native YUYV frame for live preview.

    librealsense exposes this stream as a 720x1280 uint16 array on the
    low-level sensor queue used by the lossless recorder.  Reinterpret each
    uint16 pixel as its two native YUYV bytes before asking OpenCV to decode
    it.  Keep this conversion outside the recorder path: DB3 remains native
    YUYV and no preview work can change the stored evidence.
    """
    color_yuyv = np.asanyarray(frame.get_data())
    if color_yuyv.dtype == np.uint16:
        color_yuyv = color_yuyv.view(np.uint8).reshape(
            color_yuyv.shape + (2,)
        )
    elif color_yuyv.ndim == 2 and color_yuyv.shape[1] == 2560:
        color_yuyv = color_yuyv.reshape(color_yuyv.shape[0], 1280, 2)
    if color_yuyv.shape != (720, 1280, 2):
        raise RuntimeError(
            f"unexpected native YUYV buffer: shape={color_yuyv.shape} "
            f"dtype={color_yuyv.dtype}"
        )
    return cv2.cvtColor(color_yuyv, cv2.COLOR_YUV2BGR_YUY2)


def preview_mosaic(frame_map: dict) -> np.ndarray:
'''
    if source.count(helper_anchor) != 1:
        raise RuntimeError("capture color helper anchor changed")
    source = source.replace(helper_anchor, helper, 1)

    bridge_old = '''            qos_reliable=True,
            epoch_offset=epoch_offset,
        )
'''
    bridge_new = '''            qos_reliable=True,
            epoch_offset=epoch_offset,
            imu_lead_guard_ms=float(
                os.environ.get("EGO_VIO_IMU_LEAD_GUARD_MS", "10.0")
            ),
            preview_topic="/rgb_preview/image_raw",
            preview_hz=30.0,
            gripper_topic="/gripper/state",
            gripper_hz=15.0,
        )
'''
    if source.count(bridge_old) != 1:
        raise RuntimeError("capture bridge anchor changed")
    source = source.replace(bridge_old, bridge_new, 1)

    frame_old = '''                            color=left_image,
                            depth=None,
'''
    frame_new = '''                            color=color_frame_to_bgr(
                                latest_frames["color"]
                            ),
                            depth=None,
'''
    if source.count(frame_old) != 1:
        raise RuntimeError("capture RGB preview anchor changed")
    return source.replace(frame_old, frame_new, 1)


def patch_visualizer(source: str) -> str:
    helper_anchor = '''def _bbox_scene(bbox_min: np.ndarray, bbox_max: np.ndarray,
'''
    helper = '''def _format_gripper_markdown(
    angle_deg: float | None, gap_mm: float | None
) -> str:
    angle = "--" if angle_deg is None else f"{float(angle_deg):.2f}°"
    gap = "--" if gap_mm is None else f"{float(gap_mm):.2f} mm"
    # The dashboard row is intentionally compact. Large Markdown headings
    # pushed the gap value below the visible viewport even though it existed
    # in every live payload.
    return f"**编码器角度**：{angle}  \\n**夹爪开合距离**：{gap}"


def _bbox_scene(bbox_min: np.ndarray, bbox_max: np.ndarray,
'''
    if source.count(helper_anchor) != 1:
        raise RuntimeError("visualizer helper anchor changed")
    source = source.replace(helper_anchor, helper, 1)

    method_anchor = '''    def log_imu(self, unit: str, sample, max_hz: float = 50.0):
'''
    method = '''    def log_gripper_state(
        self,
        unit: str,
        angle_deg: float | None,
        gap_mm: float | None,
        ts: float,
    ) -> None:
        self._set_time(float(ts))
        self.rr.log(
            f"world/{unit}/gripper",
            self.rr.TextDocument(
                _format_gripper_markdown(angle_deg, gap_mm),
                media_type="text/markdown",
            ),
        )

    def log_imu(self, unit: str, sample, max_hz: float = 50.0):
'''
    if source.count(method_anchor) != 1:
        raise RuntimeError("visualizer gripper method anchor changed")
    source = source.replace(method_anchor, method, 1)

    blueprint_old = '''        panel_views = []
        for name in self.unit_names:
            panel_views.extend([
                rrb.Spatial2DView(origin=f"world/{name}/image", name=f"{name} camera"),
                rrb.TimeSeriesView(origin=f"world/{name}/imu/**", name=f"{name} IMU"),
            ])
        right = rrb.Vertical(
            *(panel_views + [rrb.TextLogView(origin="stats", name="stats")])
        )
'''
    blueprint_new = '''        panel_views = []
        panel_shares = []
        for name in self.unit_names:
            panel_views.extend([
                rrb.Spatial2DView(origin=f"world/{name}/image", name=f"{name} camera"),
                rrb.TextDocumentView(origin=f"world/{name}/gripper", name="夹爪"),
                rrb.TimeSeriesView(origin=f"world/{name}/imu/**", name=f"{name} IMU"),
            ])
            panel_shares.extend([4.0, 1.4, 2.0])
        right = rrb.Vertical(
            *(panel_views + [rrb.TextLogView(origin="stats", name="stats")]),
            row_shares=panel_shares + [1.0],
        )
'''
    if source.count(blueprint_old) != 1:
        raise RuntimeError("visualizer blueprint anchor changed")
    return source.replace(blueprint_old, blueprint_new, 1)


def patch_viewer(source: str) -> str:
    if "import os\n" not in source:
        source = source.replace("import json\n", "import json\nimport os\n", 1)
    args_old = '''    args = parser.parse_args()

    import rclpy
'''
    args_new = '''    parser.add_argument("--gripper-topic", default="/gripper/state")
    args = parser.parse_args()
    initial_settle_s = float(os.environ.get("EGO_VIO_VIEWER_INITIAL_SETTLE_S", "0"))
    if initial_settle_s < 0.0:
        raise ValueError("EGO_VIO_VIEWER_INITIAL_SETTLE_S must be non-negative")

    import rclpy
'''
    if source.count(args_old) != 1:
        raise RuntimeError("viewer args anchor changed")
    source = source.replace(args_old, args_new, 1)

    helper_anchor = '''def main() -> None:
'''
    helper = '''def _decode_gripper_state(payload_text: str):
    try:
        payload = json.loads(payload_text)
        if payload.get("schema") != "umi_gripper_live_v1" or payload.get("valid") is not True:
            return None, None
        angle = float(payload["angle_deg"])
        gap = float(payload["estimated_no_load_gap_mm"])
        if not np.isfinite(angle) or not np.isfinite(gap):
            return None, None
        return angle, gap
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, None


def main() -> None:
'''
    if source.count(helper_anchor) != 1:
        raise RuntimeError("viewer gripper helper anchor changed")
    source = source.replace(helper_anchor, helper, 1)

    state_old = '''    started_at = time.monotonic()
    last_fast_message_ts = None
'''
    state_new = '''    started_at = time.monotonic()
    first_corrected_received_mono = None
    last_fast_message_ts = None
'''
    if source.count(state_old) != 1:
        raise RuntimeError("viewer state anchor changed")
    source = source.replace(state_old, state_new, 1)

    count_old = '''        "image": 0,
        "imu": 0,
    }
'''
    count_new = '''        "image": 0,
        "imu": 0,
        "gripper": 0,
    }
'''
    if source.count(count_old) != 1:
        raise RuntimeError("viewer gripper count anchor changed")
    source = source.replace(count_old, count_new, 1)

    health_old = '''    correction = LoopCorrection()
    health = SlamHealthDisplay()

    def log_pose'''
    health_new = '''    correction = LoopCorrection()
    health = SlamHealthDisplay()
    viz.log_gripper_state("left_hand", None, None, time.time())

    def log_pose'''
    if source.count(health_old) != 1:
        raise RuntimeError("viewer initial gripper anchor changed")
    source = source.replace(health_old, health_new, 1)

    callback_old = '''    def on_corrected_odom(msg: Odometry) -> None:
        position, orientation = _message_pose(msg)
        correction.add_corrected(_message_key(msg), position, orientation)
        counts["backend_corrected"] += 1
        # The corrected backend is the signed-off pose.  Do not substitute the
        # noisier IMU propagation path merely to hide backend latency.
        log_pose(_message_time(msg), position, orientation)
'''
    callback_new = '''    def on_corrected_odom(msg: Odometry) -> None:
        nonlocal first_corrected_received_mono
        position, orientation = _message_pose(msg)
        correction.add_corrected(_message_key(msg), position, orientation)
        counts["backend_corrected"] += 1
        received_mono = time.monotonic()
        if first_corrected_received_mono is None:
            first_corrected_received_mono = received_mono
        # Suppress only the operator display during the known static/ZUPT
        # settling window. Raw and corrected CSV evidence remains untouched.
        if received_mono - first_corrected_received_mono < initial_settle_s:
            return
        # The corrected backend is the signed-off pose.  Do not substitute the
        # noisier IMU propagation path merely to hide backend latency.
        log_pose(_message_time(msg), position, orientation)
'''
    if source.count(callback_old) != 1:
        raise RuntimeError("viewer corrected odometry anchor changed")
    source = source.replace(callback_old, callback_new, 1)

    gripper_anchor = '''    def log_stats() -> None:
'''
    gripper_callback = '''    def on_gripper(msg: String) -> None:
        angle_deg, gap_mm = _decode_gripper_state(msg.data)
        viz.log_gripper_state("left_hand", angle_deg, gap_mm, time.time())
        counts["gripper"] += 1

    def log_stats() -> None:
'''
    if source.count(gripper_anchor) != 1:
        raise RuntimeError("viewer gripper callback anchor changed")
    source = source.replace(gripper_anchor, gripper_callback, 1)

    subscription_old = '''    node.create_subscription(ImuMsg, "/imu0", on_imu, visualization_qos)
    node.create_subscription(
        String,
        args.health_topic,
'''
    subscription_new = '''    node.create_subscription(ImuMsg, "/imu0", on_imu, visualization_qos)
    node.create_subscription(
        String, args.gripper_topic, on_gripper, visualization_qos
    )
    node.create_subscription(
        String,
        args.health_topic,
'''
    if source.count(subscription_old) != 1:
        raise RuntimeError("viewer gripper subscription anchor changed")
    source = source.replace(subscription_old, subscription_new, 1)

    startup_old = '''        f" + {args.image_topic} + /imu0；RGB目标30Hz，BEST_EFFORT latest-only"
    )
'''
    startup_new = '''        f" + {args.image_topic} + /imu0；RGB目标30Hz，BEST_EFFORT latest-only"
        f"；夹爪显示 {args.gripper_topic}"
    )
'''
    if source.count(startup_old) != 1:
        raise RuntimeError("viewer gripper startup evidence anchor changed")
    source = source.replace(startup_old, startup_new, 1)

    stop_old = '''            f'img={counts["image"]} imu={counts["imu"]}'
        )
'''
    stop_new = '''            f'img={counts["image"]} imu={counts["imu"]}'
            f' gripper={counts["gripper"]}'
        )
'''
    if source.count(stop_old) != 1:
        raise RuntimeError("viewer gripper stop evidence anchor changed")
    return source.replace(stop_old, stop_new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--viewer", type=Path, required=True)
    parser.add_argument("--visualizer", type=Path, required=True)
    args = parser.parse_args()
    args.bridge.write_text(
        patch_bridge(args.bridge.read_text(encoding="utf-8")), encoding="utf-8"
    )
    args.capture.write_text(
        patch_capture(args.capture.read_text(encoding="utf-8")), encoding="utf-8"
    )
    args.viewer.write_text(
        patch_viewer(args.viewer.read_text(encoding="utf-8")), encoding="utf-8"
    )
    args.visualizer.write_text(
        patch_visualizer(args.visualizer.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
