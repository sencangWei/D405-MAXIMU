#!/usr/bin/env python3
"""Overlay a trajectory after fitting one rigid transform to a selected region.

This is a visualization aid for checking whether a visibly separated segment is
mostly a frame-placement issue.  The selected region is fitted once; the
resulting transform is then applied to the complete UMI trajectory.  No input
trajectory is changed and no local warp is performed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from generate_max_overlap_overlay import apply, html_payload, kabsch, load_matched, stats, write_static_plot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matched-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    times, umi, robot = load_matched(args.matched_csv.resolve())
    # In this recording the lower-right arc is the portion with robot X > 0.4 m
    # and robot Y < 0.  This is a spatial selection, not an endpoint constraint.
    fit_mask = (robot[:, 0] > 0.4) & (robot[:, 1] < 0.0)
    if int(fit_mask.sum()) < 20:
        raise SystemExit(f"右下长弧有效点不足: {int(fit_mask.sum())}")

    rotation, translation = kabsch(umi[fit_mask], robot[fit_mask])
    aligned = apply(umi, rotation, translation)
    fit_error_mm = np.linalg.norm(aligned[fit_mask] - robot[fit_mask], axis=1) * 1000.0
    report = {
        "schema": "region_overlap_overlay_v1",
        "input_matched_csv": str(args.matched_csv.resolve()),
        "points": int(len(times)),
        "duration_s": float(times[-1] - times[0]),
        "fit_region": "robot_actual_x > 0.4 m and robot_actual_y < 0 m (right/lower arc)",
        "fit_points": int(fit_mask.sum()),
        "rotation_umi_to_robot": rotation.tolist(),
        "translation_m_umi_to_robot": translation.tolist(),
        "fit_region_rmse_mm": float(np.sqrt(np.mean(fit_error_mm**2))),
        "fit_region_p95_mm": float(np.percentile(fit_error_mm, 95)),
        "fit_region_max_mm": float(np.max(fit_error_mm)),
        "full_curve_nearest_overlap": stats(aligned, robot),
        "interpretation": "仅把右下长弧作为诊断拟合区，完整绿色轨迹使用同一个固定变换显示；不能当作整段绝对精度。",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "right_arc_overlap_overlay.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    html = html_payload(robot, umi, aligned, {"overlap": report["full_curve_nearest_overlap"]})
    html = html.replace("几何最大重合轨迹", "右下长弧重合诊断")
    html = html.replace("UMI 最大重合放置", "UMI 按右下长弧拟合")
    html = html.replace(
        "白色=机械臂原始 TCP；绿色=UMI 经过一次最大重合刚体放置后的轨迹。拖动旋转，滚轮缩放；不按时间点强行配对，不改变任何原始轨迹。",
        "白色=机械臂原始 TCP；绿色=按右下长弧拟合后应用到整条 UMI 的轨迹。拖动旋转，滚轮缩放；原始轨迹未修改。",
    )
    (args.output_dir / "right_arc_overlap_overlay.html").write_text(html, encoding="utf-8")
    write_static_plot(args.output_dir, robot, aligned, report)
    (args.output_dir / "max_overlap_overlay_3d.png").rename(args.output_dir / "right_arc_overlap_overlay_3d.png")

    md = [
        "# 右下长弧重合诊断", "",
        "仅用右下长弧空间区域求一次固定 SE(3)，再把同一个变换应用到完整绿色轨迹。这样可以观察该段重合后其他段的真实形状差异；不修改任何输入轨迹。", "",
        f"- 总点数：{len(times)}", f"- 拟合区域点数：{int(fit_mask.sum())}",
        f"- 拟合区域 RMSE：{report['fit_region_rmse_mm']:.3f} mm",
        f"- 拟合区域 P95：{report['fit_region_p95_mm']:.3f} mm",
        f"- 拟合区域最大值：{report['fit_region_max_mm']:.3f} mm", "",
        "## 文件", "",
        "- `right_arc_overlap_overlay.html`：可旋转三维图",
        "- `right_arc_overlap_overlay_3d.png`：静态三维图",
        "- `right_arc_overlap_overlay.json`：拟合区域和完整曲线统计",
    ]
    (args.output_dir / "right_arc_overlap_overlay.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
