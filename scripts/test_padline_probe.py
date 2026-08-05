"""验证: 透明 LineStrips 的顶点是否计入自动取景 bounds。

在标签伸出方向补 3 段透明线段(各 2 个步长), 若画面明显拉远,
说明可以用透明 padding 线把轴标签纳入自动取景。
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rerun as rr

from ego_vio.visualizer.rerun_viz import RerunVisualizer


class P:
    def __init__(self, ts, t):
        self.ts = ts
        self.t = np.asarray(t, dtype=float)
        self.q = np.array([0.0, 0.0, 0.0, 1.0])
        self.valid = True


def main():
    viz = RerunVisualizer(unit_names=["dummy"], app_id="test_padline_probe")

    for i in range(60):
        ts = i * 0.025
        viz.log_pose("dummy", P(ts, [0.3 * np.cos(ts), 0.3 * np.sin(ts), 0.1]))
    print("scene laid out (no padding)", flush=True)
    time.sleep(6.0)

    p0 = viz._scene_bbox_min
    p1 = viz._scene_bbox_max
    pad = viz._scene_step * 2.0
    segs = [
        [[p0[0] - pad, p0[1], p0[2]], [p0[0], p0[1], p0[2]]],   # Z 标题侧 x-
        [[p1[0], p0[1], p0[2]], [p1[0] + pad, p0[1], p0[2]]],   # Y 标题侧 x+
        [[p0[0], p0[1] - pad, p0[2]], [p0[0], p0[1], p0[2]]],   # X 标题侧 y-
    ]
    rr.log(
        "world/bounds_pad",
        rr.LineStrips3D(segs, colors=[[0, 0, 0, 0]], radii=0.001),
    )
    print(f"transparent pad lines logged (pad={pad:.3f}m)", flush=True)
    time.sleep(20.0)
    print("done", flush=True)


if __name__ == "__main__":
    main()
