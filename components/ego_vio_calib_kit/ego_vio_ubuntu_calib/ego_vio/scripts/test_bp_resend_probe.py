"""决定性验证: make_default=True 的 blueprint 重发是否能生效(改 position)。

背景: 实测 make_active=True/make_default=False 的重发是 no-op(帧级像素不变),
tracked Pinhole 接管后 Transform3D 更新也被忽略。若 make_default=True 重发
能改变取景, 就有了可控的变焦通道(低频/平滑重发), 不再依赖 viewer 自动适配。

流程: 静态螺旋 → 初始 blueprint(d≈0.9) → 3s 后重发 d=2.0 → 3s 后重发 d=0.45。
画面若随两次重发变化 => 通道可用。
"""
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rerun.blueprint as rrb

from ego_vio.visualizer.rerun_viz import RerunVisualizer


class P:
    def __init__(self, ts, t):
        self.ts = ts
        self.t = np.asarray(t, dtype=float)
        self.q = np.array([0.0, 0.0, 0.0, 1.0])
        self.valid = True


def main():
    viz = RerunVisualizer(unit_names=["dummy"], app_id="test_bp_resend")

    for i in range(80):
        ts = i * 0.025
        viz.log_pose(
            "dummy",
            P(ts, [0.35 * math.cos(ts * 2), 0.35 * math.sin(ts * 2),
                   0.1 + 0.05 * math.sin(ts)]),
        )
    center = (viz._scene_bbox_min + viz._scene_bbox_max) / 2.0
    print(f"scene center = {center.round(3)}", flush=True)

    print("initial blueprint (production settings)", flush=True)
    time.sleep(3.0)

    for d in (2.0, 0.45):
        print(f"resend blueprint with distance={d}, make_default=True", flush=True)
        viz._send_blueprint(center, d, make_default=True)
        time.sleep(3.0)

    print("done, holding 2s", flush=True)
    time.sleep(2.0)


if __name__ == "__main__":
    main()
