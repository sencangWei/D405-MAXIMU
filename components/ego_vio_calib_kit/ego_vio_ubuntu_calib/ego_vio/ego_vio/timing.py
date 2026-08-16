"""在线 counter/帧序号时钟拟合, 给实时采集提供去抖时间戳。

离线转 bag 用的 fit_counter_timestamps 是对整段数据做拟合;
这里提供滑动窗口版, 每收到若干样本拟合一次, 中间样本用最新参数预测。
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np


class OnlineCounterFitter:
    """用硬件 counter(或帧序号)对 PC 接收时间做在线线性拟合去抖。

    模型: ts_fitted = a * unwrapped_counter + b
    - counter 严格等间隔(设备晶振), 所以拟合斜率 a 就是采样周期。
    - 截距 b 吸收固定传输延迟, 不需要在实时里精确估计, 只要稳定即可。
    """

    def __init__(
        self,
        counter_wrap: Optional[int] = None,
        window_size: int = 400,
        fit_every: int = 50,
    ):
        self.counter_wrap = counter_wrap
        self.window_size = window_size
        self.fit_every = fit_every
        self._samples = deque(maxlen=window_size)
        self._a: Optional[float] = None
        self._b: Optional[float] = None
        self._last_raw_counter: Optional[int] = None
        self._wrap_acc = 0
        self._fit_count = 0

    def feed(self, raw_counter: int, raw_ts: float) -> float:
        """喂入一个新样本, 返回去抖后的时间戳。"""
        # 展开回绕
        if self._last_raw_counter is not None and raw_counter < self._last_raw_counter:
            wrap = self.counter_wrap if self.counter_wrap is not None else (1 << 32)
            self._wrap_acc += wrap
        self._last_raw_counter = raw_counter
        uc = raw_counter + self._wrap_acc

        self._samples.append((uc, raw_ts))
        self._fit_count += 1

        if len(self._samples) >= 10 and self._fit_count >= self.fit_every:
            self._fit()
            self._fit_count = 0

        if self._a is not None:
            return float(self._a * uc + self._b)
        return raw_ts

    def _fit(self):
        """对窗口内样本做鲁棒线性拟合。"""
        arr = np.asarray(self._samples, dtype=np.float64)
        ec = arr[:, 0]
        ts = arr[:, 1]

        A = np.vstack([ec, np.ones(len(ec))]).T
        # 第一次普通最小二乘
        a, b = np.linalg.lstsq(A, ts, rcond=None)[0]
        res = ts - (a * ec + b)
        sigma = float(res.std())
        # 剔除 3σ 离群点再拟合
        mask = np.abs(res) < 3.0 * max(sigma, 1e-4)
        if mask.sum() >= 10:
            a, b = np.linalg.lstsq(A[mask], ts[mask], rcond=None)[0]
        self._a = float(a)
        self._b = float(b)

    @property
    def rate_hz(self) -> Optional[float]:
        return 1.0 / self._a if self._a and self._a > 0 else None
