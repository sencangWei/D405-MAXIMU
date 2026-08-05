#!/usr/bin/env python3
"""相机-IMU 时间同步验收测试(产品级)。

两路时间戳都做设备时钟去抖, 消除 PC 侧送达抖动:
  相机: 设备帧序号对到达时刻鲁棒拟合(Windows 拿不到 RealSense metadata,
        global_time 无效, 帧序号是设备侧唯一可靠时钟; 丢帧跳号天然容忍)
  IMU:  硬件 counter 对接收时刻鲁棒拟合

去抖后剩下的"固定"偏移由 Kalibr --time-calibration 精确估计。
注意: 30fps 互相关估计器本身有 ±5~10ms 方法噪声。图像差分是帧间积分,
因此时间戳必须取前后两帧曝光时刻的中点；估计偏差仍会随晃动频谱变化。
本测试的定位是给 Kalibr 提供收敛域内的初值
(±16ms@30fps), 不是精确测量 td。

验收标准:
  1. 至少采集 5 轮，单轮只用于快速诊断，不能验收重复性
  2. 每轮陀螺通道 ρ > 0.8(证明每轮数据质量都足够)
  3. 各轮延迟离散度 < 12ms(保证 ρ 加权均值落在 Kalibr 收敛域内)
真正的 td 产品验收在下游: 两次独立采集分别跑 Kalibr, td 重复性 <3ms。

用法:
  python scripts/test_sync.py                  # 5 轮验收(默认)
  python scripts/test_sync.py --runs 1 --secs 12   # 单轮快看
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import load_config
from ego_vio.imu.imu_reader import ImuReader, fit_counter_timestamps
from ego_vio.camera.realsense_capture import RealSenseCapture

PASS_SPREAD_MS = 12.0   # Kalibr td 收敛域 ≈ 半帧(16.7ms@30fps), 留裕量
PASS_RHO = 0.8
MIN_ACCEPTANCE_RUNS = 5


def evaluate_acceptance(delays, rhos):
    """Return acceptance state and delay spread for independent runs."""
    delays = np.asarray(delays, dtype=float)
    rhos = np.asarray(rhos, dtype=float)
    spread = float(delays.max() - delays.min()) if len(delays) else float("inf")
    enough_runs = len(delays) >= MIN_ACCEPTANCE_RUNS
    correlations_ok = len(rhos) == len(delays) and bool((rhos > PASS_RHO).all())
    return enough_runs and correlations_ok and spread < PASS_SPREAD_MS, spread


def motion_interval_timestamp(previous_ts: float, current_ts: float) -> float:
    """Timestamp frame-to-frame image motion at the interval midpoint."""
    return 0.5 * (float(previous_ts) + float(current_ts))


def collect_run(secs: float, unit) -> dict:
    """采一轮: 返回 IMU/相机的原始时间序列。采集中需要持续晃动设备。"""
    imu_ts, imu_cnt, imu_g, imu_a = [], [], [], []
    cam_t, cam_fn, cam_e, cam_preview, domains = [], [], [], [], []
    prev = {"img": None, "ts": None}

    def on_imu(s):
        imu_ts.append(s.ts)
        imu_cnt.append(s.counter)
        imu_g.append((s.gx ** 2 + s.gy ** 2 + s.gz ** 2) ** 0.5)
        imu_a.append(abs((s.ax ** 2 + s.ay ** 2 + s.az ** 2) ** 0.5 - 1.0))

    def on_frame(f):
        import cv2
        gray = cv2.cvtColor(f.color, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 120)).astype(np.int16)
        if prev["img"] is not None:
            # |I_k-I_(k-1)| represents motion accumulated across the exposure
            # interval. Assigning it to t_k adds a systematic half-frame delay
            # (16.7 ms at 30 fps), so use the interval midpoint instead.
            cam_t.append(motion_interval_timestamp(prev["ts"], f.ts))
            cam_fn.append(f.frame_number)
            cam_e.append(float(np.mean(np.abs(small - prev["img"]))))
            cam_preview.append(small.astype(np.uint8))
            domains.append(f.ts_domain)
        prev["img"] = small
        prev["ts"] = f.ts

    cam = RealSenseCapture(
        serial=unit.camera.serial, width=unit.camera.width,
        height=unit.camera.height, fps=unit.camera.fps,
        auto_exposure=unit.camera.auto_exposure,
        exposure_us=unit.camera.exposure_us,
        gain=unit.camera.gain,
        on_frame=on_frame, name="sync",
    )
    if not cam.start():
        print("!! 相机启动失败, 检查 D405 连接")
        return {}
    imu = ImuReader(port=unit.imu.port, baud=unit.imu.baud,
                    on_sample=on_imu, name="sync")
    if not imu.start():
        cam.stop()
        print("!! IMU 打开失败, 检查串口")
        return {}

    print("3 秒后开始, 采集中持续来回晃动...")
    time.sleep(3.0)
    print(">>> 开始! 保持晃动 <<<")
    time.sleep(secs)
    cam.stop()
    imu.stop()
    print(">>> 本轮结束 <<<\n")

    return {
        "imu_ts": imu_ts, "imu_cnt": imu_cnt, "imu_g": imu_g, "imu_a": imu_a,
        "cam_t": cam_t, "cam_fn": cam_fn, "cam_e": cam_e,
        "cam_preview": cam_preview, "domains": domains,
        "imu_stats": imu.stats(),
    }


def xcorr_lag(grid, ci_s, t_imu, imu_sig, ker, dt):
    """互相关求延迟: 返回 (delay_ms, rho)。delay>0 = 相机比 IMU 晚。"""
    ii = np.interp(grid, t_imu, imu_sig)
    ii = ii - ii.mean()
    ii_s = np.convolve(ii, ker, mode="same")
    if ii_s.std() < 1e-9:
        return None
    xc = np.correlate(ii_s, ci_s, mode="full")
    lags = (np.arange(len(xc)) - (len(ii_s) - 1)) * dt
    m = np.abs(lags) <= 0.5
    lag_v, xc_v = lags[m], xc[m]
    norm = ii_s.std() * ci_s.std() * len(ii_s)
    rho = xc_v / norm if norm > 0 else xc_v * 0
    best = int(np.argmax(rho))
    # 抛物线插值拿亚采样精度(2ms 网格 → ~0.5ms)
    lag_ref = lag_v[best]
    if 0 < best < len(rho) - 1:
        y0, y1, y2 = rho[best - 1], rho[best], rho[best + 1]
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-12:
            lag_ref = lag_v[best] + 0.5 * (y0 - y2) / denom * dt
    return -lag_ref * 1000.0, float(rho[best])


def analyze(run: dict) -> dict:
    """分析一轮数据: IMU 去抖 + 互相关, 返回陀螺/加速度两通道的延迟和 ρ。"""
    t_raw = np.asarray(run["imu_ts"])
    cnt = run["imu_cnt"]
    g = np.asarray(run["imu_g"])
    a = np.asarray(run["imu_a"])
    ct = np.asarray(run["cam_t"])
    ce = np.asarray(run["cam_e"])

    print(f"IMU 样本: {len(t_raw)}  相机帧: {len(ct)}")
    if len(t_raw) < 100 or len(ct) < 10:
        print("!! 数据太少, 检查 IMU/相机连接")
        return {}

    # IMU counter 去抖(与转 bag 同一代码路径)
    t_fit, info = fit_counter_timestamps(t_raw, cnt)
    print(f"IMU 去抖: 实测 {info['rate_hz']:.1f}Hz, 接收抖动 σ={info['sigma_ms']:.2f}ms")
    t = t_fit

    # 相机帧序号去抖(与转 bag 同一代码路径)
    ct_fit, cinfo = fit_counter_timestamps(run["cam_t"], run["cam_fn"])
    fnums = run["cam_fn"]
    cam_drops = sum(max(0, b - a - 1) for a, b in zip(fnums, fnums[1:]))
    print(f"相机去抖: 实测 {cinfo['rate_hz']:.1f}fps, 送达抖动 σ={cinfo['sigma_ms']:.2f}ms, "
          f"丢帧 {cam_drops}")
    ct = ct_fit

    print(f"运动强度: 陀螺 std={g.std():.1f}°/s  图像 std={ce.std():.2f}")
    if ce.std() < 0.5:
        print("!! 图像运动太小, 你是不是没晃? 晃动别停")
        return {}

    # 统一到 500Hz 网格
    t0, t1 = max(t[0], ct[0]), min(t[-1], ct[-1])
    if t1 - t0 < 3:
        print("!! 两路数据时间重叠太短")
        return {}
    dt = 0.002
    grid = np.arange(t0, t1, dt)
    ci = np.interp(grid, ct, ce)
    ci = ci - ci.mean()
    ker = np.ones(25) / 25   # 50ms 短窗平滑
    ci_s = np.convolve(ci, ker, mode="same")

    gyro = xcorr_lag(grid, ci_s, t, g, ker, dt)
    accel = xcorr_lag(grid, ci_s, t, a, ker, dt)
    if gyro is None:
        print("!! 陀螺信号太平")
        return {}
    res = {"gyro_ms": gyro[0], "gyro_rho": gyro[1]}
    if accel is not None:
        res["accel_ms"], res["accel_rho"] = accel
    print(f"陀螺通道:   相机延迟 {gyro[0]:+7.1f} ms  ρ={gyro[1]:.2f}")
    if accel is not None:
        print(f"加速度通道: 相机延迟 {accel[0]:+7.1f} ms  ρ={accel[1]:.2f} (参考)")
    return res


def select_motion_peaks(times, energy, count=6, min_spacing_s=0.5):
    """Select strong camera-motion samples while avoiding adjacent duplicates."""
    times = np.asarray(times, dtype=float)
    energy = np.asarray(energy, dtype=float)
    selected = []
    for index in np.argsort(energy)[::-1]:
        if all(abs(times[index] - times[other]) >= min_spacing_s for other in selected):
            selected.append(int(index))
            if len(selected) >= count:
                break
    return sorted(selected)


def _robust_normalize(values):
    values = np.asarray(values, dtype=float)
    lo, hi = np.percentile(values, [5, 95])
    scale = max(float(hi - lo), 1e-9)
    return np.clip((values - lo) / scale, 0.0, 1.2)


def save_sync_report(run: dict, result: dict, output_path: Path):
    """Save one human-readable camera/IMU timing report as a PNG."""
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    cjk_font = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if cjk_font.exists():
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=cjk_font).get_name()
        plt.rcParams["axes.unicode_minus"] = False

    imu_t, _ = fit_counter_timestamps(run["imu_ts"], run["imu_cnt"])
    cam_t, _ = fit_counter_timestamps(run["cam_t"], run["cam_fn"])
    gyro = np.asarray(run["imu_g"], dtype=float)
    energy = np.asarray(run["cam_e"], dtype=float)
    delay_s = float(result["gyro_ms"]) / 1000.0
    t0 = max(float(imu_t[0]), float(cam_t[0]))
    t1 = min(float(imu_t[-1]), float(cam_t[-1]))
    grid = np.arange(t0, t1, 0.002)
    cam_curve = _robust_normalize(np.interp(grid, cam_t, energy))
    gyro_raw = _robust_normalize(np.interp(grid, imu_t, gyro))
    # delay > 0 means the camera motion signal occurs after its matching IMU event.
    gyro_aligned = _robust_normalize(np.interp(grid - delay_s, imu_t, gyro))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(16, 9), constrained_layout=True)
    grid_spec = fig.add_gridspec(2, 6, height_ratios=[2.2, 1.0])
    ax = fig.add_subplot(grid_spec[0, :])
    x = grid - t0
    ax.plot(x, cam_curve, color="#2563eb", linewidth=2.0, label="相机运动强度")
    ax.plot(x, gyro_raw, color="#9ca3af", linewidth=1.0, alpha=0.65,
            label="IMU陀螺（原始时间）")
    ax.plot(x, gyro_aligned, color="#dc2626", linewidth=1.7,
            label=f"IMU陀螺（按 {result['gyro_ms']:+.1f} ms 对齐）")
    ax.set_title(
        f"相机—IMU 快速运动时间同步  |  延迟={result['gyro_ms']:+.1f} ms  "
        f"相关系数 ρ={result['gyro_rho']:.2f}"
    )
    ax.set_xlabel("采集开始后的时间（秒）")
    ax.set_ylabel("归一化运动强度")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper right")

    peaks = select_motion_peaks(cam_t, energy, count=6, min_spacing_s=0.5)
    previews = run.get("cam_preview", [])
    for slot in range(6):
        pane = fig.add_subplot(grid_spec[1, slot])
        pane.axis("off")
        if slot >= len(peaks):
            continue
        index = peaks[slot]
        if index < len(previews):
            pane.imshow(previews[index], cmap="gray", vmin=0, vmax=255)
        camera_stamp = float(cam_t[index])
        target_imu_stamp = camera_stamp - delay_s
        imu_index = int(np.argmin(np.abs(imu_t - target_imu_stamp)))
        nearest_imu_stamp = float(imu_t[imu_index])
        pane.set_title(
            f"峰值帧 {run['cam_fn'][index]}\n"
            f"cam={camera_stamp:.6f}\n"
            f"imu*={nearest_imu_stamp:.6f}\n"
            f"cam-imu*={(camera_stamp-nearest_imu_stamp)*1000:+.1f} ms",
            fontsize=9,
        )
        ax.axvline(camera_stamp - t0, color="#2563eb", alpha=0.18, linewidth=1.0)

    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"同步诊断图片: {output_path.resolve()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5, help="验收轮数(默认 5)")
    ap.add_argument("--secs", type=float, default=12.0, help="每轮采集秒数")
    ap.add_argument("--config", default=None, help="设备配置文件路径")
    ap.add_argument("--report-dir", default="sync_reports",
                    help="同步诊断图片目录(默认 sync_reports)")
    args = ap.parse_args()

    unit = load_config(args.config).units[0]
    print("=== 相机-IMU 时间同步验收测试 ===")
    print(f"标准: 延迟离散度<{PASS_SPREAD_MS:.0f}ms, 每轮 ρ>{PASS_RHO}\n")

    results = []
    report_stamp = time.strftime("%Y%m%d_%H%M%S")
    for r in range(args.runs):
        if args.runs > 1:
            print(f"----- 第 {r + 1}/{args.runs} 轮 -----")
        run = collect_run(args.secs, unit)
        if not run:
            return 1
        if run["domains"]:
            print(f"相机时间戳域: {run['domains'][-1]} (global_time=曝光时刻, arrival=到达时刻, "
                  f"两种都经帧序号去抖, 不影响验收)")
        res = analyze(run)
        if not res:
            return 1
        save_sync_report(
            run,
            res,
            Path(args.report_dir) / f"sync_{report_stamp}_run{r + 1}.png",
        )
        results.append(res)
        if r + 1 < args.runs:
            print("休息 3 秒(设备可以放下来)...\n")
            time.sleep(3.0)

    delays = np.array([r["gyro_ms"] for r in results])
    rhos = np.array([r["gyro_rho"] for r in results])
    ok, spread = evaluate_acceptance(delays, rhos)

    print("\n" + "=" * 56)
    print(f"各轮延迟(ms): {'  '.join(f'{d:+.1f}' for d in delays)}")
    print(f"各轮 ρ:       {'  '.join(f'{x:.2f}' for x in rhos)}")
    print(f"离散度: {spread:.1f}ms (标准 <{PASS_SPREAD_MS:.0f}ms)")

    if ok:
        # ρ 加权均值: 高 ρ 轮次的估计偏差小, 权重高
        mean_d = float((delays * rhos).sum() / rhos.sum())
        print(f"\n*** PASS ***  ρ 加权平均延迟 {mean_d:+.1f}ms")
        print(f"转 bag 预对齐参数: --cam-offset {-mean_d / 1000.0:+.3f}")
        print("说明: 这是 Kalibr 的 td 初值(收敛域 ±16ms), 精确 td 由")
        print("      kalibr_calibrate_imu_camera --time-calibration 估计;")
        print("      产品验收 = 两次独立采集的 Kalibr td 重复性 <3ms。")
        return 0
    print("\n*** FAIL ***")
    if len(results) < MIN_ACCEPTANCE_RUNS:
        print(f"- 只有 {len(results)} 轮: 至少需要 {MIN_ACCEPTANCE_RUNS} 轮才能检验重复性。")
    if spread >= PASS_SPREAD_MS:
        if (rhos > PASS_RHO).all():
            print(f"- 在每轮高相关的前提下，离散度 {spread:.1f}ms 仍超标：检查USB链路和时间映射。")
        else:
            print(f"- 离散度 {spread:.1f}ms 超标，但存在低相关轮次；先改善纹理和运动模糊，")
            print("  当前证据不足以判定USB或设备时钟发生漂移。")
    if not (rhos > PASS_RHO).all():
        failed = int((rhos <= PASS_RHO).sum())
        print(f"- 有 {failed} 轮 ρ≤0.8: 连续多轴旋转，相机对着近距离有纹理物体，并减少运动模糊。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
