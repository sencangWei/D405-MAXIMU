#!/usr/bin/env python3
"""IMU Allan 方差分析: 从 imu.bin 测出真实噪声参数。

用法:
  # 直接用已有会话的 IMU 数据 (需静止放置录的)
  python3 scripts/allan_variance_imu.py --session recordings/d405_720p_all_xxx

  # 或直接指定 imu.bin
  python3 scripts/allan_variance_imu.py --bin path/to/imu.bin

输出: 陀螺/加速度计噪声密度 + 随机游走 + 零偏稳定性 (控制台 + allan.png)

注意: 数据必须是静止采集的, 否则曲线在高 τ 段会翘起污染拟合。
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np

IMU_PACK_FMT = "<dI7f"
IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)
DEG2RAD = 3.141592653589793 / 180.0
G0 = 9.80665


def load_imu_bin(path: Path):
    """返回 (t, gyro_rad_s (N,3), accel_m_s2 (N,3))."""
    t, g, a = [], [], []
    with path.open("rb") as f:
        while True:
            chunk = f.read(IMU_PACK_SIZE)
            if len(chunk) < IMU_PACK_SIZE:
                break
            ts, _cnt, gx, gy, gz, ax, ay, az, _temp = struct.unpack(IMU_PACK_FMT, chunk)
            t.append(ts)
            g.append((gx * DEG2RAD, gy * DEG2RAD, gz * DEG2RAD))
            a.append((ax * G0, ay * G0, az * G0))
    return (np.asarray(t), np.asarray(g), np.asarray(a))


def allan_deviation(data: np.ndarray, max_log2: int = 16) -> tuple:
    """对 (N,3) 序列算 Allan deviation. 返回 (tau, adev) 各为 (M,3)."""
    n = len(data)
    max_log2 = min(max_log2, int(np.log2(n)) - 1)
    taus, devs = [], []
    for m in range(1, max_log2 + 1):
        tau = 1 << m
        n_blocks = n // tau
        if n_blocks < 4:
            break
        blocks = data[: n_blocks * tau].reshape(n_blocks, tau, data.shape[1])
        means = blocks.mean(axis=1)                      # (n_blocks, 3)
        diff = np.diff(means, axis=0)                    # (n_blocks-1, 3)
        dev = np.sqrt(np.mean(diff ** 2, axis=0) / 2.0)  # Allan deviation
        taus.append(tau)
        devs.append(dev)
    return (np.asarray(taus, float), np.asarray(devs))


def fit_region(tau, dev, slope, lo_frac, hi_frac):
    """在 log-log 曲线上取 [lo_frac,hi_frac] 比例段, 拟合 Y = C * tau^slope, 返回 C."""
    lo, hi = int(lo_frac * len(tau)), int(hi_frac * len(tau))
    lo, hi = max(lo, 1), max(hi, lo + 1)
    x = np.log(tau[lo:hi])
    y = np.log(dev[lo:hi])
    # 固定斜率拟合: y - slope*x = const
    c = np.exp(np.mean(y - slope * x))
    return c


def fit_noise_density(tau, dev):
    """斜率 -1/2 段 -> 噪声密度 N (deg/s^2·noise_density 的拟合值)."""
    return fit_region(tau, dev, -0.5, 0.05, 0.45)


def fit_random_walk(tau, dev):
    """斜率 +1/2 段 -> 随机游走系数 K."""
    return fit_region(tau, dev, 0.5, 0.5, 0.95)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=Path, help="采集会话目录 (含 external_imu/imu.bin)")
    ap.add_argument("--bin", type=Path, help="直接指定 imu.bin")
    ap.add_argument("--out", type=Path, default=Path("allan.png"), help="输出图")
    args = ap.parse_args()

    if args.bin:
        bin_path = args.bin
    elif args.session:
        bin_path = args.session / "external_imu" / "imu.bin"
    else:
        ap.error("需 --session 或 --bin")
    if not bin_path.exists():
        print(f"[ERROR] 找不到 {bin_path}")
        return 1

    t, g, a = load_imu_bin(bin_path)
    dt = np.median(np.diff(t))
    if len(g) < 1000:
        print(f"[WARN] 数据太短 ({len(g)} 帧 ~{len(g)*dt:.0f}s), Allan 结果不可靠, 建议>=3分钟")
    print(f"载入 {len(g)} 帧 IMU ({dt*1000:.2f}ms @ {1/dt:.0f}Hz), 时长 {t[-1]-t[0]:.0f}s")
    print(f"静态判定: 角速度rms={np.sqrt((g**2).mean()):.4f} rad/s, "
          f"加速度rms={np.sqrt(((a-np.mean(a,0))**2).mean()):.3f} m/s^2 "
          f"(应接近 0, 若大说明非静止采集)")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axs = plt.subplots(1, 2, figsize=(13, 5))
    out = {}

    for ax, data, name, unit in (
        (axs[0], g, "Gyro", "rad/s"),
        (axs[1], a, "Accel", "m/s^2"),
    ):
        tau, dev = allan_deviation(data)
        dev_mean = np.sqrt((dev ** 2).mean(axis=1))  # 3 轴 RMS
        rep = {
            "noise_density": fit_noise_density(tau, dev_mean),
            "random_walk": fit_random_walk(tau, dev_mean),
            "bias_stability": dev_mean.min(),
            "tau_bias": tau[np.argmin(dev_mean)],
        }
        out[name] = rep
        print(f"\n===== {name} =====")
        print(f" 噪声密度   N = {rep['noise_density']:.3e} ({unit}/s/sqrt(Hz))")
        print(f" 随机游走   K = {rep['random_walk']:.3e} ({unit}/s^2/sqrt(Hz))")
        print(f" 零偏稳定性 B = {rep['bias_stability']:.3e} (在 tau={rep['tau_bias']*dt:.1f}s)")
        ax.loglog(tau * dt, dev_mean, "o-", ms=3, lw=1, label=f"{name} ADEV")
        for c, label in (
            (rep["noise_density"] * np.sqrt(dt), f"N={rep['noise_density']:.2e}"),
            (rep["bias_stability"], f"B={rep['bias_stability']:.2e}"),
            (rep["random_walk"] * np.sqrt(dt) * dt, f"K={rep['random_walk']:.2e}"),
        ):
            ax.axhline(c, ls="--", lw=0.8, alpha=0.5)
        ax.set_xlabel("Cluster time τ (s)")
        ax.set_ylabel(f"Allan deviation ({unit}/√τ)")
        ax.set_title(name)
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(args.out, dpi=150)
    print(f"\n曲线已保存 -> {args.out}")

    print("\n===== SLAM 配置建议 (填进 config) =====")
    # 陀螺: noise_density 直接填; random_walk 用 单位 rad/s^2/sqrt(Hz)
    # 加速度计同理
    g_nd, a_nd = out["Gyro"]["noise_density"], out["Accel"]["noise_density"]
    g_rw, a_rw = out["Gyro"]["random_walk"], out["Accel"]["random_walk"]
    print(f"  IMU.NoiseGyro: {g_nd:.4e}")
    print(f"  IMU.NoiseAcc:  {a_nd:.4e}")
    print(f"  IMU.GyroWalk:  {g_rw:.4e}")
    print(f"  IMU.AccWalk:   {a_rw:.4e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
