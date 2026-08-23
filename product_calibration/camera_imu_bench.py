#!/usr/bin/env python3
"""Product-bound camera-IMU candidate over the current USB-TTL timing chain."""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path

import yaml

from .camera_bench import load_bound_identity, verify_bound_device
from .compare_camera_imu import compare_runs
from .kalibr_pipeline import (
    DEFAULT_CAPTURE_RUNTIME,
    DEFAULT_RSUSB_RUNTIME,
    camera_capture_health,
    collect_known_good,
    convert_to_bag,
    detect_d405,
    imucam_residuals,
    imu_capture_health,
    require_capture_stack,
    require_executable,
    solve_camera_imu,
)
from .workflow import WorkflowError, sha256_file


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml"
DEFAULT_SESSION_ROOT = ROOT / "calibration_sessions"
DEFAULT_STEREO_ROOT = ROOT / "camera_bench_results"
DEFAULT_IMU_ROOT = ROOT / "imu_bench_results"
DEFAULT_OUTPUT_ROOT = ROOT / "camera_imu_bench_results"
PRODUCT_ALLAN_MINIMUM_S = 6 * 3600.0
ALLOWED_PROVISIONAL_ALLAN_FAILURES = {
    "counter_gaps_zero",
    "crc_errors_zero",
    "discarded_bytes_zero",
}
REQUIRED_ALLAN_CHECKS = {
    "capture_summary_present",
    "duration",
    "rate_400hz",
    "sequence_gaps_zero",
    "invalid_imu_flags_zero",
    "queue_overflow_zero",
    "capture_not_interrupted",
    "stationary_gyro_rms",
    "stationary_accel_rms",
}


def positive_seconds(value: str) -> float:
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0.0:
        raise argparse.ArgumentTypeError("采集阶段时长必须是正有限数")
    return duration


def save_yaml(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_report(path: Path) -> dict:
    path = Path(path).resolve()
    if not path.is_file():
        raise WorkflowError(f"报告不存在: {path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise WorkflowError(f"报告格式错误: {path}")
    return document


def latest_report(root: Path, suffix: str, predicate) -> Path:
    candidates = sorted(Path(root).resolve().glob(f"*_{suffix}/report.yaml"), reverse=True)
    for path in candidates:
        try:
            if predicate(load_report(path)):
                return path
        except (OSError, ValueError, yaml.YAMLError):
            continue
    raise WorkflowError(f"没有找到可用的{suffix}报告: {Path(root).resolve()}")


def verify_evidence(report: dict, path_key: str, hash_key: str) -> Path:
    evidence = report.get("evidence", {})
    path = Path(str(evidence.get(path_key, ""))).resolve()
    expected = evidence.get(hash_key)
    if not path.is_file() or not expected:
        raise WorkflowError(f"报告缺少可验证证据: {path_key}")
    if sha256_file(path) != expected:
        raise WorkflowError(f"报告证据SHA-256不一致: {path}")
    return path


def allan_is_provisionally_usable(report: dict) -> bool:
    checks = report.get("checks", {})
    try:
        duration = float(report["duration_s"])
        rate = float(report["rate_hz"])
    except (KeyError, TypeError, ValueError):
        return False
    if not math.isfinite(duration) or duration < PRODUCT_ALLAN_MINIMUM_S:
        return False
    if not math.isfinite(rate) or not 380.0 <= rate <= 420.0:
        return False
    failed = {name for name, passed in checks.items() if passed is not True}
    if not REQUIRED_ALLAN_CHECKS.issubset(
        {name for name, passed in checks.items() if passed is True}
    ):
        return False
    if report.get("result") == "PASS":
        return not failed
    return report.get("result") == "FAIL" and bool(failed) and failed <= ALLOWED_PROVISIONAL_ALLAN_FAILURES


def write_provisional_imu_yaml(report: dict, output: Path) -> dict:
    if not allan_is_provisionally_usable(report):
        failed = sorted(
            name for name, passed in report.get("checks", {}).items() if passed is not True
        )
        raise WorkflowError(
            "Allan报告只能因允许的传输门失败且时长/频率/静止性必须通过；"
            f"当前result={report.get('result')} failed_checks={failed}"
        )
    try:
        values = {
            "gyroscope_noise_density": float(report["gyroscope"]["noise_density"]),
            "gyroscope_random_walk": float(report["gyroscope"]["random_walk"]),
            "accelerometer_noise_density": float(report["accelerometer"]["noise_density"]),
            "accelerometer_random_walk": float(report["accelerometer"]["random_walk"]),
        }
        rate = float(report["rate_hz"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError("Allan报告缺少Kalibr噪声参数") from exc
    if not math.isfinite(rate) or rate <= 0.0 or any(
        not math.isfinite(value) or value <= 0.0 for value in values.values()
    ):
        raise WorkflowError("Allan报告包含非正或非有限Kalibr噪声参数")
    document = {
        "rostopic": "/imu0",
        "update_rate": rate,
        **values,
        "model": "calibrated",
        "timeframe": "ros",
        "use_accel": True,
    }
    save_yaml(output, document)
    return {
        "allan_result": report.get("result"),
        "accepted_failed_checks": sorted(
            name for name, passed in report.get("checks", {}).items() if passed is not True
        ),
        "imu_yaml": str(Path(output).resolve()),
        "imu_yaml_sha256": sha256_file(output),
    }


def _residual_checks(residuals: dict) -> dict[str, bool]:
    run1, run2 = residuals["run1"], residuals["run2"]
    return {
        "run1_reprojection_mean_le_0_5px": run1["reprojection_mean_px"] <= 0.5,
        "run2_reprojection_mean_le_0_5px": run2["reprojection_mean_px"] <= 0.5,
        "run1_gyro_mean_le_0_02rad_s": run1["gyroscope_mean_rad_s"] <= 0.02,
        "run2_gyro_mean_le_0_02rad_s": run2["gyroscope_mean_rad_s"] <= 0.02,
        "run1_accel_mean_le_0_25m_s2": run1["accelerometer_mean_m_s2"] <= 0.25,
        "run2_accel_mean_le_0_25m_s2": run2["accelerometer_mean_m_s2"] <= 0.25,
    }


def finalize_candidate_report(
    comparison: dict,
    residuals: dict,
    camera_health: dict,
    imu_health: dict,
    *,
    product_id: str,
    device: dict,
    source_evidence: dict,
    run_evidence: dict,
    transport: str = "ttl",
) -> dict:
    residual_checks = _residual_checks(residuals)
    health_pass = all(
        item.get("result") == "PASS" for item in camera_health.values()
    )
    not_aborted = all(
        item.get("metrics", {}).get("guided_nine_grid", {}).get("user_aborted") is False
        for item in camera_health.values()
    )
    imu_health_pass = all(
        item.get("result") == "PASS" for item in imu_health.values()
    )
    expected_protocol = (
        "stm32_combined_v1" if transport == "stm32" else "kt_ex9_37"
    )
    protocol_pass = all(
        item.get("metrics", {}).get("protocol") == expected_protocol
        for item in imu_health.values()
    )
    checks = {
        "two_run_repeatability_pass": comparison.get("result") == "PASS",
        **residual_checks,
        "both_camera_capture_health_pass": health_pass,
        "both_imu_transport_health_pass": imu_health_pass,
        "both_guided_captures_not_aborted": not_aborted,
    }
    if transport == "stm32":
        checks["both_expected_imu_protocol"] = protocol_pass
    report = dict(comparison)
    report["repeatability_checks"] = comparison.get("checks", {})
    report["checks"] = checks
    report["result"] = "PASS" if all(checks.values()) else "FAIL"
    report.update(
        {
            "mode": "live_capture",
            "acceptance_scope": f"product_bound_camera_imu_{transport}_candidate",
            "release_eligible": False,
            "product_result": "BLOCKED",
            "product_blocked_reason": (
                "正式必需前置阶段尚未全部导入产品session；本候选通过后才可导入第5步"
                if transport == "stm32"
                else "当前时间偏移属于USB转TTL时间链；最终STM32到货后必须完成时间戳A/B，"
                     "且正式必需前置阶段PASS后才能导入第5步"
            ),
            "product_id": product_id,
            "bound_device": device,
            "residuals": residuals,
            "residual_checks": residual_checks,
            "camera_capture_health": camera_health,
            "imu_capture_health": imu_health,
            "spatial_calibration": {
                "status": "CANDIDATE_PASS" if all(checks.values()) else "CANDIDATE_FAIL",
                "reuse_if_mount_unchanged": True,
                "invalidated_by": ["mount_change", "camera_replacement", "imu_replacement"],
            },
            "temporal_calibration": {
                "td_status": (
                    "CANDIDATE_STM32_TIME_CHAIN"
                    if transport == "stm32" else "PROVISIONAL_USB_TTL_ONLY"
                ),
                "reuse_after_stm32": transport == "stm32",
                "invalidated_by": (
                    ["firmware_change", "timestamp_chain_change", "usb_bridge_change"]
                    if transport == "stm32"
                    else ["stm32_arrival", "timestamp_chain_change", "usb_bridge_change"]
                ),
            },
            "source_evidence": source_evidence,
            "evidence": run_evidence,
        }
    )
    return report


def new_attempt(output_root: Path, transport: str) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    attempt = Path(output_root).resolve() / f"{timestamp}_camera_imu_{transport}_candidate"
    attempt.mkdir(parents=True)
    return attempt


def _run_one(
    *,
    index: int,
    attempt: Path,
    port: str,
    baud: int,
    phase_seconds: float,
    capture_runtime: Path,
    rsusb_runtime: Path,
    preview: bool,
    stereo_camchain: Path,
    imu_yaml: Path,
) -> dict:
    run_dir = attempt / f"run_{index}"
    run_dir.mkdir()
    input(
        f"第{index}/2份独立联合采集：把AprilGrid固定不动，移动整个D405＋IMU刚体；"
        "准备覆盖XYZ平移和roll/pitch/yaw后按回车……"
    )
    recording = collect_known_good(
        attempt=run_dir,
        mode="imucam",
        port=port,
        baud=baud,
        phase_seconds=phase_seconds,
        capture_root=capture_runtime,
        rsusb_root=rsusb_runtime,
        preview=preview,
    )
    health = camera_capture_health(recording)
    imu_health = imu_capture_health(recording)
    bag = run_dir / "imucam.bag"
    convert_to_bag(recording, bag, capture_runtime)
    camchain, results = solve_camera_imu(
        bag,
        stereo_camchain,
        imu_yaml,
        Path(capture_runtime) / "config/aprilgrid_6x6_35mm.yaml",
        run_dir / "solve",
    )
    return {
        "camchain": camchain,
        "results": results,
        "residuals": imucam_residuals(results),
        "camera_health": health,
        "imu_health": imu_health,
        "accelerometer_application": {
            "policy": "NOT_APPLIED_PRODUCT_BASELINE",
            "input": "raw_imu",
        },
        "recording": recording,
    }


def _resolve_sources(args, device: dict) -> tuple[Path, dict, Path, dict]:
    stereo_path = args.stereo_report or latest_report(
        args.stereo_root,
        "d405_stereo_candidate",
        lambda report: report.get("result") == "PASS"
        and report.get("product_id") == args.product_id,
    )
    stereo = load_report(stereo_path)
    if stereo.get("result") != "PASS" or stereo.get("product_id") != args.product_id:
        raise WorkflowError("双目候选报告必须PASS且绑定当前产品编号")
    if stereo.get("bound_device", {}).get("serial") != device.get("serial"):
        raise WorkflowError("双目候选报告绑定的D405与当前设备不一致")
    verify_evidence(stereo, "camchain", "camchain_sha256")

    allan_path = args.allan_report or latest_report(
        args.imu_root,
        "imu_allan_bench",
        allan_is_provisionally_usable,
    )
    allan = load_report(allan_path)
    if not allan_is_provisionally_usable(allan):
        raise WorkflowError("没有可用于TTL候选求解的Allan数值报告")
    capture_dir = Path(str(allan.get("evidence", {}).get("capture_dir", ""))).resolve()
    imu_bin = capture_dir / "imu.bin"
    if not imu_bin.is_file() or sha256_file(imu_bin) != allan.get("evidence", {}).get("imu_bin_sha256"):
        raise WorkflowError("Allan原始IMU证据缺失或SHA-256不一致")
    return (
        Path(stereo_path).resolve(), stereo,
        Path(allan_path).resolve(), allan,
    )


def run_candidate(args: argparse.Namespace) -> int:
    identity, identity_path = load_bound_identity(args.product_id, args.session_root)
    require_executable("kalibr_calibrate_imu_camera")
    require_capture_stack(args.capture_runtime, args.rsusb_runtime, imu=True)
    device = detect_d405(args.capture_runtime, args.rsusb_runtime)
    port = verify_bound_device(identity, device, args.port)
    (
        stereo_path, stereo,
        allan_path, allan,
    ) = _resolve_sources(args, device)

    attempt = new_attempt(args.output_root, args.transport)
    imu_yaml = attempt / "imu_kalibr_provisional_ttl.yaml"
    imu_prior = write_provisional_imu_yaml(allan, imu_yaml)
    stereo_camchain = verify_evidence(stereo, "camchain", "camchain_sha256")

    runs = [
        _run_one(
            index=index,
            attempt=attempt,
            port=port,
            baud=args.baud,
            phase_seconds=args.phase_seconds,
            capture_runtime=args.capture_runtime,
            rsusb_runtime=args.rsusb_runtime,
            preview=not args.no_preview,
            stereo_camchain=stereo_camchain,
            imu_yaml=imu_yaml,
        )
        for index in (1, 2)
    ]
    comparison = compare_runs(runs[0]["camchain"], runs[1]["camchain"], BASELINE)
    residuals = {f"run{index}": run["residuals"] for index, run in enumerate(runs, 1)}
    health = {f"run{index}": run["camera_health"] for index, run in enumerate(runs, 1)}
    imu_health = {f"run{index}": run["imu_health"] for index, run in enumerate(runs, 1)}
    source_evidence = {
        "identity_report": str(identity_path.resolve()),
        "identity_report_sha256": sha256_file(identity_path),
        "stereo_report": str(stereo_path),
        "stereo_report_sha256": sha256_file(stereo_path),
        "accelerometer_intrinsic_policy": "NOT_REQUIRED_NOT_APPLIED",
        "allan_report": str(allan_path),
        "allan_report_sha256": sha256_file(allan_path),
        "allan_prior_policy": imu_prior,
    }
    run_evidence = {}
    for index, run in enumerate(runs, 1):
        run_evidence.update(
            {
                f"run{index}_camchain": str(run["camchain"].resolve()),
                f"run{index}_camchain_sha256": sha256_file(run["camchain"]),
                f"run{index}_results": str(run["results"].resolve()),
                f"run{index}_results_sha256": sha256_file(run["results"]),
                f"run{index}_recording": str(run["recording"].resolve()),
                f"run{index}_accelerometer_application": run["accelerometer_application"],
            }
        )
    report = finalize_candidate_report(
        comparison,
        residuals,
        health,
        imu_health,
        product_id=args.product_id,
        device=device,
        source_evidence=source_evidence,
        run_evidence=run_evidence,
        transport=args.transport,
    )
    report_path = attempt / "report.yaml"
    save_yaml(report_path, report)
    print(f"相机-IMU {args.transport}候选结果 {report['result']}：{report_path}")
    if args.transport == "stm32":
        print("正式产品结果 BLOCKED：候选需导入完整产品session后才能签发")
    else:
        print("正式产品结果 BLOCKED：STM32到货后必须复核时间链；本次td不得直接签发")
    return 0 if report["result"] == "PASS" else 1


def parser() -> argparse.ArgumentParser:
    item = argparse.ArgumentParser(description="当前USB转TTL链的相机-IMU两次联合标定候选")
    item.add_argument("product_id")
    item.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    item.add_argument("--stereo-root", type=Path, default=DEFAULT_STEREO_ROOT)
    item.add_argument("--imu-root", type=Path, default=DEFAULT_IMU_ROOT)
    item.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    item.add_argument("--stereo-report", type=Path)
    item.add_argument("--allan-report", type=Path)
    item.add_argument("--port")
    item.add_argument("--baud", type=int, default=921600)
    item.add_argument("--transport", choices=("ttl", "stm32"), default="ttl")
    item.add_argument("--capture-runtime", type=Path, default=DEFAULT_CAPTURE_RUNTIME)
    item.add_argument("--rsusb-runtime", type=Path, default=DEFAULT_RSUSB_RUNTIME)
    item.add_argument("--phase-seconds", type=positive_seconds, default=20.0)
    item.add_argument("--no-preview", action="store_true")
    return item


def main() -> int:
    args = parser().parse_args()
    try:
        return run_candidate(args)
    except KeyboardInterrupt:
        print("BLOCKED：用户中断相机-IMU TTL候选采集", file=sys.stderr)
        return 2
    except (WorkflowError, OSError, ValueError) as exc:
        print(f"BLOCKED：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
