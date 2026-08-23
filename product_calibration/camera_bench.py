#!/usr/bin/env python3
"""Product-bound D405 stereo candidate capture outside the formal stage order."""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

from .kalibr_pipeline import (
    DEFAULT_CAPTURE_RUNTIME,
    DEFAULT_RSUSB_RUNTIME,
    camera_capture_health,
    collect_known_good,
    convert_to_bag,
    d405_factory_stereo_baseline,
    detect_d405,
    heldout_epipolar_report,
    require_capture_stack,
    require_executable,
    solve_stereo,
    stereo_report,
)
from .workflow import CalibrationSession, WorkflowError, load_workflow, sha256_file


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "product_calibration/workflow.yaml"
BASELINE = ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml"
DEFAULT_SESSION_ROOT = ROOT / "calibration_sessions"
DEFAULT_OUTPUT_ROOT = ROOT / "camera_bench_results"


def positive_seconds(value: str) -> float:
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0.0:
        raise argparse.ArgumentTypeError("采集阶段时长必须是正有限数")
    return duration


def save_yaml(path: Path, document: dict) -> None:
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def new_attempt(output_root: Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    attempt = Path(output_root).resolve() / f"{timestamp}_d405_stereo_candidate"
    attempt.mkdir(parents=True)
    return attempt


def load_bound_identity(product_id: str, session_root: Path) -> tuple[dict, Path]:
    session = CalibrationSession.open(
        load_workflow(WORKFLOW), Path(session_root).resolve() / product_id
    )
    status = session.status()
    if status["bound_input_integrity"]["active_workflow"]["state"] != "PASS":
        raise WorkflowError("产品档案绑定的workflow与当前工具不一致")
    if status["stages"]["identity"]["state"] != "PASS":
        raise WorkflowError("产品身份阶段没有可用PASS报告")
    identity_path = session.root / session.workflow.stages["identity"].evidence
    identity = yaml.safe_load(identity_path.read_text(encoding="utf-8")) or {}
    return identity, identity_path


def verify_bound_device(identity: dict, device: dict, requested_port: str | None) -> str:
    expected = identity.get("devices", {}).get("d405", {})
    if device.get("serial") != expected.get("serial"):
        raise WorkflowError(
            f"D405序列号与产品档案不一致: live={device.get('serial')} "
            f"bound={expected.get('serial')}"
        )
    if device.get("firmware") != expected.get("firmware"):
        raise WorkflowError(
            f"D405固件与产品档案不一致: live={device.get('firmware')} "
            f"bound={expected.get('firmware')}"
        )
    bound_port = str(identity.get("devices", {}).get("imu_port", ""))
    port = requested_port or bound_port
    if port != bound_port:
        raise WorkflowError("相机候选采集必须使用产品档案绑定的IMU串口")
    path = Path(port)
    if not path.exists() or not os.access(path, os.R_OK | os.W_OK):
        raise WorkflowError(f"产品档案绑定的IMU串口不可读写: {path}")
    return port


def finalize_stereo_report(
    base: dict,
    heldout: dict,
    training_health: dict,
    validation_health: dict,
    *,
    product_id: str,
    identity_report: Path,
    device: dict,
) -> dict:
    report = dict(base)
    report["heldout_epipolar"] = heldout
    report["camera_capture_health"] = {
        "training": training_health,
        "heldout_validation": validation_health,
    }
    report["result"] = (
        "PASS" if all(item.get("result") == "PASS" for item in (
            base, heldout, training_health, validation_health
        ))
        else "FAIL"
    )
    report.update(
        {
            "mode": "live_capture",
            "acceptance_scope": "product_bound_stereo_candidate",
            "release_eligible": False,
            "product_result": "BLOCKED",
            "product_blocked_reason": (
                "正式身份阶段尚未PASS；候选可在前置完成且设备/profile未变化时离线导入第4步"
            ),
            "product_id": product_id,
            "bound_device": device,
            "reuse_policy": {
                "formal_import_after_prerequisites": True,
                "invalidated_by": [
                    "d405_replacement",
                    "d405_internal_change",
                    "firmware_change",
                    "dual_ir_profile_change",
                ],
                "not_invalidated_by": ["stm32_arrival", "imu_transport_change"],
            },
        }
    )
    report.setdefault("evidence", {}).update(
        {
            "identity_report": str(Path(identity_report).resolve()),
            "identity_report_sha256": sha256_file(identity_report),
        }
    )
    return report


def run_stereo(args: argparse.Namespace) -> int:
    identity, identity_path = load_bound_identity(args.product_id, args.session_root)
    require_executable("kalibr_calibrate_cameras")
    require_capture_stack(args.capture_runtime, args.rsusb_runtime, imu=True)
    device = detect_d405(args.capture_runtime, args.rsusb_runtime)
    port = verify_bound_device(identity, device, args.port)
    factory_reference = d405_factory_stereo_baseline(
        args.capture_runtime, args.rsusb_runtime, device["serial"]
    )

    attempt = new_attempt(args.output_root)
    training_root = attempt / "training"
    training_root.mkdir()
    input(
        "求解集：固定整机，只移动AprilGrid。当前尚未打开相机；按回车后约3秒会弹出"
        "‘ego_vio calibration camera’实时预览，再按画面提示覆盖近中远、四角和多个倾角……"
    )
    recording = collect_known_good(
        attempt=training_root,
        mode="camera",
        port=port,
        baud=args.baud,
        phase_seconds=args.phase_seconds,
        capture_root=args.capture_runtime,
        rsusb_root=args.rsusb_runtime,
        preview=not args.no_preview,
    )

    validation_root = attempt / "heldout_validation"
    validation_root.mkdir()
    input(
        "独立留出集：保持整机不动。按回车后会再次弹出实时预览；重新移动AprilGrid"
        "；程序将从左上到右下逐格高亮并显示尚缺格，九格全部通过才结束，"
        "该批图不参与求解……"
    )
    validation_recording = collect_known_good(
        attempt=validation_root,
        mode="camera_validation",
        port=port,
        baud=args.baud,
        phase_seconds=args.validation_phase_seconds,
        capture_root=args.capture_runtime,
        rsusb_root=args.rsusb_runtime,
        preview=not args.no_preview,
    )

    bag = attempt / "stereo.bag"
    convert_to_bag(recording, bag, args.capture_runtime)
    camchain, results = solve_stereo(
        bag,
        Path(args.capture_runtime) / "config/aprilgrid_6x6_35mm.yaml",
        attempt / "solve",
    )
    base = stereo_report(camchain, results, BASELINE, factory_reference)
    heldout = heldout_epipolar_report(validation_recording, camchain)
    training_health = camera_capture_health(recording)
    validation_health = camera_capture_health(validation_recording)
    report = finalize_stereo_report(
        base,
        heldout,
        training_health,
        validation_health,
        product_id=args.product_id,
        identity_report=identity_path,
        device=device,
    )
    report_path = attempt / "report.yaml"
    save_yaml(report_path, report)
    print(f"D405双目候选结果 {report['result']}：{report_path}")
    print("正式产品结果 BLOCKED：完成产品身份绑定后使用本候选证据导入正式第4步")
    return 0 if report["result"] == "PASS" else 1


def parser() -> argparse.ArgumentParser:
    item = argparse.ArgumentParser(description="产品身份绑定的D405双目候选采集与求解")
    item.add_argument("product_id")
    item.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    item.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    item.add_argument("--port")
    item.add_argument("--baud", type=int, default=921600)
    item.add_argument("--capture-runtime", type=Path, default=DEFAULT_CAPTURE_RUNTIME)
    item.add_argument("--rsusb-runtime", type=Path, default=DEFAULT_RSUSB_RUNTIME)
    item.add_argument("--phase-seconds", type=positive_seconds, default=20.0)
    item.add_argument("--validation-phase-seconds", type=positive_seconds, default=8.0)
    item.add_argument("--no-preview", action="store_true")
    return item


def main() -> int:
    args = parser().parse_args()
    try:
        return run_stereo(args)
    except KeyboardInterrupt:
        print("BLOCKED：用户中断D405双目候选采集", file=sys.stderr)
        return 2
    except (WorkflowError, OSError, ValueError) as exc:
        print(f"BLOCKED：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
