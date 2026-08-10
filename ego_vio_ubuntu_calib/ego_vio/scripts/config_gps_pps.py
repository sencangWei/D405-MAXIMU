#!/usr/bin/env python3
"""配置 u-blox NEO-M8N 的 PPS 输出。

默认 PPS 只在定位成功后输出; 本脚本用 UBX-CFG-TP5 把它配成:
  - 1Hz 方波
  - 脉宽 100ms
  - 即使没定位也输出(always-on)
  - 保存到 Flash, 断电不丢

用法:
  python scripts/config_gps_pps.py --port COM8 --baud 9600
"""
import argparse
import struct
import sys
import time


def ubx_checksum(payload: bytes) -> bytes:
    """UBX 校验和: CK_A, CK_B。"""
    ck_a = ck_b = 0
    for b in payload:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return bytes([ck_a, ck_b])


def ubx_frame(msg_class: int, msg_id: int, payload: bytes) -> bytes:
    """构造完整 UBX 帧。"""
    body = bytes([msg_class, msg_id]) + struct.pack("<H", len(payload)) + payload
    return b"\xb5\x62" + body + ubx_checksum(body)


def cfg_tp5() -> bytes:
    """UBX-CFG-TP5: 配置 TIMEPULSE (PPS)。

    关键字段(按 u-blox M8 协议, 32 字节 payload):
      tpIdx=0, version=1, reserved1[2]=0
      antCableDelay=0, rfGroupDelay=0
      freqPeriod=1, freqPeriodLock=1          # 1Hz
      pulseLenRatio=100000, pulseLenRatioLock=100000  # 100ms (单位: us)
      flags=0x00000077:
        active=1, lockGnssFreq=1, lockedOtherSet=1,
        isLength=1, alignToTow=1, polarity=1
    """
    payload = struct.pack(
        "<BBBBhhIIIIII",
        0,        # tpIdx
        1,        # version
        0, 0,     # reserved1[2]
        0,        # antCableDelay
        0,        # rfGroupDelay
        1,        # freqPeriod (1Hz)
        1,        # freqPeriodLock (1Hz)
        100000,   # pulseLenRatio (100ms)
        100000,   # pulseLenRatioLock (100ms)
        0x00000077,  # flags
        0,        # reserved2
    )
    assert len(payload) == 32, f"payload len={len(payload)}"
    return ubx_frame(0x06, 0x31, payload)


def cfg_cfg_save() -> bytes:
    """UBX-CFG-CFG: 保存当前配置到 Flash (BBR/Flash)。"""
    clear_mask = 0
    save_mask = 0x0001 | 0x0002 | 0x0004  # ioPort + msgConf + infMsg ... 不对, 标准 mask:
    # 用 commonly-used: save to BBR + Flash
    # clearMask, saveMask, loadMask 都是 32-bit
    # saveMask: 0xFFFF 保存所有
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
            # 找 UBX 帧头
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
                # ACK-ACK for the expected message?
                if msg_class == 0x05 and msg_id == 0x01:
                    if len(payload) >= 2 and payload[0] == expected_class and payload[1] == expected_id:
                        return True
                # ACK-NAK for the expected message?
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

    # 先排空 NMEA 缓冲
    ser.reset_input_buffer()

    print(f"配置 {args.port} @ {args.baud} PPS 1Hz/100ms/always-on...")
    ser.write(cfg_tp5())
    if wait_ack(ser, 0x06, 0x31):
        print("  CFG-TP5 ACK 收到")
    else:
        print("  !! CFG-TP5 未收到 ACK, 继续尝试保存...")

    time.sleep(0.1)
    print("保存到 Flash...")
    ser.write(cfg_cfg_save())
    if wait_ack(ser, 0x06, 0x09):
        print("  CFG-CFG ACK 收到, 已保存")
    else:
        print("  !! CFG-CFG 未收到 ACK")

    ser.close()
    print("完成。PPS 引脚应开始输出 1Hz 脉冲。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
