"""验证: blueprint 完全不带 eye_controls 时, viewer 默认相机行为
是否能持续跟上增长的场景(自动适配距离+跟随中心)。

若默认行为就是"场景长了就重新取景", 生产代码可以删掉整套 eye_controls
(也就没有 tracking 黑盒), 零重发零闪烁。
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rerun.blueprint as rrb

from ego_vio.visualizer.rerun_viz import RerunVisualizer
from test_viz_adaptive import make_spiral_pose


class NoEyeViz(RerunVisualizer):
    """和生产版一样, 只是 blueprint 不带 eye_controls(用 viewer 默认相机)。"""

    def _send_blueprint(self, center, distance, make_default=False):
        rrb_ = self._rrb
        blueprint = rrb_.Blueprint(
            rrb_.Horizontal(
                rrb_.Spatial3DView(
                    origin="world",
                    name="3D Pose / Trajectory",
                    line_grid=False,
                    background=[40, 40, 40],
                ),
                rrb_.Vertical(rrb_.TextLogView(origin="stats", name="stats")),
                column_shares=[0.75, 0.25],
            ),
            collapse_panels=True,
        )
        self.rr.send_blueprint(
            blueprint, make_active=True, make_default=make_default
        )


def main():
    viz = NoEyeViz(unit_names=["dummy"], app_id="test_no_eye")
    poses = make_spiral_pose(22.0)
    print(f"{len(poses)} poses, realtime 22s, NO eye_controls", flush=True)

    t0 = time.time()
    for pose in poses:
        viz.log_pose("dummy", pose)
        target = pose.ts
        elapsed = time.time() - t0
        if target > elapsed:
            time.sleep(target - elapsed)
    print("done", flush=True)
    time.sleep(2.0)


if __name__ == "__main__":
    main()
