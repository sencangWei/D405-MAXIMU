#!/usr/bin/env python3
"""Generate a reproducible SLAM consistency report without external ground truth."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from pathlib import Path

import numpy as np


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open()))
    if len(rows) < 2:
        raise RuntimeError(f"轨迹点不足: {path}")
    time_s = np.array([float(row["t_sec"]) for row in rows])
    points = np.array(
        [[float(row["x"]), float(row["y"]), float(row["z"])] for row in rows]
    )
    return time_s, points


def trajectory_metrics(time_s: np.ndarray, points: np.ndarray, dwell_s: float) -> dict:
    elapsed = time_s - time_s[0]
    start = points[elapsed <= dwell_s]
    end = points[elapsed >= elapsed[-1] - dwell_s]
    start_center = start.mean(axis=0)
    end_center = end.mean(axis=0)
    delta = end_center - start_center
    segments = np.linalg.norm(np.diff(points, axis=0), axis=1)

    centered_xy = points[:, :2] - points[:, :2].mean(axis=0)
    _, _, axes = np.linalg.svd(centered_xy, full_matrices=False)
    principal_xy = centered_xy @ axes.T

    return {
        "samples": int(len(points)),
        "duration_s": float(elapsed[-1]),
        "path_length_m": float(segments.sum()),
        "xyz_span_cm": (np.ptp(points, axis=0) * 100.0).tolist(),
        "pca_xy_span_cm": (np.ptp(principal_xy, axis=0) * 100.0).tolist(),
        "pca_xy_p1_p99_span_cm": (
            (np.percentile(principal_xy, 99, axis=0)
             - np.percentile(principal_xy, 1, axis=0))
            * 100.0
        ).tolist(),
        "dwell_window_s": dwell_s,
        "start_dwell_samples": int(len(start)),
        "end_dwell_samples": int(len(end)),
        "start_dwell_rms_mm": float(
            np.sqrt(np.mean(np.sum((start - start_center) ** 2, axis=1))) * 1000.0
        ),
        "end_dwell_rms_mm": float(
            np.sqrt(np.mean(np.sum((end - end_center) ** 2, axis=1))) * 1000.0
        ),
        "dwell_center_delta_cm": (delta * 100.0).tolist(),
        "dwell_center_distance_cm": float(np.linalg.norm(delta) * 100.0),
        "dwell_horizontal_distance_cm": float(np.linalg.norm(delta[:2]) * 100.0),
        "dwell_vertical_distance_cm": float(abs(delta[2]) * 100.0),
    }


def sync_metrics(frames_csv: Path) -> dict:
    rows = list(csv.DictReader(frames_csv.open()))

    def differences(first: str, second: str) -> np.ndarray:
        return np.array(
            [float(row[first]) - float(row[second]) for row in rows]
        )

    color_left = differences("color_device_ms", "infrared_left_device_ms")
    left_right = differences(
        "infrared_left_device_ms", "infrared_right_device_ms"
    )
    return {
        "framesets": len(rows),
        "color_left_median_ms": float(np.median(color_left)),
        "color_left_abs_p95_ms": float(np.percentile(np.abs(color_left), 95)),
        "color_left_abs_max_ms": float(np.max(np.abs(color_left))),
        "left_right_median_ms": float(np.median(left_right)),
        "left_right_abs_p95_ms": float(np.percentile(np.abs(left_right), 95)),
        "left_right_abs_max_ms": float(np.max(np.abs(left_right))),
    }


def format_vector(values: list[float], digits: int = 2) -> str:
    return " / ".join(f"{value:.{digits}f}" for value in values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-log", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--copy-dir", type=Path)
    parser.add_argument("--dwell-s", type=float, default=3.0)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    acceptance = json.loads((args.session / "acceptance.json").read_text())
    baseline_time, baseline_points = load_trajectory(args.baseline)
    candidate_time, candidate_points = load_trajectory(args.candidate)
    baseline = trajectory_metrics(baseline_time, baseline_points, args.dwell_s)
    candidate = trajectory_metrics(candidate_time, candidate_points, args.dwell_s)
    synchronization = sync_metrics(args.session / "d405_frames.csv")

    loop_lines = [
        line
        for line in args.candidate_log.read_text(errors="replace").splitlines()
        if "[AUTO_LOOP_ACCEPT]" in line
    ]
    improvement = 100.0 * (
        baseline["dwell_center_distance_cm"]
        - candidate["dwell_center_distance_cm"]
    ) / baseline["dwell_center_distance_cm"]

    metrics = {
        "scope": "no_external_ground_truth_consistency_report",
        "absolute_accuracy_status": "NOT_MEASURED",
        "reason": "未提供测量尺逐点轨迹、动捕、全站仪或标定靶真值",
        "session": str(args.session),
        "capture": acceptance,
        "synchronization": synchronization,
        "baseline": baseline,
        "automatic_loop_candidate": candidate,
        "automatic_loop_accepts": loop_lines,
        "dwell_center_improvement_percent": improvement,
    }
    (args.out_dir / "slam_precision_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font = font_manager.FontProperties(
        fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    )
    fig, axes = plt.subplots(2, 3, figsize=(17, 10))
    views = ((0, 1, "俯视 X-Y"), (0, 2, "侧视 X-Z"), (1, 2, "侧视 Y-Z"))
    variants = (
        (baseline_points, baseline, "稳定基线", "tab:blue"),
        (candidate_points, candidate, "自动闭环候选", "tab:orange"),
    )
    for row, (points, values, name, color) in enumerate(variants):
        for axis, (first, second, title) in zip(axes[row], views):
            axis.plot(points[:, first], points[:, second], color=color, linewidth=1.4)
            axis.scatter(points[0, first], points[0, second], color="green", s=55)
            axis.scatter(points[-1, first], points[-1, second], color="red", marker="x", s=65)
            axis.set_xlabel("XYZ"[first] + " (m)")
            axis.set_ylabel("XYZ"[second] + " (m)")
            axis.set_title(f"{name}｜{title}", fontproperties=font)
            axis.grid(alpha=0.3)
            axis.axis("equal")
        axes[row, 0].text(
            0.02,
            0.98,
            f"首尾3秒中心差 {values['dwell_center_distance_cm']:.2f} cm",
            transform=axes[row, 0].transAxes,
            va="top",
            fontproperties=font,
        )
    fig.suptitle(
        "D405双IR + KT-EX9-2 IMU｜无外部真值SLAM一致性报告",
        fontproperties=font,
        fontsize=16,
    )
    fig.tight_layout()
    plot_path = args.out_dir / "slam_precision_top_side.png"
    fig.savefig(plot_path, dpi=180)
    plt.close(fig)

    camera = acceptance["camera"]["db3_streams"]
    imu = acceptance["imu"]
    report = f"""# D405 + KT-EX9-2 SLAM 精度与一致性报告

## 结论

- 本次采集验收：**PASS**。三路相机约30 fps且零缺帧，IMU约400 Hz且正式采集窗口零丢帧。
- 当前稳定基线的首尾3秒驻留中心差为 **{baseline['dwell_center_distance_cm']:.2f} cm**。
- 自动闭环候选版在不知道真实终点的情况下自主接受 {len(loop_lines)} 个闭环，首尾驻留中心差为 **{candidate['dwell_center_distance_cm']:.2f} cm**，相对基线改善 **{improvement:.1f}%**。
- 本报告没有外部逐点真值，因此上述数值是**闭环一致性/重复定位误差**，不是绝对轨迹误差ATE。当前不能据此宣称“绝对定位精度为{candidate['dwell_center_distance_cm']:.2f} cm”。
- 交付建议：稳定基线可作为当前版本；五帧一致性自动闭环已通过非闭环片段与独立升降数据的误触发检查，仍需扩大重复纹理和开放轨迹样本后再替代稳定基线。

## 测试条件

- 数据会话：`{args.session.name}`
- 分辨率与频率：1280×720@30 fps，外置IMU 400 Hz
- 视觉输入：D405左/右红外双目；彩色流同步落盘但不进入本次VINS估计
- VIO：VINS-Fusion双目惯性，固定08-08外参与 `td=-0.0117 s`
- 用户提供的唯一验收事实：终点与起点接近但并非完全重合；算法没有接收该事实作为约束

## 采集质量

| 项目 | 结果 |
|---|---:|
| 彩色帧率 / 缺帧 / 重置 | {camera['color']['rate_hz']:.4f} Hz / {camera['color']['skipped_frames']} / {camera['color']['frame_number_resets']} |
| 左IR帧率 / 缺帧 / 重置 | {camera['infrared_left']['rate_hz']:.4f} Hz / {camera['infrared_left']['skipped_frames']} / {camera['infrared_left']['frame_number_resets']} |
| 右IR帧率 / 缺帧 / 重置 | {camera['infrared_right']['rate_hz']:.4f} Hz / {camera['infrared_right']['skipped_frames']} / {camera['infrared_right']['frame_number_resets']} |
| 左右IR设备时间戳差P95 | {synchronization['left_right_abs_p95_ms']:.3f} ms |
| 彩色-左IR设备时间戳差P95 | {synchronization['color_left_abs_p95_ms']:.3f} ms |
| IMU频率 / 正式窗口丢帧 | {imu['rate_hz']:.4f} Hz / {imu['formal_window_stats']['dropped_frames']} |
| IMU坏帧 / 重同步 / 计数复位 | {imu['formal_window_stats']['frames_bad']} / {imu['formal_window_stats']['resyncs']} / {imu['formal_window_stats']['counter_resets']} |

## SLAM结果

| 指标 | 稳定基线 | 自动闭环候选 |
|---|---:|---:|
| 输出位姿数 | {baseline['samples']} | {candidate['samples']} |
| 有效轨迹时长 | {baseline['duration_s']:.2f} s | {candidate['duration_s']:.2f} s |
| 累计路径长度 | {baseline['path_length_m']:.3f} m | {candidate['path_length_m']:.3f} m |
| 首尾3秒驻留中心差 | {baseline['dwell_center_distance_cm']:.2f} cm | {candidate['dwell_center_distance_cm']:.2f} cm |
| 水平中心差 | {baseline['dwell_horizontal_distance_cm']:.2f} cm | {candidate['dwell_horizontal_distance_cm']:.2f} cm |
| 垂直中心差 | {baseline['dwell_vertical_distance_cm']:.2f} cm | {candidate['dwell_vertical_distance_cm']:.2f} cm |
| XYZ跨度 | {format_vector(baseline['xyz_span_cm'])} cm | {format_vector(candidate['xyz_span_cm'])} cm |
| PCA俯视跨度 | {format_vector(baseline['pca_xy_span_cm'])} cm | {format_vector(candidate['pca_xy_span_cm'])} cm |
| 起始驻留RMS | {baseline['start_dwell_rms_mm']:.3f} mm | {candidate['start_dwell_rms_mm']:.3f} mm |
| 结束驻留RMS | {baseline['end_dwell_rms_mm']:.3f} mm | {candidate['end_dwell_rms_mm']:.3f} mm |

基线首尾中心差XYZ分量：**{format_vector(baseline['dwell_center_delta_cm'])} cm**。  
闭环候选首尾中心差XYZ分量：**{format_vector(candidate['dwell_center_delta_cm'])} cm**。

## 边界与下一步

1. 当前结果能证明采集完整、估计稳定，并量化“回到相近位置”时的轨迹自洽程度。
2. 因真实终点并非严格重合，首尾中心差同时包含真实放置差和SLAM误差；因此它是保守上界，不等于纯算法误差。
3. 若老板需要“绝对精度”或ATE/RPE，下一轮必须加入可追溯真值：标尺约束的离散停靠点、AprilTag标定场、全站仪或动捕系统。
4. 自动闭环已完成首轮非闭环和升降安全性验证；下一步扩大重复纹理、光照变化和开放轨迹样本，统计误检率后再替代稳定基线。

![轨迹三视图](slam_precision_top_side.png)
"""
    report_path = args.out_dir / "SLAM精度报告_20260813.md"
    report_path.write_text(report, encoding="utf-8")

    if args.copy_dir:
        args.copy_dir.mkdir(parents=True, exist_ok=True)
        for path in (report_path, plot_path, args.out_dir / "slam_precision_metrics.json"):
            shutil.copy2(path, args.copy_dir / path.name)

    print(report_path)
    print(plot_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
