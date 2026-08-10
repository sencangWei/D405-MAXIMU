#!/usr/bin/env python3
"""Ubuntu 本地 Rerun 可视化: 订阅 /ov_msckf/odomimu + /cam0/image_raw。

用法:
  python3 scripts/rerun_vio_viewer.py [--headless]
"""
import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="保存 .rrd 文件而非实时显示")
    args = ap.parse_args()

    import rerun as rr

    if args.headless:
        rr.init("ego_vio", spawn=False)
        print("[viewer] headless mode, saving to ego_vio.rrd")
    else:
        rr.init("ego_vio", spawn=True)

    rr.log("world", rr.ViewCoordinates.RDF, static=True)

    # ROS2 setup
    import sys as _sys
    _ros_py = "/opt/ros/jazzy/lib/python3.12/site-packages"
    if _ros_py not in _sys.path:
        _sys.path.insert(0, _ros_py)

    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Image as ImageMsg
    from nav_msgs.msg import Odometry
    from cv_bridge import CvBridge

    rclpy.init(args=None)
    node = Node("rerun_vio_viewer")
    bridge = CvBridge()

    poses = [(0.0, 0.0, 0.0)]
    n_pose = 0
    n_img = 0
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

        with lock:
            poses.append((p.x, p.y, p.z))
            if len(poses) > 10000:
                del poses[:-5000]
        n_pose += 1

    def on_image(msg: ImageMsg):
        nonlocal n_img
        try:
            cv_img = bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
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

    node.create_subscription(Odometry, "/ov_msckf/odomimu", on_odom, 10)
    node.create_subscription(ImageMsg, "/cam0/image_raw", on_image, 10)
    print("[viewer] 订阅 /ov_msckf/odomimu + /cam0/image_raw")

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
        print(f"[viewer] 停止, pose={n_pose} img={n_img}")


if __name__ == "__main__":
    main()
