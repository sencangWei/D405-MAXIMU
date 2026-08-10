#!/usr/bin/env python3
"""测试 Rerun 自适应历史姿态轴 + 动态网格效果。

不需要相机/IMU, 用模拟的 6DOF 轨迹验证可视化效果。

用法:
  python scripts/test_viz_adaptive.py
"""
import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.visualizer.rerun_viz import RerunVisualizer


def _axis_angle_to_quat(axis, angle):
    """轴角 → 四元数 xyzw。"""
    axis = np.asarray(axis, dtype=float)
    axis /= np.linalg.norm(axis)
    half = angle / 2.0
    return np.array([
        axis[0] * math.sin(half),
        axis[1] * math.sin(half),
        axis[2] * math.sin(half),
        math.cos(half),
    ])


def _quat_mul(q1, q2):
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return np.array([
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ])


class DummyPose:
    def __init__(self, ts, t, q):
        self.ts = ts
        self.t = np.asarray(t, dtype=float)
        self.q = np.asarray(q, dtype=float)
        self.valid = True


def make_spiral_pose(t_total, speed=0.3, radius_growth=0.05):
    """生成一个向外螺旋的 6DOF 位姿序列, 模拟人边走边转圈。"""
    dt = 0.025  # 40Hz
    poses = []
    ts = 0.0
    while ts < t_total:
        s = ts * speed
        # 初始半径很小, 前几秒在 ±0.2m 网格内清晰可见; 后面慢慢扩展
        r = 0.1 + radius_growth * s
        x = r * math.cos(s)
        y = r * math.sin(s)
        # z 逐渐上升 + 上下起伏, 让三平面网格都能被看到
        z = 0.02 * s + 0.08 * math.sin(2 * s)

        # 姿态: 朝向运动方向 + 轻微摆动
        heading = s + math.pi / 2
        q_yaw = _axis_angle_to_quat([0, 0, 1], heading)
        q_pitch = _axis_angle_to_quat([0, 1, 0], 0.1 * math.sin(2 * s))
        q_roll = _axis_angle_to_quat([1, 0, 0], 0.05 * math.cos(3 * s))
        q = _quat_mul(q_yaw, _quat_mul(q_pitch, q_roll))

        poses.append(DummyPose(ts, [x, y, z], q))
        ts += dt
    return poses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=30.0, help="模拟轨迹长度(秒)")
    ap.add_argument("--speed", type=float, default=0.3, help="角速度")
    ap.add_argument("--no-realtime", action="store_true", help="以最快速度发送, 不按真实时间")
    ap.add_argument("--hold", type=float, default=-1.0,
                    help="结束后保持窗口打开的秒数; -1 = 一直保持(按 Ctrl+C 退出)")
    args = ap.parse_args()

    print("生成模拟轨迹...")
    poses = make_spiral_pose(args.duration, speed=args.speed)
    print(f"共 {len(poses)} 个位姿, 约 {args.duration:.1f}s")

    viz = RerunVisualizer(unit_names=["dummy"], app_id="test_adaptive_viz")

    print("发送到 Rerun... 观察: 轨迹变长后姿态轴密度和网格会自动调整")
    t0 = time.time()
    for pose in poses:
        viz.log_pose("dummy", pose)
        if not args.no_realtime:
            # 按轨迹时间真实播放
            elapsed = time.time() - t0
            target = pose.ts
            if target > elapsed:
                time.sleep(target - elapsed)
        else:
            time.sleep(0.001)

    print("完成。保持 Rerun 窗口打开, 可继续查看。按 Ctrl+C 退出。")
    if args.hold >= 0.0:
        # 自动退出, 避免后台运行时进程/viewer 泄漏
        time.sleep(args.hold)
        return
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
