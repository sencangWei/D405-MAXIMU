#!/usr/bin/env python3
"""实时大字显示 IMU counter，用于复现/录制 counter 复位问题。

用法:
  python3 scripts/show_imu_counter.py
  python3 scripts/show_imu_counter.py --duration 60

按 Ctrl+C 结束。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.imu_reader import ImuReader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="实时显示 IMU counter")
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00",
    )
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--duration", type=float, default=0.0, help="0=不限时长")
    return parser.parse_args()


def clear():
    print("\033[2J\033[H", end="")


def main() -> int:
    args = parse_args()
    last_counter: int | None = None
    resets = 0
    last_reset_info = "无"
    samples = 0
    start = time.monotonic()
    last_display = 0.0

    def on_sample(s):
        nonlocal last_counter, resets, last_reset_info, samples
        samples += 1

        if last_counter is not None and s.counter < last_counter:
            resets += 1
            last_reset_info = f"{last_counter} -> {s.counter} @ {time.monotonic()-start:.2f}s"
            # 复位时额外响铃
            print("\a", end="", flush=True)

        last_counter = s.counter

        nonlocal last_display
        now = time.monotonic()
        if now - last_display >= 0.1:  # 10Hz 刷新
            last_display = now
            clear()
            elapsed = now - start
            rate = samples / elapsed if elapsed > 0 else 0.0
            print("=" * 60)
            print("         IMU COUNTER 实时监控")
            print("=" * 60)
            print()
            print(f"  当前 counter : {s.counter}")
            print(f"  运行时间     : {elapsed:7.1f} s")
            print(f"  样本数       : {samples}")
            print(f"  平均频率     : {rate:6.1f} Hz")
            print()
            print(f"  复位次数     : {resets}")
            print(f"  最近复位     : {last_reset_info}")
            print()
            print("  陀螺 (deg/s) : " + ", ".join(f"{v:+.2f}" for v in (s.gx, s.gy, s.gz)))
            print("  加速度 (g)   : " + ", ".join(f"{v:+.3f}" for v in (s.ax, s.ay, s.az)))
            print()
            print("=" * 60)
            print("  提示: 快速翻转/晃动 IMU 尝试复现 counter 复位")
            print("=" * 60)
            sys.stdout.flush()

    reader = ImuReader(
        args.port,
        baud=args.baud,
        on_sample=on_sample,
        warmup_frames=0,
        name="show_counter",
    )

    if not reader.start():
        print(f"无法打开串口: {args.port}")
        return 1

    print("启动中...")
    try:
        t0 = time.monotonic()
        while True:
            if args.duration > 0 and time.monotonic() - t0 >= args.duration:
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        clear()
        elapsed = time.monotonic() - start
        print("=" * 60)
        print("监控结束")
        print(f"  运行 {elapsed:.1f}s，样本 {samples}，复位 {resets} 次")
        print(f"  最近复位: {last_reset_info}")
        print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
