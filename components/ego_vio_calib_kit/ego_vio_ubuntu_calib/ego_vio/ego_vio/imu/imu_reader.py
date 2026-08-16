"""KT-EX9-2 军工 IMU UART 读取 + 解析。

从 C 版(rdk_x5_capture/src/imu_reader.c)移植，去掉 GPIO PPS 捕获
(小电脑没有 GPIO)，改为: PPS 接 IMU DIO2 让 counter 规整，系统时钟打时间戳。

线程模型:
  - 读线程: serial.read() → ring buffer(字节流)
  - 解析线程: 从 buffer 找帧头 → 校验 → 解析 → 回调(ImuSample)
"""

from __future__ import annotations
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from ..timing import OnlineCounterFitter

FRAME_SIZE = 37
HEADER0 = 0xEB
HEADER1 = 0x90
EXPECTED_LEN = 0x22     # 数据包长度字节
IMU_HZ = 400            # 名义采样率，用于丢帧诊断


@dataclass
class ImuSample:
    """一帧解析后的 IMU 数据。ts 是系统绝对时间(秒)。"""
    ts: float           # 系统时间戳(收到时打)
    counter: int        # 1..400，PPS 周期内序号
    gx: float; gy: float; gz: float    # °/s
    ax: float; ay: float; az: float    # g
    temp: float         # ℃
    rx_time: float      # 原始收到时刻(诊断抖动用)


def verify_checksum(buf: bytes) -> bool:
    """校验和 = 字节[0..35] 累加的低 8 位 == buf[36]。"""
    if len(buf) < FRAME_SIZE:
        return False
    return (sum(buf[0:36]) & 0xFF) == buf[36]


def parse_frame(buf: bytes) -> Optional[ImuSample]:
    """解析一帧(必须已校验通过)。时间戳留空，由 reader 打。"""
    if len(buf) < FRAME_SIZE:
        return None
    if buf[0] != HEADER0 or buf[1] != HEADER1 or buf[2] != EXPECTED_LEN:
        return None
    if not verify_checksum(buf):
        return None
    gx, gy, gz, ax, ay, az, temp = struct.unpack("<7f", buf[4:32])
    counter = struct.unpack("<I", buf[32:36])[0]
    now = time.monotonic()
    return ImuSample(
        ts=now, counter=counter,
        gx=gx, gy=gy, gz=gz,
        ax=ax, ay=ay, az=az,
        temp=temp, rx_time=now,
    )


def find_frames(stream: bytes):
    """从字节流里抽出所有合法帧，返回 (帧列表, 消费后的剩余字节, 统计)。

    用于离线解析 + 单元测试。
    """
    frames = []
    bad = 0
    resync = 0
    i = 0
    n = len(stream)
    while i <= n - FRAME_SIZE:
        if stream[i] == HEADER0 and stream[i + 1] == HEADER1 and stream[i + 2] == EXPECTED_LEN:
            frame = stream[i:i + FRAME_SIZE]
            if verify_checksum(frame):
                frames.append(frame)
                i += FRAME_SIZE
                continue
            else:
                bad += 1
        resync += 1
        i += 1
    return frames, stream[i:], {"bad_checksum": bad, "resyncs": resync}


def fit_counter_timestamps(ts_list, counter_list):
    """用硬件 counter 对 PC 接收时刻做鲁棒线性拟合, 消除串口/USB 接收抖动。

    IMU 采样由设备晶振驱动(等间隔), PC 接收时刻含串口+驱动+调度抖动。
    拟合 ts = a*counter + b 后, 每帧时间戳改取拟合值 —— 常数偏移 b 由
    Kalibr 的时间标定吸收, 我们要的是消除"逐帧不一致"的抖动。

    counter 可能是 1..400 回绕(PPS 接 DIO2)或自由递增, 先展开成单调序列。

    返回 (fitted_ts, info): fitted_ts 为 np.ndarray; info 含
    rate_hz(实测采样率)、sigma_ms(拟合残差 RMS, 即接收抖动指标)、
    outliers(剔除的离群点数)。
    """
    import numpy as np

    ts_arr = np.asarray(ts_list, dtype=np.float64)
    cnt = np.asarray(counter_list, dtype=np.int64)
    if len(ts_arr) < 10:
        return ts_arr.copy(), {"rate_hz": 0.0, "sigma_ms": 0.0, "outliers": 0}

    # 展开回绕: 1..400(PPS) 周期 400; 自由计数按 2^32
    ec = np.empty(len(cnt), dtype=np.int64)
    wraps = 0
    prev = None
    for i, c in enumerate(cnt):
        if prev is not None and c < prev:
            wraps += 400 if prev <= 400 else (1 << 32)
        ec[i] = c + wraps
        prev = c

    A = np.vstack([ec.astype(np.float64), np.ones(len(ec))]).T
    mask = np.ones(len(ec), dtype=bool)
    a = b = 0.0
    for _ in range(4):
        a, b = np.linalg.lstsq(A[mask], ts_arr[mask], rcond=None)[0]
        res = ts_arr - (a * ec + b)
        sigma = float(res[mask].std())
        new_mask = np.abs(res) < 3.0 * max(sigma, 1e-4)
        if (new_mask == mask).all():
            break
        mask = new_mask
    fitted = a * ec + b
    sigma_ms = float((ts_arr[mask] - fitted[mask]).std() * 1000.0)
    info = {
        "rate_hz": 1.0 / a if a > 0 else 0.0,
        "sigma_ms": sigma_ms,
        "outliers": int((~mask).sum()),
    }
    return fitted, info


class ImuReader:
    """后台线程读串口 + 解析。每解出一帧调 on_sample。

    用法:
        r = ImuReader(port="/dev/ttyUSB0", on_sample=callback)
        r.start()
        ...
        r.stop()
    """

    def __init__(
        self,
        port: str,
        baud: int = 921600,
        on_sample: Optional[Callable[[ImuSample], None]] = None,
        name: str = "imu",
    ):
        self.port = port
        self.baud = baud
        self.on_sample = on_sample
        self.name = name

        self._ser = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._buf = bytearray()

        # 在线时间戳去抖: IMU 400Hz PPS counter, 每 50 点拟合一次
        self._ts_fitter = OnlineCounterFitter(counter_wrap=400, window_size=400, fit_every=50)

        # 统计
        self.frames_ok = 0
        self.frames_bad = 0
        self.resyncs = 0
        self.last_counter = None
        self.dropped_frames = 0      # counter 不连续 = 丢帧
        self.recent_dt = deque(maxlen=400)   # 最近帧间隔，算抖动

    def start(self) -> bool:
        try:
            import serial
        except ImportError as e:
            raise RuntimeError("需要 pyserial: pip install pyserial") from e

        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0.001)
        except Exception as e:
            print(f"[{self.name}] 打开 {self.port} 失败: {e}")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"imu-{self.name}", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def _loop(self):
        while self._running:
            try:
                # 有多少读多少,不等凑批(read(512)要等~34ms填满,是抖动主因)
                n = self._ser.in_waiting
                data = self._ser.read(n if n > 0 else 1)
            except Exception:
                time.sleep(0.01)
                continue
            if not data:
                continue
            self._buf.extend(data)
            self._consume()

    def _consume(self):
        """从 buffer 里尽量抽出完整帧。"""
        buf = self._buf
        n = len(buf)
        i = 0
        consumed = 0
        while i <= n - FRAME_SIZE:
            if buf[i] == HEADER0 and buf[i + 1] == HEADER1 and buf[i + 2] == EXPECTED_LEN:
                frame = bytes(buf[i:i + FRAME_SIZE])
                if verify_checksum(frame):
                    self._handle(frame)
                    i += FRAME_SIZE
                    consumed = i
                    continue
                else:
                    self.frames_bad += 1
            # 不匹配，前进一字节找下一个帧头
            self.resyncs += 1
            i += 1
        # 丢弃已消费部分，保留尾巴
        del buf[:consumed]

    def _handle(self, frame: bytes):
        s = parse_frame(frame)
        if s is None:
            return
        self.frames_ok += 1

        # 丢帧检测: counter 应连续递增。
        # 有 PPS 时每秒 1..400 回绕; 无 PPS 时自由递增,两种都视为正常,
        # 只有 counter 既不连续也不回绕到 1 才算丢帧。
        if self.last_counter is not None:
            prev = self.last_counter
            if s.counter != prev + 1 and s.counter != 1:
                self.dropped_frames += 1
        self.last_counter = s.counter

        # 时间戳去抖: 用 counter 在线拟合, 替换 ts 为拟合值
        s.ts = self._ts_fitter.feed(s.counter, s.ts)

        # 时间间隔抖动统计(仍用原始接收时刻, 作为链路质量诊断)
        if not hasattr(self, "_first_rx"):
            self._first_rx = s.rx_time
        if self.recent_dt:
            self.recent_dt.append(s.rx_time - self._last_rx)
        else:
            self.recent_dt.append(0.0)
        self._last_rx = s.rx_time

        if self.on_sample:
            try:
                self.on_sample(s)
            except Exception:
                pass

    @property
    def _last_rx(self):
        return getattr(self, "_last_rx_val", 0.0)

    @_last_rx.setter
    def _last_rx(self, v):
        self._last_rx_val = v

    def stats(self) -> dict:
        dts = list(self.recent_dt)[1:] if len(self.recent_dt) > 1 else []
        dt_ms = [d * 1000 for d in dts]
        # 帧率用 累计帧数/累计时长(USB 批量到达会让逐帧 dt 统计虚高)
        first_rx = getattr(self, "_first_rx", None)
        rate = 0.0
        if first_rx is not None and self._last_rx > first_rx:
            rate = self.frames_ok / (self._last_rx - first_rx)
        return {
            "frames_ok": self.frames_ok,
            "frames_bad": self.frames_bad,
            "resyncs": self.resyncs,
            "dropped_frames": self.dropped_frames,
            "rate_hz": rate,
            "dt_min_ms": min(dt_ms) if dt_ms else 0.0,
            "dt_max_ms": max(dt_ms) if dt_ms else 0.0,
            "dt_jitter_ms": (max(dt_ms) - min(dt_ms)) if dt_ms else 0.0,
        }
