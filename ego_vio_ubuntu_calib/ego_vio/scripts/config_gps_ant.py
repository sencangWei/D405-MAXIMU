#!/usr/bin/env python3
"""开启 u-blox NEO-M8N 的有源天线供电, 并保存到 Flash。

用法:
  python scripts/config_gps_ant.py --port COM8 --baud 9600
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


def cfg_ant() -> bytes:
    """UBX-CFG-ANT: 开启天线供电、短路/开路检测。

    swBits (U2):
      bit0 svcs = 1  启用供电开关
      bit1 scd  = 1  短路检测
      bit2 ocd  = 1  开路检测
      bit4 powPin = 1 使用专用电源引脚供电(而非 PWM)
    """
    sw_bits = 0x0017  # svcs | scd | ocd | powPin
    payload = struct.pack("<HBB", sw_bits, 0, 0)
    return ubx_frame(0x06, 0x13, payload)


def cfg_cfg_save() -> bytes:
    """UBX-CFG-CFG: 保存当前配置到 BBR/Flash。"""
    payload = struct.pack("<IIIBB", 0, 0xFFFF, 0, 0, 0)
    return ubx_frame(0x06, 0x09, payload)


def wait_ack(ser, expected_class: int, expected_id: int, timeout: float = 2.0) -> bool:
    """等待指定 msgClass/msgId 的 UBX-ACK-ACK。"""
    t_end = time.time() + timeout
    buf = bytearray()
    while time.time() < t_end:
        data = ser.read(64)
        if data:
            buf.extend(data)
            while len(buf) >= 2:
                try:
                    idx = buf.index(0xB5)
                except ValueError:
                    buf.clear()
                    break
                if len(buf) < idx + 6:
                    break
                if buf[idx + 1] != 0x62:
                    del buf[:idx + 1]
                    continue
                msg_class = buf[idx + 2]
                msg_id = buf[idx + 3]
                plen = struct.unpack_from("<H", buf, idx + 4)[0]
                if len(buf) < idx + 8 + plen:
                    break
                payload = bytes(buf[idx + 6:idx + 6 + plen])
                if msg_class == 0x05 and msg_id == 0x01:
                    if len(payload) >= 2 and payload[0] == expected_class and payload[1] == expected_id:
                        return True
                if msg_class == 0x05 and msg_id == 0x00:
                    if len(payload) >= 2 and payload[0] == expected_class and payload[1] == expected_id:
                        print(f"  !! 收到 NAK (class=0x{expected_class:02X}, id=0x{expected_id:02X})")
                        return False
                del buf[:idx + 8 + plen]
        else:
            time.sleep(0.01)
    return False


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

    print(f"开启 {args.port} @ {args.baud} 天线供电...")
    ser.write(cfg_ant())
    if wait_ack(ser, 0x06, 0x13):
        print("  CFG-ANT ACK 收到")
    else:
        print("  !! CFG-ANT 未收到 ACK")

    time.sleep(0.1)
    print("保存到 Flash...")
    ser.write(cfg_cfg_save())
    if wait_ack(ser, 0x06, 0x09):
        print("  CFG-CFG ACK 收到, 已保存")
    else:
        print("  !! CFG-CFG 未收到 ACK")

    ser.close()
    print("完成。RF_IN 接口应对外壳地有 3.3V 左右电压。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
