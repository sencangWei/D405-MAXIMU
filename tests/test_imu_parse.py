"""KT-EX9-2 IMU 帧解析单元测试(不依赖硬件)。"""
import struct
import sys
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.imu.imu_reader import (
    FRAME_SIZE, HEADER0, HEADER1, EXPECTED_LEN,
    ImuReader, verify_checksum, parse_frame, find_frames,
)


def make_frame(gx=1.0, gy=2.0, gz=3.0, ax=0.0, ay=0.0, az=1.0, temp=25.0, counter=1):
    buf = bytearray(FRAME_SIZE)
    buf[0] = HEADER0
    buf[1] = HEADER1
    buf[2] = EXPECTED_LEN
    buf[3] = 0x01
    struct.pack_into("<7f", buf, 4, gx, gy, gz, ax, ay, az, temp)
    struct.pack_into("<I", buf, 32, counter)
    # 校验和: 字节[0..35] 累加低 8 位(含帧头)
    buf[36] = sum(buf[0:36]) & 0xFF
    return bytes(buf)


def test_checksum_valid():
    assert verify_checksum(make_frame()) is True


def test_checksum_bad():
    f = bytearray(make_frame())
    f[10] ^= 0xFF     # 破坏一个数据字节
    assert verify_checksum(bytes(f)) is False


def test_parse_values():
    f = make_frame(gx=10.5, gy=-20.0, gz=0.0, ax=0.1, ay=-0.2, az=9.8, temp=42.0, counter=123)
    # parse_frame 用 monotonic 时间戳，值不测
    s = parse_frame(f)
    assert s is not None
    assert abs(s.gx - 10.5) < 1e-4
    assert abs(s.gy - (-20.0)) < 1e-4
    assert abs(s.az - 9.8) < 1e-3     # float32 精度
    assert abs(s.temp - 42.0) < 1e-3
    assert s.counter == 123


def test_find_frames_in_stream():
    f1 = make_frame(counter=1)
    f2 = make_frame(counter=2)
    junk = b"\x00\x01\x02"
    stream = junk + f1 + junk + f2
    frames, tail, stats = find_frames(stream)
    assert len(frames) == 2
    assert stats["bad_checksum"] == 0


def test_reject_garbage_header():
    # header 不对 → parse_frame 拒绝
    bad_header = bytes([0x00] * FRAME_SIZE)
    assert parse_frame(bad_header) is None
    # 校验和明确不对 → verify 拒绝
    bad_chk = bytearray(make_frame())
    bad_chk[36] ^= 0xFF
    assert verify_checksum(bytes(bad_chk)) is False


def test_reader_does_not_publish_warmup_frames():
    received = []
    reader = ImuReader("unused", on_sample=received.append, warmup_frames=2)

    reader._handle(make_frame(counter=1))
    reader._handle(make_frame(counter=2))
    reader._handle(make_frame(counter=3))

    assert [sample.counter for sample in received] == [3]


def test_reader_uses_continuous_host_clock_across_device_counter_resets():
    received = []
    reader = ImuReader("unused", on_sample=received.append, warmup_frames=0)
    counters = list(range(100, 130)) + list(range(1, 31))

    for counter in counters:
        reader._handle(make_frame(counter=counter))

    assert reader._clock_counter == len(counters)
    assert reader.counter_resets == 1
    assert np.all(np.diff([sample.ts for sample in received]) > 0.0)


def test_reader_preserves_small_forward_packet_gap_in_virtual_counter():
    reader = ImuReader("unused", warmup_frames=0)

    reader._handle(make_frame(counter=10))
    reader._handle(make_frame(counter=12))

    assert reader._clock_counter == 3


def test_reader_counts_repeated_one_as_one_reset_and_stalls():
    reader = ImuReader("unused", warmup_frames=0)

    for counter in (398, 399, 1, 1, 1, 2):
        reader._handle(make_frame(counter=counter))

    assert reader.counter_resets == 1
    assert reader.counter_stalls == 2
    assert reader.dropped_frames == 0
    assert reader._clock_counter == 6


def test_reader_reconnect_keeps_virtual_counter_and_reanchors_clock(monkeypatch):
    opened = []

    class FakeSerial:
        def __init__(self, port, baud, timeout):
            self.port = port
            self.baud = baud
            self.timeout = timeout
            self.closed = False
            opened.append(self)

        def reset_input_buffer(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=FakeSerial))
    reader = ImuReader("/dev/serial/by-id/test", warmup_frames=0)
    assert reader._open_port() is True
    reader._clock_counter = 1234
    reader._ts_fitter.feed(reader._clock_counter, 10.0)

    reader._disconnect_serial("test")
    assert opened[0].closed is True
    assert reader._ser is None
    assert reader._try_reconnect() is True

    assert len(opened) == 2
    assert reader.serial_reconnects == 1
    assert reader._clock_counter == 1234
    assert reader._ts_fitter._last_output_ts is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name}: OK")
    print("test_imu_parse: ALL PASSED")
