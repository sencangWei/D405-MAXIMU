#!/usr/bin/env python3
"""单独录制 KT-EX9-2 IMU 的 counter 复位现象，用于给商家复现问题。

用法:
  python3 scripts/record_imu_counter.py --duration 30
  python3 scripts/record_imu_counter.py --duration 60 --plot

输出:
  - 终端实时打印 counter，复位时高亮
  - recordings/imu_counter_YYYYMMDD_HHMMSS/imu_counter.csv
  - recordings/imu_counter_YYYYMMDD_HHMMSS/summary.json
  - 若加 --plot，退出后绘制 counter 曲线并标红复位点
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.imu_reader import ImuReader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="录制 IMU counter 复位现象")
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00",
        help="IMU 串口路径",
    )
    parser.add_argument("--baud", type=int, default=921600, help="波特率")
    parser.add_argument("--duration", type=float, default=30.0, help="录制时长(秒)")
    parser.add_argument("--plot", action="store_true", help="结束后绘制 counter 曲线")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "recordings",
        help="输出根目录",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = args.output_root / f"imu_counter_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=False)
    csv_path = out_dir / "imu_counter.csv"
    summary_path = out_dir / "summary.json"

    samples: list[dict] = []
    resets: list[dict] = []
    last_counter: int | None = None
    start_ts: float | None = None

    def on_sample(s):
        nonlocal last_counter, start_ts
        now = time.monotonic()
        if start_ts is None:
            start_ts = now
        elapsed = now - start_ts

        reset_info = None
        if last_counter is not None:
            if s.counter == 1 and last_counter != 1:
                # counter 异常归 1（不是连续多个 1 的毛刺，而是真实复位边沿）
                reset_info = {
                    "elapsed_s": round(elapsed, 3),
                    "from": last_counter,
                    "to": s.counter,
                }
                resets.append(reset_info)
                print(
                    f"\033[91m[RESET] elapsed={elapsed:7.3f}s  {last_counter:>8} -> {s.counter:<8}  "
                    f"total_resets={len(resets)}\033[0m"
                )
            elif s.counter < last_counter and s.counter != 1:
                # 其他异常回退
                reset_info = {
                    "elapsed_s": round(elapsed, 3),
                    "from": last_counter,
                    "to": s.counter,
                    "note": "unexpected_drop",
                }
                resets.append(reset_info)
                print(
                    f"\033[93m[DROP]  elapsed={elapsed:7.3f}s  {last_counter:>8} -> {s.counter:<8}\033[0m"
                )

        last_counter = s.counter

        samples.append(
            {
                "elapsed_s": round(elapsed, 6),
                "counter": s.counter,
                "gx": s.gx,
                "gy": s.gy,
                "gz": s.gz,
                "ax": s.ax,
                "ay": s.ay,
                "az": s.az,
            }
        )

        # 每 100 帧打印一次正常进度
        if len(samples) % 100 == 0:
            print(
                f"[OK] elapsed={elapsed:7.3f}s  samples={len(samples):>5}  "
                f"counter={s.counter:>8}  resets={len(resets)}"
            )

    reader = ImuReader(
        args.port,
        baud=args.baud,
        on_sample=on_sample,
        warmup_frames=0,
        name="imu_counter",
    )

    print(f"[IMU counter recorder] 输出目录: {out_dir}")
    print(f"[IMU counter recorder] 串口: {args.port} @ {args.baud}")
    print(f"[IMU counter recorder] 计划录制 {args.duration:.0f}s，按 Ctrl+C 提前结束")
    print("-" * 70)

    if not reader.start():
        print(f"[ERROR] 无法打开串口: {args.port}")
        return 1

    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < args.duration:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[中断] 用户停止录制")
    finally:
        reader.stop()

    duration = time.monotonic() - t0
    rate = len(samples) / duration if duration > 0 else 0.0

    # 写 CSV
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        import csv

        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "elapsed_s",
                "counter",
                "gx",
                "gy",
                "gz",
                "ax",
                "ay",
                "az",
            ],
        )
        writer.writeheader()
        writer.writerows(samples)

    # 写 summary
    summary = {
        "duration_s": round(duration, 3),
        "samples": len(samples),
        "rate_hz": round(rate, 1),
        "resets": len([r for r in resets if r.get("to") == 1]),
        "unexpected_drops": len([r for r in resets if r.get("to") != 1]),
        "reset_events": resets,
        "port": args.port,
        "baud": args.baud,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 70)
    print(f"[完成] 录制 {duration:.1f}s，样本 {len(samples)}，平均 {rate:.1f}Hz")
    print(f"[完成] counter 复位到 1 的次数: {summary['resets']}")
    print(f"[完成] 其他异常回退次数: {summary['unexpected_drops']}")
    print(f"[完成] CSV: {csv_path}")
    print(f"[完成] SUMMARY: {summary_path}")

    if args.plot and samples:
        _plot(samples, resets, out_dir)

    return 0


def _plot(samples: list[dict], resets: list[dict], out_dir: Path):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[WARN] 未安装 matplotlib，跳过绘图")
        return

    t = [s["elapsed_s"] for s in samples]
    c = [s["counter"] for s in samples]

    plt.figure(figsize=(14, 5))
    plt.plot(t, c, "b-", linewidth=0.8, label="IMU counter")

    # 标红复位点：画竖线 + 文字
    for r in resets:
        if r.get("to") == 1:
            color = "red"
            label = f"reset {r['from']}→1"
        else:
            color = "orange"
            label = f"drop {r['from']}→{r['to']}"
        plt.axvline(x=r["elapsed_s"], color=color, linestyle="--", alpha=0.7)
        plt.text(
            r["elapsed_s"],
            max(c) * 0.9,
            label,
            rotation=90,
            verticalalignment="top",
            fontsize=7,
            color=color,
        )

    plt.xlabel("elapsed time [s]")
    plt.ylabel("counter")
    plt.title(f"KT-EX9-2 IMU counter resets ({len(resets)} events)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plot_path = out_dir / "counter_plot.png"
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    print(f"[完成] PLOT: {plot_path}")


if __name__ == "__main__":
    raise SystemExit(main())
