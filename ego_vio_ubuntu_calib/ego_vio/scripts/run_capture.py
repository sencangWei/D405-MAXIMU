#!/usr/bin/env python3
"""仅采集录制(三路)，不跑 VIO / 不可视化。

用于:
  - 先验证相机+IMU 不掉帧(老板关心的掉频率)
  - 头部数据录制(后处理 SLAM/点云用)
  - 标定数据采集(给 Kalibr)

用法:
  python scripts/run_capture.py
  python scripts/run_capture.py --duration 60
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import load_config
from ego_vio.runtime import Runtime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--duration", type=float, default=0, help="录制秒数(0=手动 Ctrl-C)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rt = Runtime(cfg, session_name=args.session)
    rt.setup(record=True, visualize=False)
    rt.start()

    try:
        if args.duration > 0:
            # 定时停止: 到点设 stop 标志,期间照常每 3 秒刷统计
            import threading
            print(f"录制 {args.duration}s (每 3 秒刷新统计) ...")
            timer = threading.Timer(args.duration, rt._stop_evt.set)
            timer.daemon = True
            timer.start()
        rt.run()
    except KeyboardInterrupt:
        pass
    finally:
        rt.stop()


if __name__ == "__main__":
    main()
