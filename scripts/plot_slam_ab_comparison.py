#!/usr/bin/env python3
"""Plot before/after SLAM trajectories from the same captured sensor data."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


def load_points(path: Path) -> np.ndarray:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if len(rows) < 2:
        raise ValueError(f"trajectory has insufficient points: {path}")
    return np.array(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )


def minimum_rectangle_cm(points: np.ndarray) -> np.ndarray:
    rectangle = cv2.minAreaRect(points[:, :2].astype(np.float32))
    return np.sort(np.asarray(rectangle[1], dtype=float) * 100.0)[::-1]


def endpoint_mm(points: np.ndarray) -> float:
    return float(np.linalg.norm(points[-1] - points[0]) * 1000.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    before = load_points(args.before)
    after = load_points(args.after)
    before_extent = minimum_rectangle_cm(before)
    after_extent = minimum_rectangle_cm(after)

    plt.rcParams["axes.unicode_minus"] = False
    chinese_font = font_manager.FontProperties(
        fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    )
    figure, axes = plt.subplots(1, 3, figsize=(18, 6))
    views = ((0, 1, "俯视 X-Y"), (0, 2, "侧视 X-Z"), (1, 2, "侧视 Y-Z"))
    for axis, (horizontal, vertical, title) in zip(axes, views):
        axis.plot(
            before[:, horizontal], before[:, vertical], color="#d95f02",
            alpha=0.78, linewidth=1.4, label=f"优化前 {endpoint_mm(before):.1f} mm"
        )
        axis.plot(
            after[:, horizontal], after[:, vertical], color="#1b9e77",
            linewidth=1.7, label=f"优化后 {endpoint_mm(after):.1f} mm"
        )
        axis.scatter(after[0, horizontal], after[0, vertical], marker="o", s=60,
                     color="green", label="起点" if horizontal == 0 and vertical == 1 else None)
        axis.scatter(after[-1, horizontal], after[-1, vertical], marker="x", s=70,
                     color="red", label="终点" if horizontal == 0 and vertical == 1 else None)
        axis.set_title(title, fontproperties=chinese_font)
        axis.set_xlabel("XYZ"[horizontal] + " (m)")
        axis.set_ylabel("XYZ"[vertical] + " (m)")
        axis.grid(True, alpha=0.3)
        axis.axis("equal")
        axis.legend(loc="best", prop=chinese_font)

    figure.suptitle(
        "同一份原始采集：自动闭环几何证据平滑后处理\n"
        f"优化前矩形外包 {before_extent[0]:.1f}×{before_extent[1]:.1f} cm，"
        f"优化后 {after_extent[0]:.1f}×{after_extent[1]:.1f} cm；"
        "未使用目标尺寸或人工终点",
        fontsize=14, fontproperties=chinese_font,
    )
    figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.91))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=180)
    plt.close(figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
