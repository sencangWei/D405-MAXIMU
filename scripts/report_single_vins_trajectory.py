#!/usr/bin/env python3
"""Generate a compact Chinese report and top/side plots for one VINS trajectory."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open()))
    if len(rows) < 2:
        raise RuntimeError(f"轨迹点不足: {len(rows)}")
    time_s = np.array([float(row["t_sec"]) for row in rows])
    points = np.array(
        [[float(row["x"]), float(row["y"]), float(row["z"])] for row in rows]
    )
    return time_s, points


def plane_metrics(points: np.ndarray) -> tuple[float, float, float]:
    design = np.column_stack((points[:, 0], points[:, 1], np.ones(len(points))))
    coefficients = np.linalg.lstsq(design, points[:, 2], rcond=None)[0]
    residual = points[:, 2] - design @ coefficients
    tilt_deg = float(np.degrees(np.arctan(np.linalg.norm(coefficients[:2]))))
    return (
        tilt_deg,
        float(np.std(residual) * 1000.0),
        float((np.percentile(residual, 95) - np.percentile(residual, 5)) * 1000.0),
    )


def resample_by_arc_length(points: np.ndarray, count: int = 1000) -> np.ndarray:
    if len(points) < 2:
        raise ValueError("至少需要两个轨迹点")
    segment_lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    keep = np.r_[True, segment_lengths > 1e-9]
    unique_points = points[keep]
    if len(unique_points) < 2:
        raise ValueError("轨迹没有可测运动")
    distances = np.r_[
        0.0,
        np.cumsum(np.linalg.norm(np.diff(unique_points, axis=0), axis=1)),
    ]
    targets = np.linspace(0.0, distances[-1], count)
    return np.column_stack(
        [np.interp(targets, distances, unique_points[:, axis]) for axis in range(2)]
    )


def horizontal_rectangle_metrics(points_xy: np.ndarray) -> dict[str, object]:
    sampled = resample_by_arc_length(points_xy)
    rectangle = cv2.minAreaRect(sampled.astype(np.float32))
    box = cv2.boxPoints(rectangle).astype(float)
    edges = np.roll(box, -1, axis=0) - box
    edge_lengths = np.linalg.norm(edges, axis=1)
    extent_cm = np.sort(np.asarray(rectangle[1], dtype=float) * 100.0)[::-1]
    line_distances = []
    for point, edge, length in zip(box, edges, edge_lengths):
        offsets = sampled - point
        line_distances.append(
            np.abs(edge[0] * offsets[:, 1] - edge[1] * offsets[:, 0]) / length
        )
    boundary_distance = np.min(np.column_stack(line_distances), axis=1)
    return {
        "method": "uniform_arc_length_minimum_area_rectangle",
        "resampled_points": len(sampled),
        "robust_extent_cm": extent_cm.tolist(),
        "boundary_rms_mm": float(np.sqrt(np.mean(boundary_distance**2)) * 1000.0),
        "boundary_p95_mm": float(np.percentile(boundary_distance, 95) * 1000.0),
    }


def dwell_closure_metrics(
    time_s: np.ndarray,
    points: np.ndarray,
    window_s: float,
) -> dict[str, object]:
    if window_s <= 0:
        raise ValueError("静止窗口必须大于0秒")
    start = points[time_s <= time_s[0] + window_s]
    end = points[time_s >= time_s[-1] - window_s]
    if len(start) == 0 or len(end) == 0:
        raise ValueError("起止静止窗口没有轨迹样本")
    start_center = np.median(start, axis=0)
    end_center = np.median(end, axis=0)
    delta_cm = (end_center - start_center) * 100.0
    return {
        "window_s": window_s,
        "start_samples": len(start),
        "end_samples": len(end),
        "start_center_m": start_center.tolist(),
        "end_center_m": end_center.tolist(),
        "delta_xyz_cm": delta_cm.tolist(),
        "distance_cm": float(np.linalg.norm(delta_cm)),
        "horizontal_distance_cm": float(np.linalg.norm(delta_cm[:2])),
        "vertical_distance_cm": float(abs(delta_cm[2])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trajectory", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--expected-x-cm", type=float)
    parser.add_argument("--expected-y-cm", type=float)
    parser.add_argument("--dwell-window-s", type=float, default=3.0)
    args = parser.parse_args()
    if (args.expected_x_cm is None) != (args.expected_y_cm is None):
        parser.error("--expected-x-cm 与 --expected-y-cm 必须同时提供")

    time_s, points = load_trajectory(args.trajectory)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    acceptance_path = args.trajectory.parent / "acceptance.json"
    acceptance = (
        json.loads(acceptance_path.read_text(encoding="utf-8"))
        if acceptance_path.exists()
        else None
    )

    segments = np.linalg.norm(np.diff(points, axis=0), axis=1)
    point_closure_delta_cm = (points[-1] - points[0]) * 100.0
    point_closure_cm = float(np.linalg.norm(point_closure_delta_cm))
    dwell_closure = dwell_closure_metrics(time_s, points, args.dwell_window_s)
    closure_cm = float(dwell_closure["distance_cm"])
    path_m = float(segments.sum())
    z_span_cm = float(np.ptp(points[:, 2]) * 100.0)
    closure_delta_cm = np.asarray(dwell_closure["delta_xyz_cm"])
    horizontal_closure_cm = float(np.linalg.norm(closure_delta_cm[:2]))
    vertical_closure_cm = float(abs(closure_delta_cm[2]))
    world_axis_extent_cm = np.ptp(points[:, :2], axis=0) * 100.0
    rectangle = horizontal_rectangle_metrics(points[:, :2])
    horizontal_extent_cm = np.asarray(rectangle["robust_extent_cm"])
    expected_rectangle_cm = None
    horizontal_error_cm = None
    if args.expected_x_cm is not None:
        expected_rectangle_cm = [args.expected_x_cm, args.expected_y_cm]
        expected_extent_cm = np.sort(np.array(expected_rectangle_cm))[::-1]
        horizontal_error_cm = horizontal_extent_cm - expected_extent_cm

    low_level = points[:, 2] <= points[:, 2].min() + 0.02
    low_points = points[low_level]
    tilt_deg, z_std_mm, z90_mm = plane_metrics(low_points)

    centered_xy = points[:, :2] - points[:, :2].mean(axis=0)
    _, _, axes = np.linalg.svd(centered_xy, full_matrices=False)
    principal_xy = centered_xy @ axes.T
    pca_cm = np.ptp(principal_xy, axis=0) * 100.0
    pca_p1p99_cm = (
        np.percentile(principal_xy, 99, axis=0)
        - np.percentile(principal_xy, 1, axis=0)
    ) * 100.0

    metrics = {
        "trajectory_points": len(points),
        "duration_s": float(time_s[-1] - time_s[0]),
        "path_m": path_m,
        "path_interpretation": (
            "sum_of_sample_to_sample_displacements_including_jitter_and_dwell; "
            "not rectangle side length or surveyed perimeter"
        ),
        "point_closure_cm": point_closure_cm,
        "point_closure_delta_xyz_cm": point_closure_delta_cm.tolist(),
        "closure_cm": closure_cm,
        "closure_method": "start_end_dwell_median",
        "dwell_closure": dwell_closure,
        "horizontal_closure_cm": horizontal_closure_cm,
        "vertical_closure_cm": vertical_closure_cm,
        "expected_rectangle_cm": expected_rectangle_cm,
        "world_axis_horizontal_extent_cm": world_axis_extent_cm.tolist(),
        "horizontal_extent_cm": horizontal_extent_cm.tolist(),
        "horizontal_extent_error_cm": (
            horizontal_error_cm.tolist() if horizontal_error_cm is not None else None
        ),
        "horizontal_rectangle_geometry": rectangle,
        "pca_extent_cm": pca_cm.tolist(),
        "pca_p1_p99_extent_cm": pca_p1p99_cm.tolist(),
        "xyz_span_cm": (np.ptp(points, axis=0) * 100.0).tolist(),
        "z_min_cm": float(points[:, 2].min() * 100.0),
        "z_max_cm": float(points[:, 2].max() * 100.0),
        "z_span_cm": z_span_cm,
        "low_level_points": int(low_level.sum()),
        "low_level_plane_tilt_deg": tilt_deg,
        "low_level_plane_residual_std_mm": z_std_mm,
        "low_level_plane_residual_p5_p95_mm": z90_mm,
    }
    capture_lines = []
    if acceptance:
        duration_s = float(acceptance.get("duration_s", 0.0))
        synced_frames = int(acceptance.get("complete_framesets_for_csv", 0))
        synced_rate_hz = synced_frames / duration_s if duration_s > 0 else 0.0
        db3_streams = acceptance.get("camera", {}).get("db3_streams", {})
        gap_ratios = {
            name: float(values.get("gap_ratio", 0.0))
            for name, values in db3_streams.items()
        }
        metrics["capture"] = {
            "result": acceptance.get("result"),
            "synced_frames": synced_frames,
            "effective_synced_rate_hz": synced_rate_hz,
            "camera_gap_ratios": gap_ratios,
            "imu_result": acceptance.get("imu", {}).get("result"),
            "imu_rate_hz": acceptance.get("imu", {}).get("rate_hz"),
            "imu_formal_window_stats": acceptance.get("imu", {}).get(
                "formal_window_stats"
            ),
        }
        capture_lines = [
            f"采集验收: {acceptance.get('result')}",
            f"有效同步图像: {synced_frames}帧 / {synced_rate_hz:.2f} Hz",
            "相机缺帧率: "
            + ", ".join(
                f"{name}={ratio * 100:.2f}%" for name, ratio in gap_ratios.items()
            ),
            f"IMU验收: {acceptance.get('imu', {}).get('result')} / "
            f"{float(acceptance.get('imu', {}).get('rate_hz', 0.0)):.2f} Hz",
        ]
    (args.out_dir / "trajectory_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    target_lines = ""
    if expected_rectangle_cm is not None:
        target_lines = (
            f"外部实测目标矩形: {args.expected_x_cm:.1f} × "
            f"{args.expected_y_cm:.1f} cm\n"
            f"俯视尺寸误差: {horizontal_error_cm[0]:+.1f} / "
            f"{horizontal_error_cm[1]:+.1f} cm\n"
        )

    report = (
        "VINS 双IR+IMU 轨迹误差报告\n"
        + ("\n".join(capture_lines) + "\n\n" if capture_lines else "")
        +
        f"轨迹点数: {len(points)}\n"
        f"有效时长: {metrics['duration_s']:.2f} s\n"
        f"累计采样步长（含抖动/停靠，不等于矩形边长或实测周长）: {path_m:.3f} m\n"
        f"静止窗口中位数闭环误差: {closure_cm:.2f} cm\n"
        f"单帧首尾闭环误差: {point_closure_cm:.2f} cm\n"
        f"闭环XYZ有符号差: {closure_delta_cm[0]:+.2f} / "
        f"{closure_delta_cm[1]:+.2f} / {closure_delta_cm[2]:+.2f} cm\n"
        f"水平闭环误差: {horizontal_closure_cm:.2f} cm\n"
        f"垂直闭环误差: {vertical_closure_cm:.2f} cm\n"
        + target_lines
        +
        f"世界XY轴向范围: {world_axis_extent_cm[0]:.1f} × {world_axis_extent_cm[1]:.1f} cm\n"
        f"旋转不变矩形范围: {horizontal_extent_cm[0]:.1f} × {horizontal_extent_cm[1]:.1f} cm\n"
        f"矩形边界RMS/P95: {rectangle['boundary_rms_mm']:.2f} / "
        f"{rectangle['boundary_p95_mm']:.2f} mm\n"
        f"轨迹PCA外包: {pca_cm[0]:.1f} × {pca_cm[1]:.1f} cm\n"
        f"轨迹PCA 1%-99%: {pca_p1p99_cm[0]:.1f} × {pca_p1p99_cm[1]:.1f} cm\n"
        f"XYZ范围: {metrics['xyz_span_cm'][0]:.1f} × "
        f"{metrics['xyz_span_cm'][1]:.1f} × {metrics['xyz_span_cm'][2]:.1f} cm\n"
        f"Z最低/最高: {metrics['z_min_cm']:.1f} / {metrics['z_max_cm']:.1f} cm\n"
        f"低位段点数: {low_level.sum()}\n"
        f"低位段平面倾角: {tilt_deg:.2f}°\n"
        f"低位段去平面Z标准差: {z_std_mm:.2f} mm\n"
        f"低位段去平面Z 5%-95%: {z90_mm:.2f} mm\n"
    )
    (args.out_dir / "trajectory_report.txt").write_text(report, encoding="utf-8")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    chinese_font = font_manager.FontProperties(
        fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    )

    fig, axes_plot = plt.subplots(1, 3, figsize=(18, 6))
    views = (
        (0, 1, "俯视图 X-Y"),
        (0, 2, "侧视图 X-Z"),
        (1, 2, "侧视图 Y-Z"),
    )
    for axis, (first, second, title) in zip(axes_plot, views):
        axis.plot(points[:, first], points[:, second], color="tab:blue", linewidth=1.6)
        axis.scatter(points[0, first], points[0, second], color="green", s=80, label="起点")
        axis.scatter(points[-1, first], points[-1, second], color="red", marker="x", s=90, label="终点")
        axis.set_xlabel("XYZ"[first] + " (m)")
        axis.set_ylabel("XYZ"[second] + " (m)")
        axis.set_title(title, fontproperties=chinese_font)
        axis.grid(alpha=0.3)
        axis.axis("equal")
        axis.legend(prop=chinese_font)
    fig.suptitle(
        f"VINS轨迹｜闭环 {closure_cm:.1f}cm｜累计采样步长 {path_m:.2f}m｜Z跨度 {z_span_cm:.1f}cm",
        fontproperties=chinese_font,
    )
    fig.tight_layout()
    fig.savefig(args.out_dir / "trajectory_top_side.png", dpi=170)
    plt.close(fig)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
