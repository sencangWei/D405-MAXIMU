#!/usr/bin/env python3
"""静置 IMU 数据采集 (Allan 方差分析用)。

在完全静止的桌面上运行, 记录原始 IMU 到 imu.bin。
用于晚上睡前定时启动, 早上起来分析噪声参数。

用法:
  # 录 4 小时 (默认)
  python3 scripts/record_imu_static.py --duration 14400

  # 录 30 分钟快速测试
  python3 scripts/record_imu_static.py --duration 1800 --out ~/imu_allan_test

输出:
  <out_dir>/imu.bin       原始数据 (Allan 分析直接读)
  <out_dir>/imu_ts.csv    时间戳
  <out_dir>/summary.json  统计
"""
from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.imu_reader import ImuReader
from ego_vio.recorder.recorder import UnitRecorder

IMU_PACK_FMT = "<dI7f"
IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="静置 IMU 采集 (Allan 分析用)")
    p.add_argument("--duration", type=float, default=14400.0,
                   help="录制时长(秒), 默认4小时")
    p.add_argument("--out", type=Path, default=Path.home() / "imu_allan_test",
                   help="输出目录 (默认 ~/imu_allan_test)")
    p.add_argument("--imu-port",
                   default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00")
    p.add_argument("--imu-baud", type=int, default=921600)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    recorder = UnitRecorder("external_imu", args.out, save_depth=False, max_queue=8000)
    imu = ImuReader(
        args.imu_port,
        baud=args.imu_baud,
        warmup_frames=500,
        on_sample=recorder.put_imu,
        name="allan_imu",
    )

    if not imu.start():
        print(f"[ERROR] 无法打开 IMU 串口: {args.imu_port}")
        return 1
    recorder.start()

    print(f"[静置采集] 开始录制 {args.duration:.0f}s ({args.duration/3600:.1f}h)")
    print(f"[静置采集] 输出: {args.out}")
    print(f"[静置采集] 重要: 请勿触碰设备!")
    print(f"[静置采集] 日志: /tmp/imu_static.log")

    t0 = time.monotonic()
    last_n = 0
    try:
        while time.monotonic() - t0 < args.duration:
            time.sleep(10)
            elapsed = time.monotonic() - t0
            st = imu.stats()
            n = st["frames_ok"]
            rate = (n - last_n) / 10.0
            last_n = n
            print(f"  [{elapsed/60:6.1f}min] 样本 {n}  ({rate:.1f}Hz) "
                  f"bad={st['frames_bad']} drop={st['dropped_frames']} "
                  f"reset={st['counter_resets']}", flush=True)
    except KeyboardInterrupt:
        print("\n[静置采集] 手动中断")

    duration = time.monotonic() - t0
    imu.stop()
    recorder.stop()
    st = imu.stats()

    # 生成 summary
    summary = {
        "duration_s": round(duration, 2),
        "frames_ok": st["frames_ok"],
        "rate_hz": round(st["frames_ok"] / duration, 2) if duration > 0 else 0,
        "frames_bad": st["frames_bad"],
        "dropped_frames": st["dropped_frames"],
        "counter_resets": st["counter_resets"],
        "output": str(args.out),
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 采集完成 =====")
    print(f"时长: {duration/60:.1f} min")
    print(f"样本: {st['frames_ok']}  ({st['frames_ok']/duration:.1f}Hz)")
    print(f"校验错误: {st['frames_bad']}, 丢帧: {st['dropped_frames']}, 复位: {st['counter_resets']}")
    print(f"数据: {args.out}/imu.bin")
    print("\n分析噪声:")
    print(f"  python3 scripts/allan_variance_imu.py --bin {args.out}/imu.bin --out ~/allan_result.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
