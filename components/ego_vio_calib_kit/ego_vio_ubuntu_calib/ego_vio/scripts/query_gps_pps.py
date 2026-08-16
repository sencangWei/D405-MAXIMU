#!/usr/bin/env python3
"""查询 u-blox NEO-M8N 的 PPS/TIMEPULSE 配置和模块信息。

用法:
  python scripts/query_gps_pps.py --port COM8 --baud 9600
"""
import argparse
import struct
import sys
import time


def ubx_checksum(payload: bytes) -> bytes:
    ck_a = ck_b = 0
    for b in payload:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return bytes([ck_a, ck_b])


def ubx_frame(msg_class: int, msg_id: int, payload: bytes) -> bytes:
    body = bytes([msg_class, msg_id]) + struct.pack("<H", len(payload)) + payload
    return b"\xb5\x62" + body + ubx_checksum(body)


def parse_ubx(data: bytes):
    """从字节流里找 UBX 帧, 返回 (class, id, payload) 列表。"""
    frames = []
    i = 0
    while i < len(data) - 8:
        if data[i] == 0xB5 and data[i + 1] == 0x62:
            msg_class = data[i + 2]
            msg_id = data[i + 3]
            plen = struct.unpack_from("<H", data, i + 4)[0]
            if i + 8 + plen > len(data):
                break
            payload = data[i + 6:i + 6 + plen]
            ck_a, ck_b = ubx_checksum(data[i + 2:i + 6 + plen])
            if data[i + 6 + plen] == ck_a and data[i + 7 + plen] == ck_b:
                frames.append((msg_class, msg_id, payload))
                i += 8 + plen
                continue
        i += 1
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--baud", type=int, default=9600)
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

    ser.reset_input_buffer()

    # 1) 查询 TP5 配置 (poll)
    print("查询 UBX-CFG-TP5 (TIMEPULSE 0)...")
    ser.write(ubx_frame(0x06, 0x31, b"\x00"))
    time.sleep(0.5)
    data = ser.read(4096)
    frames = parse_ubx(data)
    found = False
    for cls, mid, payload in frames:
        if cls == 0x06 and mid == 0x31:
            found = True
            if len(payload) == 32:
                tpIdx, version, _, _, antCableDelay, rfGroupDelay = struct.unpack_from("<BBHHHH", payload, 0)
                freqPeriod, freqPeriodLock, pulseLenRatio, pulseLenRatioLock, flags = struct.unpack_from("<IIIII", payload, 8)
                print(f"  tpIdx={tpIdx} version={version}")
                print(f"  freqPeriod={freqPeriod}Hz  lock={freqPeriodLock}Hz")
                print(f"  pulseLenRatio={pulseLenRatio}us  lock={pulseLenRatioLock}us")
                print(f"  flags=0x{flags:08X}")
                active = bool(flags & 0x01)
                lock_gnss = bool(flags & 0x02)
                locked_other = bool(flags & 0x04)
                is_freq = bool(flags & 0x08)
                is_length = bool(flags & 0x10)
                align_tow = bool(flags & 0x20)
                polarity = bool(flags & 0x40)
                print(f"    active={active} lockGnss={lock_gnss} lockedOther={locked_other} "
                      f"isFreq={is_freq} isLength={is_length} alignToTow={align_tow} polarity={polarity}")
            else:
                print(f"  收到 TP5 但 payload 长度={len(payload)}")
    if not found:
        print("  未收到 CFG-TP5 响应")

    # 2) 查询模块版本/固件
    print("\n查询 UBX-MON-VER...")
    ser.write(ubx_frame(0x0A, 0x04, b""))
    time.sleep(0.5)
    data = ser.read(4096)
    frames = parse_ubx(data)
    for cls, mid, payload in frames:
        if cls == 0x0A and mid == 0x04:
            text = payload.decode('ascii', errors='ignore').replace('\x00', '\n').strip()
            print(text)

    ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
