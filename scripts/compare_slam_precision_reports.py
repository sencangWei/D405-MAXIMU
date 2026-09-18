#!/usr/bin/env python3
"""Write a compact common-timeline Docker2 versus MASt3R-SLAM report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


METRICS = (
    ("ATE RMSE", "ate_translation_rmse_m", 1000.0, "mm"),
    ("ATE mean", "ate_translation_mean_m", 1000.0, "mm"),
    ("ATE median", "ate_translation_median_m", 1000.0, "mm"),
    ("ATE P95", "ate_translation_p95_m", 1000.0, "mm"),
    ("ATE max", "ate_translation_max_m", 1000.0, "mm"),
    ("rotation RMSE", "ate_rotation_rmse_deg", 1.0, "deg"),
    ("RPE translation RMSE", "rpe_translation_rmse_m", 1000.0, "mm"),
    ("Sim(3) shape RMSE (diagnostic)", "sim3_diagnostic_ate_rmse_m", 1000.0, "mm"),
    ("Sim(3) scale gt/estimate", "sim3_diagnostic_scale_gt_per_estimate", 1.0, "x"),
)


def compare(
    docker2: dict,
    mast3r: dict,
    mast3r_stereo: dict | None = None,
    fusion: dict | None = None,
) -> dict:
    algorithms = {"docker2": docker2, "mast3r_slam": mast3r}
    if mast3r_stereo is not None:
        algorithms["mast3r_stereo"] = mast3r_stereo
    if fusion is not None:
        algorithms["mast3r_stereo_imu"] = fusion
    for name, report in algorithms.items():
        if report["alignment"] != "SE3_estimate_to_external_ground_truth_no_scale":
            raise ValueError(f"{name} report is not no-scale SE(3)")
    sample_counts = {
        report["timestamp_overlap_samples"] for report in algorithms.values()
    }
    if len(sample_counts) != 1:
        raise ValueError("reports were not evaluated on the same timestamp count")
    rmse = {
        name: report["ate_translation_rmse_m"] for name, report in algorithms.items()
    }
    best = min(rmse.values())
    winners = [name for name, value in rmse.items() if value == best]
    winner = winners[0] if len(winners) == 1 else "tie"
    result = {
        "schema": "umi_slam_comparison_v3",
        "comparison": "same_MASt3R_tracked_frame_timestamps_same_Lighthouse_body_ground_truth",
        "alignment": "SE3_no_scale",
        "slam_supervision": False,
        "winner_by_ate_rmse": winner,
        "samples": sample_counts.pop(),
        "docker2": docker2,
        "mast3r_slam": mast3r,
        "license_notice": (
            "MASt3R-SLAM code is CC BY-NC-SA 4.0 and its checkpoints have "
            "additional dataset licenses; this result is an internal benchmark, "
            "not commercial deployment clearance."
        ),
    }
    if mast3r_stereo is not None:
        result["mast3r_stereo"] = mast3r_stereo
    if fusion is not None:
        result["mast3r_stereo_imu"] = fusion
    return result


def markdown(report: dict) -> str:
    algorithms = [
        ("Docker2", report["docker2"]),
        ("MASt3R-SLAM", report["mast3r_slam"]),
    ]
    if "mast3r_stereo" in report:
        algorithms.append(("MASt3R-Stereo", report["mast3r_stereo"]))
    if "mast3r_stereo_imu" in report:
        algorithms.append(("MASt3R-Stereo-IMU", report["mast3r_stereo_imu"]))
    lines = [
        "# UMI 后处理 SLAM 统一精度对比",
        "",
        f"结论（按 ATE RMSE）：**{report['winner_by_ate_rmse']}**",
        "",
        "所有轨迹使用相同 MASt3R 实际跟踪帧时间、同一 Lighthouse IMU/body原点真值，"
        "仅做 SE(3) 对齐，禁止缩放；Lighthouse/TCP 均不输入 SLAM。",
        "",
        "| 指标 | " + " | ".join(name for name, _ in algorithms) + " |",
        "| --- | " + " | ".join("---:" for _ in algorithms) + " |",
    ]
    for label, key, factor, unit in METRICS:
        values = " | ".join(
            f"{algorithm[key] * factor:.3f} {unit}"
            for _, algorithm in algorithms
        )
        lines.append(f"| {label} | {values} |")
    within_10mm = " | ".join(
        f"{algorithm['ate_translation_within_10mm_ratio'] * 100:.3f}%"
        for _, algorithm in algorithms
    )
    lines.extend(
        [
            f"| within 10 mm | {within_10mm} |",
            "",
            f"样本数：{report['samples']}。",
            "",
            "许可提醒：MASt3R-SLAM 官方代码为 CC BY-NC-SA 4.0，权重还受训练数据许可约束；"
            "当前结果仅代表内部技术评测，不代表已获商业部署授权。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker2", type=Path, required=True)
    parser.add_argument("--mast3r", type=Path, required=True)
    parser.add_argument("--mast3r-stereo", type=Path)
    parser.add_argument("--fusion", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()
    report = compare(
        json.loads(args.docker2.read_text(encoding="utf-8")),
        json.loads(args.mast3r.read_text(encoding="utf-8")),
        json.loads(args.mast3r_stereo.read_text(encoding="utf-8"))
        if args.mast3r_stereo
        else None,
        json.loads(args.fusion.read_text(encoding="utf-8"))
        if args.fusion
        else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.report_md.write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
