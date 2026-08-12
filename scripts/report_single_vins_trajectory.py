#!/usr/bin/env python3
"""Generate a compact Chinese report and top/side plots for one VINS trajectory."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trajectory", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--expected-x-cm", type=float, default=82.0)
    parser.add_argument("--expected-y-cm", type=float, default=63.0)
    args = parser.parse_args()

    time_s, points = load_trajectory(args.trajectory)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    acceptance_path = args.trajectory.parent / "acceptance.json"
    acceptance = (
        json.loads(acceptance_path.read_text(encoding="utf-8"))
        if acceptance_path.exists()
        else None
    )

    segments = np.linalg.norm(np.diff(points, axis=0), axis=1)
    closure_cm = float(np.linalg.norm(points[-1] - points[0]) * 100.0)
    path_m = float(segments.sum())
    z_span_cm = float(np.ptp(points[:, 2]) * 100.0)
    closure_delta_cm = (points[-1] - points[0]) * 100.0
    horizontal_closure_cm = float(np.linalg.norm(closure_delta_cm[:2]))
    vertical_closure_cm = float(abs(closure_delta_cm[2]))
    horizontal_extent_cm = np.sort(np.ptp(points[:, :2], axis=0) * 100.0)[::-1]
    expected_extent_cm = np.sort(
        np.array([args.expected_x_cm, args.expected_y_cm])
    )[::-1]
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
        "closure_cm": closure_cm,
        "horizontal_closure_cm": horizontal_closure_cm,
        "vertical_closure_cm": vertical_closure_cm,
        "expected_rectangle_cm": [args.expected_x_cm, args.expected_y_cm],
        "horizontal_extent_cm": horizontal_extent_cm.tolist(),
        "horizontal_extent_error_cm": horizontal_error_cm.tolist(),
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

    report = (
        "VINS 双IR+IMU 轨迹误差报告\n"
        + ("\n".join(capture_lines) + "\n\n" if capture_lines else "")
        +
        f"轨迹点数: {len(points)}\n"
        f"有效时长: {metrics['duration_s']:.2f} s\n"
        f"累计路径: {path_m:.3f} m\n"
        f"闭环误差: {closure_cm:.2f} cm\n"
        f"水平闭环误差: {horizontal_closure_cm:.2f} cm\n"
        f"垂直闭环误差: {vertical_closure_cm:.2f} cm\n"
        f"目标矩形: {args.expected_x_cm:.1f} × {args.expected_y_cm:.1f} cm\n"
        f"俯视轴向范围: {horizontal_extent_cm[0]:.1f} × {horizontal_extent_cm[1]:.1f} cm\n"
        f"俯视尺寸误差: {horizontal_error_cm[0]:+.1f} / {horizontal_error_cm[1]:+.1f} cm\n"
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
        f"VINS轨迹｜闭环 {closure_cm:.1f}cm｜路径 {path_m:.2f}m｜Z跨度 {z_span_cm:.1f}cm",
        fontproperties=chinese_font,
    )
    fig.tight_layout()
    fig.savefig(args.out_dir / "trajectory_top_side.png", dpi=170)
    plt.close(fig)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
