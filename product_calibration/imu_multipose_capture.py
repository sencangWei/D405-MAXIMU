"""Shared arbitrary-pose IMU capture with per-pose stability and transport gates."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np

from .imu_analysis import load_capture
from .imu_stream import capture_serial
from .workflow import sha256_file


MAX_TRIALS_PER_POSE = 3


class MultiposeCaptureFailure(ValueError):
    pass


class MultiposeCaptureBlocked(ValueError):
    pass


def _save_capture_stop(
    attempt: Path, *, result: str, reason: str, pose_reports: list[dict]
) -> None:
    (attempt / "capture_stop.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "result": result,
                "reason": reason,
                "pose_capture_health": pose_reports,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def pose_stable(samples) -> tuple[bool, dict]:
    gyro = np.asarray(samples["gyro_deg_s"], dtype=float)
    accel = np.asarray(samples["accel_g"], dtype=float)
    metrics = {
        "gyro_std_max_deg_s": float(np.max(np.std(gyro, axis=0))),
        "accel_std_max_g": float(np.max(np.std(accel, axis=0))),
    }
    passed = metrics["gyro_std_max_deg_s"] <= 0.15 and metrics["accel_std_max_g"] <= 0.006
    return passed, metrics


def transport_clean(stats: dict, *, required_duration_s: float | None = None) -> bool:
    duration_ok = (
        required_duration_s is None
        or stats["duration_s"] >= required_duration_s * 0.995
    )
    return all(
        (
            duration_ok,
            395.0 <= stats["rate_hz"] <= 405.0,
            stats["counter_gaps"] == 0,
            stats["sequence_gaps"] == 0,
            stats["crc_or_checksum_errors"] == 0,
            stats["discarded_bytes"] == 0,
            stats["invalid_imu_flags"] == 0,
            stats["queue_overflow_flags"] == 0,
            not stats["interrupted"],
        )
    )


def accepted_source_pose_ids(
    pose_reports: list[dict], *, required_duration_s: float
) -> tuple[set[str], dict[str, str]]:
    """Return source poses whose final trial still passes today's capture gates."""
    latest = {}
    for report in pose_reports:
        latest[report["pose_id"]] = report
    accepted = set()
    excluded = {}
    for pose_id, report in latest.items():
        stable = (
            report["gyro_std_max_deg_s"] <= 0.15
            and report["accel_std_max_g"] <= 0.006
        )
        clean = transport_clean(
            report["capture_health"], required_duration_s=required_duration_s
        )
        if stable and clean:
            accepted.add(pose_id)
        else:
            excluded[pose_id] = "capture_health"
    return accepted, excluded


def capture_pose_csv(
    *,
    port: str,
    baud: int,
    protocol: str,
    pose_duration_s: float,
    attempt: Path,
    capture_fn: Callable = capture_serial,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    max_trials_per_pose: int = MAX_TRIALS_PER_POSE,
) -> tuple[Path, list[dict]]:
    csv_path = attempt / "imu_multipose.csv"
    pose_reports = []
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["pose_id", "split", "ax", "ay", "az"])
        writer.writeheader()
        for index in range(30):
            split = "fit" if index < 20 else "validation"
            pose_id = f"P{index + 1:02d}"
            trial = 0
            while True:
                trial += 1
                purpose = "拟合" if split == "fit" else "独立验证"
                input_fn(
                    f"姿态 {index + 1}/30（{purpose}）：将整个固定支架摆到一个新的分散方向，"
                    "放稳、释放线缆拉力后按回车采集……"
                )
                pose_dir = attempt / "poses" / f"{pose_id}_try_{trial:02d}"
                stats = asdict(
                    capture_fn(
                        port=port,
                        baud=baud,
                        duration_s=pose_duration_s,
                        output_dir=pose_dir,
                        protocol=protocol,
                        write_timestamp_csv=False,
                        startup_discard_s=1.0,
                    )
                )
                if stats["interrupted"]:
                    pose_reports.append(
                        {
                            "pose_id": pose_id,
                            "split": split,
                            "trial": trial,
                            "transport_clean": False,
                            "capture_health": stats,
                            "capture_sha256": sha256_file(pose_dir / "imu.bin"),
                        }
                    )
                    reason = f"用户在{pose_id}采集中断；不会自动重试"
                    _save_capture_stop(
                        attempt, result="BLOCKED", reason=reason, pose_reports=pose_reports
                    )
                    raise MultiposeCaptureBlocked(reason)
                samples = load_capture(pose_dir / "imu.bin")
                stable, metrics = pose_stable(samples)
                clean = transport_clean(stats, required_duration_s=pose_duration_s)
                trial_report = {
                    "pose_id": pose_id,
                    "split": split,
                    "trial": trial,
                    **metrics,
                    "transport_clean": clean,
                    "capture_health": stats,
                    "capture_sha256": sha256_file(pose_dir / "imu.bin"),
                }
                pose_reports.append(trial_report)
                if stable and clean:
                    break
                print_fn(
                    f"{pose_id}本次未通过（静止={stable}，传输={clean}），失败数据已保留；"
                    "请重新放稳后只重采本姿态。"
                )
                if trial >= max_trials_per_pose:
                    reason = f"{pose_id}连续{max_trials_per_pose}次未通过，停止采集"
                    _save_capture_stop(
                        attempt, result="FAIL", reason=reason, pose_reports=pose_reports
                    )
                    raise MultiposeCaptureFailure(reason)
            mean = np.mean(np.asarray(samples["accel_g"], dtype=float), axis=0) * 9.80665
            writer.writerow(
                {"pose_id": pose_id, "split": split, "ax": mean[0], "ay": mean[1], "az": mean[2]}
            )
    return csv_path, pose_reports
