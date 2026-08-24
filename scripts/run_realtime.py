#!/usr/bin/env python3
"""双手实时 VIO + Rerun 可视化(给客户展示) + 三路录制。

用法:
  python scripts/run_realtime.py
  python scripts/run_realtime.py --no-record       # 不录，只看可视化
  python scripts/run_realtime.py --config config/devices_product_live_stm32.yaml
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import load_config
from ego_vio.runtime import Runtime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config",
        default=None,
        help="设备配置；默认使用正式 devices_product_live_stm32.yaml",
    )
    ap.add_argument("--session", default=None, help="录制会话名(默认时间戳)")
    ap.add_argument("--no-record", action="store_true", help="不录制，只跑可视化")
    ap.add_argument("--no-viz", action="store_true", help="不启动 Rerun")
    ap.add_argument("--duration-s", type=float, default=0.0, help="运行秒数；0表示直到Ctrl-C")
    ap.add_argument("--backend", default=None, choices=["stub", "openvins_socket", "openvins_ros2", "vins_fusion_ros2", "orbslam3_ros2"],
                    help="强制指定 VIO 后端")
    args = ap.parse_args()

    cfg = load_config(args.config)
    print(f"单元: {[u.name for u in cfg.units]}")
    print(f"实时 VIO 单元: {[u.name for u in cfg.realtime_units()]}")

    # 命令行强制后端
    if args.backend:
        for u in cfg.units:
            if u.role == "realtime_vio":
                u.vio.backend = args.backend

    rt = Runtime(cfg, session_name=args.session)
    rt.setup(record=not args.no_record, visualize=not args.no_viz)
    rt.start()
    try:
        rt.run(duration_s=args.duration_s)
    finally:
        rt.stop()


if __name__ == "__main__":
    main()
