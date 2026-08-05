"""验证 tracking_entity 下重发 blueprint 改 position 是否改变相机距离。

流程: 固定场景 → 初始 blueprint(distance=D) → 4s 后重发 blueprint(distance=3D)
如果画面明显拉远, 说明 zoom 重发有效; 不变则说明 tracking 锁定距离。
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.visualizer.rerun_viz import RerunVisualizer


class P:
    def __init__(self, ts, t):
        self.ts = ts
        self.t = np.asarray(t, dtype=float)
        self.q = np.array([0.0, 0.0, 0.0, 1.0])
        self.valid = True


def main():
    viz = RerunVisualizer(unit_names=["dummy"], app_id="test_zoom_probe")

    # 先铺一个 ~0.6m 的静态场景
    for i in range(60):
        ts = i * 0.025
        viz.log_pose("dummy", P(ts, [0.3 * np.cos(ts), 0.3 * np.sin(ts), 0.1]))
    print("scene laid out, initial fit active")
    time.sleep(10.0)

    # 直接三倍距离重发 blueprint —— 绕过变焦阈值, 强制试一次
    print("resending blueprint with 3x distance...")
    viz._camera_distance = viz._camera_distance * 3.0
    viz._send_blueprint(viz._camera_center, viz._camera_distance)
    time.sleep(4.0)
    print("done")


if __name__ == "__main__":
    main()
