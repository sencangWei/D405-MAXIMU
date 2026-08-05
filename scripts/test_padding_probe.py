"""验证: 自动取景的场景 bounds 是否计入 radii=0 的隐形点。

若是, 则可用隐形 padding 点把轴标签的伸出量纳入自动取景,
无需任何 blueprint 重发。
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
    viz = RerunVisualizer(unit_names=["dummy"], app_id="test_padding_probe")

    for i in range(60):
        ts = i * 0.025
        viz.log_pose("dummy", P(ts, [0.3 * np.cos(ts), 0.3 * np.sin(ts), 0.1]))
    print("scene laid out (no padding)")
    time.sleep(8.0)

    bbox_min = viz._scene_bbox_min
    bbox_max = viz._scene_bbox_max
    step = viz._scene_step
    pad = step * 3.0  # 故意放大, 效果肉眼可辨
    corners = [
        [bbox_min[0] - pad, bbox_min[1] - pad, bbox_min[2] - pad * 0.3],
        [bbox_max[0] + pad, bbox_min[1] - pad, bbox_min[2] - pad * 0.3],
    ]
    rr.log(
        "world/camera_fit_padding",
        rr.Points3D(corners, radii=0.0, colors=[255, 255, 0]),
    )
    print(f"padding points logged (pad={pad:.3f}m, radii=0)")
    time.sleep(6.0)
    print("done")


if __name__ == "__main__":
    main()
