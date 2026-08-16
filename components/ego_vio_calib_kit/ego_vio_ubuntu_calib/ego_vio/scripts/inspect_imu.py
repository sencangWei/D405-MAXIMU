#!/usr/bin/env python3
"""IMU 调试: 连一个串口，打印解析结果 + 统计帧率/丢帧。

硬件接好后先用这个确认:
  - 串口通不通
  - 帧能不能解析(校验通过)
  - 400Hz 稳不稳 / 掉不掉帧 / counter 连不连续

用法:
  python scripts/inspect_imu.py --port /dev/ttyUSB0
  python scripts/inspect_imu.py --port COM5 --secs 10
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.imu.imu_reader import ImuReader


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="串口(Linux: /dev/ttyUSB0, Win: COM5)")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--secs", type=float, default=0, help="测试秒数(0=手动 Ctrl-C)")
    args = ap.parse_args()

    printed = {"n": 0}

    def on_sample(s):
        if printed["n"] < 8:
            print(f"counter={s.counter:3d} gx={s.gx:8.3f} gy={s.gy:8.3f} gz={s.gz:8.3f} "
                  f"ax={s.ax:7.3f} ay={s.ay:7.3f} az={s.az:7.3f} T={s.temp:.1f}")
            printed["n"] += 1

    r = ImuReader(port=args.port, baud=args.baud, on_sample=on_sample, name="inspect")
    if not r.start():
        print("串口打开失败，检查端口/驱动。")
        return 1

    print(f"读 {args.port} @ {args.baud} ... Ctrl-C 停止")
    print("(前 8 帧打印数值,之后每秒打印一行心跳状态)")
    t0 = time.monotonic()
    last_n = 0
    try:
        while True:
            time.sleep(1.0)
            dur = time.monotonic() - t0
            if args.secs > 0 and dur >= args.secs:
                break
            # 心跳: 每秒打印一次,证明还在收数据
            n = r.frames_ok
            rate = n - last_n
            last_n = n
            st = r.stats()
            print(f"  [{dur:5.1f}s] 累计 {n} 帧 (+{rate}/s) drop={st['dropped_frames']} bad={st['frames_bad']}")
    except KeyboardInterrupt:
        pass
    r.stop()

    dur = time.monotonic() - t0
    st = r.stats()
    # 帧率按 总帧数/时长 算(与心跳同口径); dt 统计受 USB 批量到达影响会虚高
    rate = st["frames_ok"] / dur if dur > 0 else 0.0
    print("\n========== IMU 诊断 ==========")
    print(f"运行: {dur:.1f}s")
    print(f"有效帧: {st['frames_ok']}  (期望 ~{int(dur*400)})")
    print(f"实际帧率: {rate:.1f} Hz (目标 400)")
    print(f"校验错误: {st['frames_bad']}")
    print(f"重同步: {st['resyncs']}")
    print(f"丢帧(counter 跳变): {st['dropped_frames']}")
    print(f"帧间隔: {st['dt_min_ms']:.2f}~{st['dt_max_ms']:.2f} ms, 抖动 {st['dt_jitter_ms']:.2f} ms")
    print("判定: rate≈400 且 dropped=0 且 bad=0 → OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
