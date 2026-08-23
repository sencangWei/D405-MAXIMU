#!/usr/bin/env python3
"""Standalone IMU-only static and Allan bench captures.

These commands deliberately do not create or advance a product calibration session.
Their reports are useful engineering evidence but are never release eligible.
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from .imu_analysis import analyze_allan, analyze_static, load_capture
from .imu_ellipsoid import fit_and_validate, load_pose_means
from .imu_multipose_capture import (
    MultiposeCaptureFailure,
    accepted_source_pose_ids,
    capture_pose_csv,
    pose_stable,
    transport_clean,
)
from .imu_stream import capture_serial
from .workflow import sha256_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "imu_bench_results"
PRODUCT_ALLAN_MINIMUM_S = 6 * 3600.0
MAX_SUPPLEMENTAL_CANDIDATES = 12


class BenchError(RuntimeError):
    pass


class BenchFailure(BenchError):
    pass


def at_least_30_seconds(value: str) -> float:
    duration = float(value)
    if not math.isfinite(duration) or duration < 30.0:
        raise argparse.ArgumentTypeError("每个多姿态正式窗口不得短于30秒")
    return duration


def find_imu_port(requested: str | None) -> str:
    if requested:
        candidates = [requested]
    else:
        candidates = sorted(glob.glob("/dev/serial/by-id/*"))
        if len(candidates) != 1:
            raise BenchError(f"无法唯一识别IMU串口，找到{len(candidates)}个，请使用--port")
    path = Path(candidates[0])
    if not path.exists():
        raise BenchError(f"IMU串口不存在: {path}")
    if not os.access(path, os.R_OK | os.W_OK):
        raise BenchError(
            f"当前用户无权读写{path}；请加入dialout组并重新登录，禁止用chmod 666临时放开设备"
        )
    return str(path)


def new_attempt(output_root: Path, stage: str) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    attempt = Path(output_root).resolve() / f"{timestamp}_{stage}"
    attempt.mkdir(parents=True)
    return attempt


def save_yaml(path: Path, document: dict) -> None:
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def save_allan_plot(report: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 2, figsize=(12, 5))
    for axis, name in zip(axes, ("gyroscope", "accelerometer")):
        item = report[name]
        tau = np.asarray(item["tau_s"])
        deviation = np.asarray(item["allan_deviation_axes"])
        for index, label in enumerate("XYZ"):
            axis.loglog(tau, deviation[:, index], label=label)
        axis.set_title(name)
        axis.set_xlabel("tau (s)")
        axis.grid(True, which="both")
        axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def common_report(report: dict, capture_dir: Path, mode: str) -> dict:
    report.update(
        {
            "acceptance_scope": "bench_provisional",
            "release_eligible": False,
            "product_result": "BLOCKED",
            "product_blocked_reason": (
                "IMU-only台架采集未绑定D405、最终STM32、最终装配和正式产品身份"
            ),
            "evidence": {
                "mode": mode,
                "capture_dir": str(capture_dir.resolve()),
                "imu_bin_sha256": sha256_file(capture_dir / "imu.bin"),
                "summary_sha256": sha256_file(capture_dir / "summary.json"),
            },
        }
    )
    raw_packets = capture_dir / "raw_packets.bin"
    if raw_packets.is_file():
        report["evidence"]["raw_packets_sha256"] = sha256_file(raw_packets)
    return report


def capture_or_reuse(args, attempt: Path, *, write_timestamp_csv: bool) -> tuple[Path, str]:
    if args.input_capture:
        capture_dir = args.input_capture.resolve()
        if not (capture_dir / "imu.bin").is_file():
            raise BenchError(f"输入目录缺少imu.bin: {capture_dir}")
        if not (capture_dir / "summary.json").is_file():
            raise BenchError(f"输入目录缺少summary.json: {capture_dir}")
        return capture_dir, "offline_reanalysis"

    port = find_imu_port(args.port)
    capture_dir = attempt / "capture"
    input(
        f"IMU串口：{port}\n"
        f"将IMU刚性放稳、释放线缆拉力，确认采集期间不会被碰到后按回车开始……"
    )
    capture_serial(
        port=port,
        baud=args.baud,
        duration_s=args.duration,
        output_dir=capture_dir,
        protocol=args.protocol,
        write_timestamp_csv=write_timestamp_csv,
        startup_discard_s=1.0,
    )
    return capture_dir, "live_capture"


def run_static(args) -> int:
    attempt = new_attempt(args.output_root, "imu_static_bench")
    capture_dir, mode = capture_or_reuse(args, attempt, write_timestamp_csv=True)
    report = common_report(
        analyze_static(capture_dir, warmup_s=args.warmup, formal_s=args.formal),
        capture_dir,
        mode,
    )
    report_path = attempt / "report.yaml"
    save_yaml(report_path, report)
    print(f"台架结果 {report['result']}：{report_path}")
    print("正式产品结果 BLOCKED：最终装配后仍需执行正式第1步")
    return 0 if report["result"] == "PASS" else 1


def run_allan(args) -> int:
    attempt = new_attempt(args.output_root, "imu_allan_bench")
    capture_dir, mode = capture_or_reuse(args, attempt, write_timestamp_csv=False)
    report = common_report(
        analyze_allan(capture_dir, min_duration_s=args.minimum_duration),
        capture_dir,
        mode,
    )
    report["product_gate"] = {
        "minimum_duration_s": PRODUCT_ALLAN_MINIMUM_S,
        "duration_pass": bool(report["duration_s"] >= PRODUCT_ALLAN_MINIMUM_S),
        "note": "产品最低6小时、默认10小时；台架结果仍因未绑定最终硬件而不可签发",
    }
    plot_path = attempt / "allan.png"
    save_allan_plot(report, plot_path)
    report["evidence"]["allan_plot"] = str(plot_path.resolve())
    report["evidence"]["allan_plot_sha256"] = sha256_file(plot_path)
    report_path = attempt / "report.yaml"
    save_yaml(report_path, report)
    print(f"Allan台架结果 {report['result']}：{report_path}")
    print("正式产品结果 BLOCKED：最终STM32、D405身份和最终装配尚未绑定")
    return 0 if report["result"] == "PASS" else 1


def run_multipose(args) -> int:
    attempt = new_attempt(args.output_root, "imu_multipose_bench")
    recovery = None
    if args.resume_attempt:
        if args.input_csv:
            raise BenchError("--resume-attempt不能与--input-csv同时使用")
        port = find_imu_port(args.port)
        csv_path, poses, recovery = recover_multipose(
            source_attempt=args.resume_attempt.resolve(),
            attempt=attempt,
            port=port,
            baud=args.baud,
            protocol=args.protocol,
            pose_duration_s=args.pose_duration,
        )
        mode = "live_supplemental_recovery"
    elif args.input_csv:
        csv_path = args.input_csv.resolve()
        if not csv_path.is_file():
            raise BenchError(f"输入CSV不存在: {csv_path}")
        poses = []
        mode = "offline_reanalysis"
    else:
        port = find_imu_port(args.port)
        print(
            "将移动整个固定支架采集30个分散静止姿态：前20个拟合，后10个独立验证。\n"
            "不要求精确±X/±Y/±Z，但必须覆盖正放、倒放、各侧边和斜角，不能集中在同一半球。"
        )
        csv_path, poses = capture_pose_csv(
            port=port,
            baud=args.baud,
            protocol=args.protocol,
            pose_duration_s=args.pose_duration,
            attempt=attempt,
        )
        mode = "live_capture"
    fit, validation = load_pose_means(csv_path)
    report = fit_and_validate(fit, validation)
    report.update(
        {
            "acceptance_scope": "bench_provisional",
            "release_eligible": False,
            "product_result": "BLOCKED",
            "product_blocked_reason": "尚未绑定最终产品身份；参数可在身份绑定后复用",
            "pose_capture_health": poses,
            "reuse_policy": {
                "repeat_long_calibration": "only_if_invalidated",
                "invalidated_by": ["imu_replacement", "imu_remount", "range_change", "filter_change"],
                "not_invalidated_by": ["d405_arrival", "mount_tilt", "stm32_transport_only_change"],
            },
            "evidence": {
                "mode": mode,
                "pose_csv": str(csv_path),
                "pose_csv_sha256": sha256_file(csv_path),
            },
        }
    )
    if recovery is not None:
        report["recovery"] = recovery
        report["evidence"]["source_report"] = recovery["source_report"]
        report["evidence"]["source_report_sha256"] = recovery["source_report_sha256"]
    report_path = attempt / "report.yaml"
    save_yaml(report_path, report)
    print(f"多姿态台架结果 {report['result']}：{report_path}")
    print("最终产品状态 BLOCKED：绑定IMU身份并完成最终STM32短时A/B后才能签发")
    return 0 if report["result"] == "PASS" else 1


def run_orientation_preview(args) -> int:
    attempt = new_attempt(args.output_root, "imu_orientation_preview")
    source_attempt = args.source_attempt.resolve()
    source_report_path = source_attempt / "report.yaml"
    source_csv = source_attempt / "imu_multipose.csv"
    if not source_report_path.is_file() or not source_csv.is_file():
        raise BenchError("预览源目录必须同时包含report.yaml和imu_multipose.csv")
    source_report = yaml.safe_load(source_report_path.read_text(encoding="utf-8"))
    if sha256_file(source_csv) != source_report.get("evidence", {}).get("pose_csv_sha256"):
        raise BenchError("预览源姿态CSV的SHA-256与报告不一致")
    failed_checks = {
        name for name, passed in source_report.get("checks", {}).items() if not passed
    }
    if source_report.get("result") != "FAIL" or failed_checks != {"fit_octant_coverage"}:
        raise BenchError("方向预览只接受唯一失败项为姿态覆盖的源报告")
    with source_csv.open(newline="", encoding="utf-8") as stream:
        source_rows = list(csv.DictReader(stream))
    accepted, excluded = accepted_source_pose_ids(
        source_report.get("pose_capture_health", []), required_duration_s=30.0
    )
    fit_rows = [
        dict(row) for row in source_rows
        if row["split"] == "fit" and row["pose_id"] in accepted
    ]
    provisional = _fit_rows_report(fit_rows)
    seen = _corrected_octants(fit_rows, provisional)
    missing = sorted({a + b + c for a in "+-" for b in "+-" for c in "+-"} - seen)

    port = find_imu_port(args.port)
    capture_dir = attempt / "capture"
    stats = asdict(capture_serial(
        port=port,
        baud=args.baud,
        duration_s=2.0,
        output_dir=capture_dir,
        protocol=args.protocol,
        write_timestamp_csv=False,
        startup_discard_s=1.0,
    ))
    if stats["interrupted"]:
        report = {
            "format_version": 1,
            "result": "BLOCKED",
            "reason": "用户中断方向预览",
            "acceptance_scope": "orientation_preview_only",
            "release_eligible": False,
            "capture_health": stats,
        }
        save_yaml(attempt / "report.yaml", report)
        print(f"方向预览 BLOCKED：{attempt / 'report.yaml'}")
        return 2
    samples = load_capture(capture_dir / "imu.bin")
    stable, stability = pose_stable(samples)
    clean = transport_clean(stats, required_duration_s=2.0)
    mean = np.mean(np.asarray(samples["accel_g"], dtype=float), axis=0)
    bias = np.asarray(provisional["bias_m_s2"], dtype=float)
    correction = np.asarray(provisional["correction_matrix"], dtype=float)
    corrected = correction @ (mean * 9.80665 - bias) / 9.80665
    octant = "".join("-" if value < 0.0 else "+" for value in corrected)
    margin = float(np.min(np.abs(corrected)))
    target_ready = stable and clean and octant in missing and margin >= 0.15
    report = {
        "format_version": 1,
        "result": "PREVIEW",
        "acceptance_scope": "orientation_preview_only",
        "release_eligible": False,
        "source_attempt": str(source_attempt),
        "source_report_sha256": sha256_file(source_report_path),
        "excluded_source_pose_ids": excluded,
        "missing_fit_octants": missing,
        "raw_mean_accel_g": mean.tolist(),
        "corrected_accel_g": corrected.tolist(),
        "direction_octant": octant,
        "direction_margin_g": margin,
        "target_ready": target_ready,
        "stability": stability,
        "transport_clean": clean,
        "capture_health": stats,
        "capture_sha256": sha256_file(capture_dir / "imu.bin"),
    }
    report_path = attempt / "report.yaml"
    save_yaml(report_path, report)
    print(
        f"方向预览：ax={corrected[0]:+.3f}g ay={corrected[1]:+.3f}g "
        f"az={corrected[2]:+.3f}g => {octant}，余量={margin:.3f}g"
    )
    print(f"当前缺少：{','.join(missing)}；正式补采可用={target_ready}")
    print(f"预览证据：{report_path}")
    return 0


def _corrected_octants(rows: list[dict], fit_report: dict) -> set[str]:
    bias = np.asarray(fit_report["bias_m_s2"], dtype=float)
    correction = np.asarray(fit_report["correction_matrix"], dtype=float)
    octants = set()
    for row in rows:
        vector = np.asarray([float(row[key]) for key in ("ax", "ay", "az")])
        corrected = correction @ (vector - bias) / 9.80665
        octants.add("".join("-" if value < 0.0 else "+" for value in corrected))
    return octants


def _fit_rows_report(rows: list[dict]) -> dict:
    samples = np.asarray(
        [[float(row[key]) for key in ("ax", "ay", "az")] for row in rows],
        dtype=float,
    )
    return fit_and_validate(samples, np.empty((0, 3), dtype=float))


def recover_multipose(
    *,
    source_attempt: Path,
    attempt: Path,
    port: str,
    baud: int,
    protocol: str,
    pose_duration_s: float,
    max_candidates: int = MAX_SUPPLEMENTAL_CANDIDATES,
) -> tuple[Path, list[dict], dict]:
    source_report_path = source_attempt / "report.yaml"
    source_csv = source_attempt / "imu_multipose.csv"
    if not source_report_path.is_file() or not source_csv.is_file():
        raise BenchError("恢复目录必须同时包含report.yaml和imu_multipose.csv")
    source_report = yaml.safe_load(source_report_path.read_text(encoding="utf-8"))
    expected_csv_hash = source_report.get("evidence", {}).get("pose_csv_sha256")
    if not expected_csv_hash or sha256_file(source_csv) != expected_csv_hash:
        raise BenchError("源姿态CSV的SHA-256与报告不一致")
    failed_checks = {
        name for name, passed in source_report.get("checks", {}).items() if not passed
    }
    if source_report.get("result") != "FAIL" or failed_checks != {"fit_octant_coverage"}:
        raise BenchError(
            "源报告必须且只能因姿态覆盖失败；"
            f"result={source_report.get('result')}, failed_checks={sorted(failed_checks)}"
        )

    with source_csv.open(newline="", encoding="utf-8") as stream:
        source_rows = list(csv.DictReader(stream))
    accepted, excluded = accepted_source_pose_ids(
        source_report.get("pose_capture_health", []),
        required_duration_s=pose_duration_s,
    )
    validation_ids = {row["pose_id"] for row in source_rows if row["split"] == "validation"}
    invalid_validation = validation_ids - accepted
    if invalid_validation:
        raise BenchError(f"源独立验证姿态无效，不能作为拟合补采恢复: {sorted(invalid_validation)}")
    fit_rows = [
        dict(row) for row in source_rows
        if row["split"] == "fit" and row["pose_id"] in accepted
    ]
    validation_rows = [
        dict(row) for row in source_rows
        if row["split"] == "validation" and row["pose_id"] in accepted
    ]
    if len(fit_rows) < 9 or len(validation_rows) < 10:
        raise BenchError("源数据不足以执行仅补拟合姿态恢复")

    supplemental_reports = []
    accepted_count = 0
    trial = 0
    while True:
        provisional = _fit_rows_report(fit_rows)
        seen = _corrected_octants(fit_rows, provisional)
        if len(fit_rows) >= 20 and len(seen) >= 7:
            break
        if trial >= max_candidates:
            reason = (
                f"连续{max_candidates}个补采候选仍未达到7象限；"
                "已保留候选证据，请检查支架可摆方向后再继续"
            )
            save_yaml(
                attempt / "report.yaml",
                {
                    "format_version": 1,
                    "result": "FAIL",
                    "failed_gate": "supplemental_fit_octant_coverage",
                    "reason": reason,
                    "acceptance_scope": "bench_provisional",
                    "release_eligible": False,
                    "product_result": "BLOCKED",
                    "fit_pose_count": len(fit_rows),
                    "fit_octants": len(seen),
                    "candidate_limit": max_candidates,
                    "supplemental_pose_capture_health": supplemental_reports,
                    "source_report": str(source_report_path),
                    "source_report_sha256": sha256_file(source_report_path),
                    "source_pose_csv_sha256": sha256_file(source_csv),
                    "excluded_source_pose_ids": excluded,
                },
            )
            raise BenchFailure(reason)
        trial += 1
        missing = sorted(
            {a + b + c for a in "+-" for b in "+-" for c in "+-"} - seen
        )
        try:
            input(
                f"补采候选 {trial}：当前有效拟合姿态{len(fit_rows)}个、覆盖{len(seen)}/7象限，"
                f"缺少{','.join(missing)}。请摆到一个新的明显斜角，放稳后按回车……"
            )
        except KeyboardInterrupt as exc:
            reason = "用户在摆放提示阶段中断；未启动新的正式候选采集"
            save_yaml(
                attempt / "report.yaml",
                {
                    "format_version": 1,
                    "result": "BLOCKED",
                    "reason": reason,
                    "acceptance_scope": "bench_provisional",
                    "release_eligible": False,
                    "product_result": "BLOCKED",
                    "supplemental_pose_capture_health": supplemental_reports,
                    "source_report": str(source_report_path),
                    "source_report_sha256": sha256_file(source_report_path),
                },
            )
            raise BenchError(reason) from exc
        pose_dir = attempt / "supplemental" / f"candidate_{trial:02d}"
        stats = asdict(capture_serial(
            port=port,
            baud=baud,
            duration_s=pose_duration_s,
            output_dir=pose_dir,
            protocol=protocol,
            write_timestamp_csv=False,
            startup_discard_s=1.0,
        ))
        if stats["interrupted"]:
            reason = f"用户中断补采；证据已保留在{pose_dir}"
            save_yaml(
                attempt / "report.yaml",
                {
                    "format_version": 1,
                    "result": "BLOCKED",
                    "reason": reason,
                    "acceptance_scope": "bench_provisional",
                    "release_eligible": False,
                    "product_result": "BLOCKED",
                    "capture_health": stats,
                    "source_report": str(source_report_path),
                    "source_report_sha256": sha256_file(source_report_path),
                },
            )
            raise BenchError(reason)
        samples = load_capture(pose_dir / "imu.bin")
        stable, metrics = pose_stable(samples)
        clean = transport_clean(stats, required_duration_s=pose_duration_s)
        mean = np.mean(np.asarray(samples["accel_g"], dtype=float), axis=0) * 9.80665
        bias = np.asarray(provisional["bias_m_s2"], dtype=float)
        correction = np.asarray(provisional["correction_matrix"], dtype=float)
        corrected = correction @ (mean - bias) / 9.80665
        octant = "".join("-" if value < 0.0 else "+" for value in corrected)
        margin = float(np.min(np.abs(corrected)))
        direction_ok = octant not in seen and margin >= 0.15
        accepted_now = stable and clean and direction_ok
        supplemental_reports.append(
            {
                "candidate": trial,
                **metrics,
                "transport_clean": clean,
                "direction_octant": octant,
                "direction_margin_g": margin,
                "direction_new_and_clear": direction_ok,
                "accepted": accepted_now,
                "capture_health": stats,
                "capture_sha256": sha256_file(pose_dir / "imu.bin"),
            }
        )
        if not accepted_now:
            print(
                f"本候选不计入拟合：静止={stable}，传输={clean}，方向={octant}，"
                f"离轴面余量={margin:.3f}g；请换一个缺失象限的明显斜角。"
            )
            continue
        accepted_count += 1
        fit_rows.append(
            {
                "pose_id": f"S{accepted_count:02d}",
                "split": "fit",
                "ax": mean[0],
                "ay": mean[1],
                "az": mean[2],
            }
        )
        print(f"补采姿态S{accepted_count:02d}已接受：方向={octant}，余量={margin:.3f}g。")

    combined_csv = attempt / "imu_multipose.csv"
    with combined_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["pose_id", "split", "ax", "ay", "az"])
        writer.writeheader()
        writer.writerows(fit_rows + validation_rows)
    recovery = {
        "source_attempt": str(source_attempt),
        "source_report": str(source_report_path),
        "source_report_sha256": sha256_file(source_report_path),
        "source_pose_csv_sha256": sha256_file(source_csv),
        "excluded_source_pose_ids": excluded,
        "source_fit_poses_reused": len(fit_rows) - accepted_count,
        "source_validation_poses_reused": len(validation_rows),
        "supplemental_fit_poses_accepted": accepted_count,
    }
    return combined_csv, supplemental_reports, recovery


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="仅IMU台架采集；结果不进入正式产品签发")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(name: str) -> argparse.ArgumentParser:
        item = subparsers.add_parser(name)
        item.add_argument("--port")
        item.add_argument("--baud", type=int, default=921600)
        item.add_argument(
            "--protocol",
            choices=("auto", "kt_ex9_37", "stm32_combined_v1"),
            default="auto",
        )
        item.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
        item.add_argument("--input-capture", type=Path)
        return item

    static = common("static")
    static.add_argument("--duration", type=float, default=600.0)
    static.add_argument("--warmup", type=float, default=120.0)
    static.add_argument("--formal", type=float, default=480.0)

    allan = common("allan")
    allan.add_argument("--duration", type=float, default=10 * 3600.0)
    allan.add_argument("--minimum-duration", type=float, default=6 * 3600.0)

    multipose = subparsers.add_parser("multipose")
    multipose.add_argument("--port")
    multipose.add_argument("--baud", type=int, default=921600)
    multipose.add_argument(
        "--protocol",
        choices=("auto", "kt_ex9_37", "stm32_combined_v1"),
        default="auto",
    )
    multipose.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    multipose.add_argument("--pose-duration", type=at_least_30_seconds, default=30.0)
    multipose.add_argument("--input-csv", type=Path)
    multipose.add_argument("--resume-attempt", type=Path)

    preview = subparsers.add_parser("orientation-preview")
    preview.add_argument("--source-attempt", type=Path, required=True)
    preview.add_argument("--port")
    preview.add_argument("--baud", type=int, default=921600)
    preview.add_argument(
        "--protocol",
        choices=("auto", "kt_ex9_37", "stm32_combined_v1"),
        default="auto",
    )
    preview.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runners = {
            "static": run_static,
            "allan": run_allan,
            "multipose": run_multipose,
            "orientation-preview": run_orientation_preview,
        }
        return runners[args.command](args)
    except (BenchFailure, MultiposeCaptureFailure) as exc:
        print(f"FAIL：{exc}", file=sys.stderr)
        return 1
    except (BenchError, OSError, ValueError) as exc:
        print(f"BLOCKED：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
