"""验证: tracking_entity 指向一个 Pinhole 相机实体时, 是否"接管视角"。

如果是, 那么我们只要用 rr.Transform3D 更新这个相机实体的位姿,
就能纯 entity 更新地控制取景(平移+变焦), 完全不用重发 blueprint。

流程: 静态场景 → 用 world/eye 作为 tracking_entity → 10s 时把 eye 拉远 2 倍,
看画面是否跟着拉远。
"""
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
    # 列: x=right, y=down, z=fwd
    return np.column_stack([right, down, fwd])


class P:
    def __init__(self, ts, t):
        self.ts = ts
        self.t = np.asarray(t, dtype=float)
        self.q = np.array([0.0, 0.0, 0.0, 1.0])
        self.valid = True


def main():
    viz = RerunVisualizer(unit_names=["dummy"], app_id="test_eye_track")

    # 铺一个 ~0.6m 的静态场景
    for i in range(60):
        ts = i * 0.025
        viz.log_pose("dummy", P(ts, [0.3 * np.cos(ts), 0.3 * np.sin(ts), 0.1]))

    center = (viz._scene_bbox_min + viz._scene_bbox_max) / 2.0

    # world/eye: Pinhole 相机实体
    rr.log("world/eye", rr.Pinhole(image_from_camera=600.0, resolution=[640, 480]))

    direction = np.array([0.62, -0.72, 0.62])
    direction /= np.linalg.norm(direction)

    def set_eye(distance):
        eye = center + direction * distance
        mat3 = look_at_mat3(eye, center)
        rr.log(
            "world/eye",
            rr.Transform3D(translation=eye.tolist(), mat3x3=mat3.tolist()),
        )

    # 用 world/eye 做 tracking_entity 重发一次 blueprint
    d0 = 1.0
    set_eye(d0)
    blueprint = rrb.Blueprint(
        rrb.Horizontal(
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
            rrb.Vertical(rrb.TextLogView(origin="stats", name="stats")),
            column_shares=[0.7, 0.3],
        ),
        collapse_panels=True,
    )
    rr.send_blueprint(blueprint, make_active=True, make_default=False)
    print(f"tracking world/eye at distance {d0}")
    time.sleep(10.0)

    print("moving eye to 2x distance via Transform3D (no blueprint)...")
    set_eye(d0 * 2.0)
    time.sleep(5.0)
    print("done")


if __name__ == "__main__":
    main()
