"""决定性验证: tracking_entity 指向 Pinhole 相机实体时,
持续的 Transform3D 更新是否持续接管视角(上一轮的"只接管一次"结论存疑)。

流程: 静态螺旋场景 → blueprint tracking world/eye →
每 0.4s 把 eye 距离 ×1.12, 共 20 步(0.5m → 4.8m, 效果不可能看错)。
若画面持续拉远 => 可以用纯 entity 更新完全控制机位(平移+变焦),
不再需要 viewer 的自动适配, 也不用重发 blueprint。
"""
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rerun as rr
import rerun.blueprint as rrb

from ego_vio.visualizer.rerun_viz import RerunVisualizer


def look_at_mat3(eye, center, up=(0.0, 0.0, 1.0)):
    """Rerun pinhole 约定: x 右, y 下, z 前。返回列向量为相机轴的 3x3。"""
    eye = np.asarray(eye, dtype=float)
    center = np.asarray(center, dtype=float)
    up = np.asarray(up, dtype=float)
    fwd = center - eye
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    return np.column_stack([right, down, fwd])


class P:
    def __init__(self, ts, t):
        self.ts = ts
        self.t = np.asarray(t, dtype=float)
        self.q = np.array([0.0, 0.0, 0.0, 1.0])
        self.valid = True


def main():
    viz = RerunVisualizer(unit_names=["dummy"], app_id="test_cam_takeover")

    # 静态螺旋场景 (~0.8m)
    for i in range(80):
        ts = i * 0.025
        viz.log_pose(
            "dummy",
            P(ts, [0.35 * math.cos(ts * 2), 0.35 * math.sin(ts * 2),
                   0.1 + 0.05 * math.sin(ts)]),
        )
    center = (viz._scene_bbox_min + viz._scene_bbox_max) / 2.0
    print(f"scene center = {center.round(3)}", flush=True)

    direction = np.array([0.62, -0.72, 0.62])
    direction /= np.linalg.norm(direction)

    def set_eye(d):
        eye = center + direction * d
        rr.log(
            "world/eye",
            rr.Transform3D(
                translation=eye.tolist(),
                mat3x3=look_at_mat3(eye, center).tolist(),
            ),
        )
        # 顺带重发内参, 排除"内参只在首帧生效"的干扰
        rr.log("world/eye", rr.Pinhole(
            image_from_camera=900.0, resolution=[1740, 1000]))

    set_eye(0.5)
    blueprint = rrb.Blueprint(
        rrb.Spatial3DView(
            origin="world",
            name="3D Pose / Trajectory",
            line_grid=False,
            background=[40, 40, 40],
            eye_controls=rrb.EyeControls3D(
                kind=rrb.Eye3DKind.Orbital,
                tracking_entity="world/eye",
                eye_up=[0.0, 0.0, 1.0],
                spin_speed=0.0,
            ),
        ),
        collapse_panels=True,
    )
    rr.send_blueprint(blueprint, make_active=True, make_default=True)
    print("tracking world/eye at d=0.5; 3s 后开始拉远", flush=True)
    time.sleep(3.0)

    d = 0.5
    for k in range(20):
        d *= 1.12
        set_eye(d)
        print(f"step {k}: d={d:.2f}", flush=True)
        time.sleep(0.4)
    print("done, holding 3s", flush=True)
    time.sleep(3.0)


if __name__ == "__main__":
    main()
