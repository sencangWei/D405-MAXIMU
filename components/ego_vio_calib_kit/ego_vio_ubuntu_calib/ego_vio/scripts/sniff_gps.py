#!/usr/bin/env python3
"""监听 GPS 串口,打印收到的原始数据(NMEA 应该是 $GN.. / $GP.. 开头的文本)。

用法:
  python scripts/sniff_gps.py --port COM9 --baud 9600 --secs 10
"""
import argparse
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--secs", type=float, default=10)
    args = ap.parse_args()

    try:
        import serial
    except ImportError:
        print("pip install pyserial")
        return 1

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except Exception as e:
        print(f"打开 {args.port} 失败: {e}")
        return 1

    print(f"监听 {args.port} @ {args.baud}, {args.secs:.0f}s ...")
    t_end = time.monotonic() + args.secs
    total = 0
    line_buf = bytearray()
    n_lines = 0
    while time.monotonic() < t_end:
        data = ser.read(256)
        if not data:
            continue
        total += len(data)
        line_buf.extend(data)
        while b"\n" in line_buf:
            idx = line_buf.index(b"\n")
            line = bytes(line_buf[:idx]).decode("ascii", errors="replace").strip()
            del line_buf[: idx + 1]
            if line:
                n_lines += 1
                if n_lines <= 20:
                    print(f"  {line[:80]}")

    print("=" * 40)
    if total == 0:
        print(f"结果: 0 字节 —— GPS 完全没在发")
        print("  → 对调 TX/RX 两根线再试")
    elif n_lines > 0:
        print(f"结果: {total} 字节, {n_lines} 行 —— NMEA 正常!")
        print(f"  → 波特率就是 {args.baud},记下它")
    else:
        print(f"结果: {total} 字节但不是文本 —— 波特率不对,换 38400/115200 试")
    ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
