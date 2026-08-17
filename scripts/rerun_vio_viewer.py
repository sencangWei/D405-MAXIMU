#!/usr/bin/env python3
"""Ubuntu 本地 Rerun 可视化: 订阅 VIO odometry + /cam0/image_raw。

用法:
  python3 scripts/rerun_vio_viewer.py [--headless] [--odom-topic /odometry_rect]
"""
import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _quat_rotate(vector: np.ndarray, quaternion: np.ndarray) -> np.ndarray:
    """Rotate a 3D vector by an xyzw quaternion without extra dependencies."""
    q = np.asarray(quaternion, dtype=np.float64)
    norm = np.linalg.norm(q)
    if norm < 1e-12:
        return np.asarray(vector, dtype=np.float64)
    q = q / norm
    xyz = q[:3]
    t = 2.0 * np.cross(xyz, vector)
    return np.asarray(vector, dtype=np.float64) + q[3] * t + np.cross(xyz, t)


def _pose_axis_strips(position: np.ndarray, quaternion: np.ndarray, length: float = 0.05) -> np.ndarray:
    """Return red/green/blue XYZ axis line strips for one historical pose."""
    origin = np.asarray(position, dtype=np.float64)
    basis = np.eye(3, dtype=np.float64) * length
    ends = np.array([origin + _quat_rotate(axis, quaternion) for axis in basis])
    return np.stack([np.stack([origin, end]) for end in ends]).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="保存 .rrd 文件而非实时显示")
    ap.add_argument("--odom-topic", default="/ov_msckf/odomimu")
    args = ap.parse_args()

    import rerun as rr
    import rerun.blueprint as rrb

    if args.headless:
        rr.init("ego_vio", spawn=False)
        print("[viewer] headless mode, saving to ego_vio.rrd")
    else:
        rr.init("ego_vio", spawn=True)

    # 固定监督布局：中央显示轨迹，右侧显示当前相机画面。
    # 不依赖 Rerun 自动布局，避免每次启动窗口位置和内容不一致。
    rr.send_blueprint(
        rrb.Blueprint(
            rrb.Horizontal(
                rrb.Spatial3DView(
                    origin="/world",
                    contents=["/world/traj", "/world/cam", "/world/pose_axes_history"],
                    name="三维位姿 / 轨迹",
                ),
                rrb.Vertical(
                    rrb.Spatial2DView(
                        origin="/world/cam/image",
                        contents=["/world/cam/image"],
                        name="相机画面",
                    ),
                    rrb.TimeSeriesView(
                        origin="/imu",
                        contents=["/imu/**"],
                        name="IMU（加速度 / 角速度）",
                    ),
                    row_shares=[2.0, 1.0],
                ),
                column_shares=[3.0, 1.0],
            ),
            collapse_panels=True,
        ),
    )

    rr.log("world", rr.ViewCoordinates.RDF, static=True)

    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Image as ImageMsg
    from sensor_msgs.msg import Imu as ImuMsg
    from nav_msgs.msg import Odometry

    rclpy.init(args=None)
    node = Node("rerun_vio_viewer")

    poses = [(0.0, 0.0, 0.0)]
    pose_axes = []
    n_pose = 0
    n_img = 0
    n_imu = 0
    lock = threading.Lock()

    def on_odom(msg: Odometry):
        nonlocal n_pose
        stamp = msg.header.stamp
        ts = stamp.sec + stamp.nanosec * 1e-9
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation

        rr.set_time("time", timestamp=ts)
        rr.log(
            "world/cam",
            rr.Transform3D(
                translation=[p.x, p.y, p.z],
                rotation=rr.Quaternion(xyzw=[q.x, q.y, q.z, q.w]),
            ),
        )

        axes_snapshot = None
        with lock:
            poses.append((p.x, p.y, p.z))
            if len(poses) > 10000:
                del poses[:-5000]
        n_pose += 1
        # 10 Hz historical pose axes: the same red/green/blue 6DOF effect as
        # the reference animation, bounded so Rerun remains responsive.
        if n_pose % 40 == 0:
            with lock:
                pose_axes.extend(_pose_axis_strips(
                    np.array([p.x, p.y, p.z]),
                    np.array([q.x, q.y, q.z, q.w]),
                ).tolist())
                if len(pose_axes) > 2400:
                    del pose_axes[:-2400]
                axes_snapshot = np.asarray(pose_axes, dtype=np.float32)
        if axes_snapshot is not None:
            n_axes = len(axes_snapshot)
            colors = np.tile(
                np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], dtype=np.uint8),
                (n_axes // 3, 1),
            )
            rr.log(
                "world/pose_axes_history",
                rr.LineStrips3D(
                    axes_snapshot,
                    radii=np.full(n_axes, 0.0015, dtype=np.float32),
                    colors=colors,
                ),
            )

    def on_image(msg: ImageMsg):
        nonlocal n_img
        try:
            encoding = msg.encoding.lower()
            if encoding in ("mono8", "8uc1"):
                raw = np.frombuffer(msg.data, dtype=np.uint8)
                cv_img = raw.reshape(msg.height, msg.step)[:, :msg.width]
            elif encoding in ("bgr8", "rgb8"):
                raw = np.frombuffer(msg.data, dtype=np.uint8)
                cv_img = raw.reshape(msg.height, msg.step)[:, :msg.width * 3]
                cv_img = cv_img.reshape(msg.height, msg.width, 3)
            else:
                return
        except Exception:
            return

        stamp = msg.header.stamp
        ts = stamp.sec + stamp.nanosec * 1e-9
        rr.set_time("time", timestamp=ts)
        rr.log("world/cam/image", rr.Image(cv_img))

        n_img += 1
        if n_img % 30 == 0:
            with lock:
                pts = np.array(poses[-500:], dtype=np.float32).reshape(1, -1, 3)
            rr.log("world/traj", rr.LineStrips3D(pts))

    def on_imu(msg: ImuMsg):
        """Plot a bounded-rate view of the full-rate IMU stream."""
        nonlocal n_imu
        n_imu += 1
        if n_imu % 8 != 0:  # 400 Hz input -> 50 Hz plot updates
            return

        stamp = msg.header.stamp
        ts = stamp.sec + stamp.nanosec * 1e-9
        rr.set_time("time", timestamp=ts)
        accel = msg.linear_acceleration
        gyro = msg.angular_velocity
        values = {
            "imu/accel/x": accel.x,
            "imu/accel/y": accel.y,
            "imu/accel/z": accel.z,
            "imu/gyro/x": gyro.x,
            "imu/gyro/y": gyro.y,
            "imu/gyro/z": gyro.z,
        }
        for path, value in values.items():
            rr.log(path, rr.Scalars([float(value)]))

    node.create_subscription(Odometry, args.odom_topic, on_odom, 10)
    node.create_subscription(ImageMsg, "/cam0/image_raw", on_image, 10)
    node.create_subscription(ImuMsg, "/imu0", on_imu, 100)
    print(f"[viewer] 订阅 {args.odom_topic} + /cam0/image_raw + /imu0 (50Hz IMU曲线)")

    # Spin in background thread
    spin_thread = threading.Thread(target=lambda: rclpy.spin(node), daemon=True)
    spin_thread.start()

    try:
        while rclpy.ok():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
        print(f"[viewer] 停止, pose={n_pose} img={n_img} imu={n_imu}")


if __name__ == "__main__":
    main()
