#!/usr/bin/env python3
"""Display live manual UMI gripper state from the STM32 combined stream."""

from __future__ import annotations

import argparse
import signal
import threading
import time
from pathlib import Path

from product_calibration.gripper_encoder import (
    GripperEncoderCollector,
    JsonlSampleRecorder,
    resolve_serial_port,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE = ROOT / "product_calibration/umi_manual_gripper_20260824.yaml"


def parser() -> argparse.ArgumentParser:
    item = argparse.ArgumentParser(description="手动UMI夹爪编码器实时状态")
    item.add_argument("--port", help="默认自动选择唯一/dev/serial/by-id设备")
    item.add_argument("--baud", type=int, default=921600)
    item.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    item.add_argument("--display-hz", type=float, default=10.0)
    item.add_argument("--jsonl", type=Path, help="可选：按400Hz保存App接口JSONL")
    return item


def main() -> int:
    args = parser().parse_args()
    if args.display_hz <= 0.0:
        raise SystemExit("--display-hz必须大于0")
    try:
        port = resolve_serial_port(args.port)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    period = 1.0 / args.display_hz
    recorder = JsonlSampleRecorder(args.jsonl) if args.jsonl else None
    collector = GripperEncoderCollector(
        port=port,
        baud=args.baud,
        profile=args.profile,
        on_sample=recorder.write if recorder else None,
    )
    print(f"连接：{port} @ {args.baud}")
    print("距离为手动夹爪无载估算，不代表受力物体真实尺寸；Ctrl+C停止。")
    if recorder:
        recorder.open()
        print(f"App JSONL：{recorder.path}")
    stop_requested = threading.Event()
    previous_sigint = signal.signal(signal.SIGINT, lambda *_args: stop_requested.set())
    previous_sigterm = signal.signal(signal.SIGTERM, lambda *_args: stop_requested.set())
    try:
        collector.start()
        while not stop_requested.wait(period):
            sample = collector.latest()
            if sample is None:
                continue
            if not sample.valid:
                print(f"raw={sample.raw_count:5d} angle={sample.angle_deg:8.3f}° INVALID")
                continue
            print(
                f"raw={sample.raw_count:5d} angle={sample.angle_deg:8.3f}° "
                f"dir={sample.direction:7s} close={100.0 * sample.closure_ratio:6.2f}% "
                f"single={sample.single_jaw_travel_mm:6.2f}mm "
                f"dual={sample.dual_closing_distance_mm:6.2f}mm "
                f"gap~={sample.estimated_no_load_gap_mm:6.2f}±"
                f"{sample.no_load_uncertainty_mm:.2f}mm",
                flush=True,
            )
    finally:
        collector.stop()
        if recorder:
            recorder.close()
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
    health = collector.health()
    print(
        f"停止：frames={health.frames} invalid_encoder={health.invalid_frames} "
        f"sequence_gaps={health.sequence_gaps} crc_errors={health.crc_errors} "
        f"app_queue_drops={health.callback_queue_drops}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
