#!/usr/bin/env python3
"""Separate customer capture-and-solve commands for product calibration stages 0-6."""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from product_calibration.imu_analysis import analyze_allan, analyze_static
from product_calibration.imu_ellipsoid import fit_and_validate, load_pose_means
from product_calibration.imu_multipose_capture import capture_pose_csv
from product_calibration.imu_stream import capture_serial
from product_calibration.compare_camera_imu import compare_runs
from product_calibration.runtime_candidate import build_stage6_runtime
from product_calibration.kalibr_pipeline import (
    DEFAULT_CAPTURE_RUNTIME,
    DEFAULT_RSUSB_RUNTIME,
    camera_capture_health,
    collect_known_good,
    convert_to_bag,
    detect_d405,
    factory_calibration_to_camchain,
    factory_stereo_report,
    imucam_residuals,
    heldout_epipolar_report,
    imu_capture_health,
    read_d405_factory_calibration,
    require_capture_stack,
    require_executable,
    solve_camera_imu,
    solve_stereo,
    stereo_report,
)
from product_calibration.workflow import CalibrationSession, WorkflowError, load_workflow, sha256_file


ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "product_calibration/workflow.yaml"
BASELINE = ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml"
DEFAULT_SESSION_ROOT = Path(
    os.environ.get("EGO_VIO_CALIBRATION_SESSIONS", ROOT / "calibration_sessions")
)


def finish_stage(result: str, report_path: Path, product_id: str,
                 next_script: str | None) -> int:
    print(f"{result}：{report_path}")
    if result == "PASS" and next_script:
        print(f"下一步：./{next_script} {product_id}")
    elif result == "PASS":
        print("五个必需标定阶段已完成；下一步执行端到端SLAM与长稳签发验收。")
    elif result == "FAIL":
        print("本阶段未通过；原始数据已保留，请按报告原因只重做本阶段。")
    else:
        print("本阶段尚未具备签发条件；请按报告中的blocking_reason处理。")
    return 0 if result == "PASS" else 2 if result == "BLOCKED" else 1


def finish_optional_diagnostic(result: str, report_path: Path,
                               product_id: str) -> int:
    print(f"{result}：{report_path}")
    print("第3步仅为研发诊断，结果不会应用到产品运行时，也不阻塞客户签发链。")
    print(f"下一必需步骤：./calibrate_04_d405_factory.sh {product_id}")
    return 0 if result == "PASS" else 2 if result == "BLOCKED" else 1


def dump_yaml(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")


def session_path(root: Path, product_id: str) -> Path:
    return Path(root).resolve() / product_id


def find_imu_port(requested: str | None) -> str:
    if requested:
        path = Path(requested)
        if not path.exists():
            raise WorkflowError(f"IMU串口不存在: {path}")
        return str(path)
    candidates = sorted(glob.glob("/dev/serial/by-id/*"))
    if len(candidates) != 1:
        raise WorkflowError(f"无法唯一识别IMU串口，找到{len(candidates)}个，请使用--port")
    return candidates[0]


def init_product(args) -> int:
    workflow = load_workflow(WORKFLOW)
    root = session_path(args.session_root, args.product_id)
    imu_port = find_imu_port(args.port)
    if not imu_port.startswith("/dev/serial/by-id/"):
        raise WorkflowError("产品档案必须使用稳定的/dev/serial/by-id/端口，不能绑定ttyUSB序号")
    d405 = detect_d405(args.capture_runtime, args.rsusb_runtime)
    if bool(args.firmware_bin) != bool(args.flash_evidence):
        raise WorkflowError("--firmware-bin与--flash-evidence必须同时提供")
    stm32 = None
    if args.firmware_bin:
        firmware_bin = args.firmware_bin.resolve()
        flash_evidence = args.flash_evidence.resolve()
        if not firmware_bin.is_file():
            raise WorkflowError(f"STM32固件不存在: {firmware_bin}")
        if not flash_evidence.is_file():
            raise WorkflowError(f"STM32烧录证据不存在: {flash_evidence}")
        stm32 = {
            "transport_protocol": "stm32_combined_v1",
            "firmware_bin": str(firmware_bin),
            "firmware_sha256": sha256_file(firmware_bin),
            "flash_evidence": str(flash_evidence),
            "flash_evidence_sha256": sha256_file(flash_evidence),
        }
    session = CalibrationSession.create(workflow, root, args.product_id, BASELINE)
    devices = {"d405": d405, "imu_port": imu_port}
    checks = {
        "one_d405": True,
        "imu_port_by_id_exists": imu_port.startswith("/dev/serial/by-id/"),
    }
    if stm32:
        devices["stm32"] = stm32
        checks["stm32_firmware_evidence_complete"] = True
    report = {
        "format_version": 1,
        "result": "PASS",
        "mode": "live_device_identity",
        "product_id": args.product_id,
        "host": socket.gethostname(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "devices": devices,
        "checks": checks,
    }
    report_path = root / "identity/attempt_001/report.yaml"
    dump_yaml(report_path, report)
    session.record_result("identity", "PASS", report_path)
    print(f"PASS：产品档案已创建 {root}")
    print(f"下一步：./calibrate_01_imu_static.sh {args.product_id}")
    return 0


def open_session(args) -> CalibrationSession:
    return CalibrationSession.open(load_workflow(WORKFLOW), session_path(args.session_root, args.product_id))


def next_attempt(session: CalibrationSession, stage: str) -> Path:
    stage_root = session.root / stage / "attempts"
    stage_root.mkdir(parents=True, exist_ok=True)
    existing = sorted(path for path in stage_root.glob("attempt_*"))
    number = max([int(path.name.split("_")[-1]) for path in existing] or [0]) + 1
    path = stage_root / f"attempt_{number:03d}"
    path.mkdir()
    return path


def require_stage_ready(session: CalibrationSession, stage: str) -> None:
    state = session.status()["stages"][stage]["state"]
    if state not in {"READY", "PASS", "FAIL"}:
        raise WorkflowError(f"{stage}当前状态为{state}，前置步骤未PASS")


def attach_provenance(report: dict, capture_dir: Path, mode: str) -> None:
    report["mode"] = mode
    report["evidence"] = {
        "capture_dir": str(capture_dir.resolve()),
        "imu_bin_sha256": sha256_file(capture_dir / "imu.bin"),
        "raw_packets_sha256": (
            sha256_file(capture_dir / "raw_packets.bin")
            if (capture_dir / "raw_packets.bin").is_file()
            else None
        ),
    }


def run_static(args) -> int:
    session = open_session(args)
    stage = "imu_static_bias"
    require_stage_ready(session, stage)
    attempt = next_attempt(session, stage)
    if args.input_capture:
        capture_dir = args.input_capture.resolve()
        mode = "offline_reanalysis"
    else:
        capture_dir = attempt / "capture"
        args.port = find_imu_port(args.port)
        input("将整机按工作姿态放稳，确认线缆不受力后按回车开始10分钟采集……")
        capture_serial(
            port=find_imu_port(args.port), baud=args.baud, duration_s=args.duration,
            output_dir=capture_dir, protocol=args.protocol, startup_discard_s=1.0,
        )
        mode = "live_capture"
    report = analyze_static(capture_dir, warmup_s=args.warmup, formal_s=args.formal)
    attach_provenance(report, capture_dir, mode)
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    return finish_stage(
        report["result"], report_path, args.product_id,
        "calibrate_02_imu_noise.sh",
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


def run_allan(args) -> int:
    session = open_session(args)
    stage = "imu_allan"
    require_stage_ready(session, stage)
    attempt = next_attempt(session, stage)
    if args.input_capture:
        capture_dir = args.input_capture.resolve()
        mode = "offline_reanalysis"
    else:
        capture_dir = attempt / "capture"
        args.port = find_imu_port(args.port)
        input("将整机固定在恒温无振动位置，确认可连续供电后按回车开始Allan采集……")
        capture_serial(
            port=find_imu_port(args.port), baud=args.baud, duration_s=args.duration,
            output_dir=capture_dir, protocol=args.protocol, write_timestamp_csv=False,
            startup_discard_s=1.0,
        )
        mode = "live_capture"
    report = analyze_allan(capture_dir, min_duration_s=args.minimum_duration)
    attach_provenance(report, capture_dir, mode)
    plot_path = attempt / "allan.png"
    save_allan_plot(report, plot_path)
    report["evidence"]["allan_plot"] = str(plot_path.resolve())
    report["evidence"]["allan_plot_sha256"] = sha256_file(plot_path)
    imu_yaml = attempt / "imu_kalibr.yaml"
    dump_yaml(imu_yaml, {
        "rostopic": "/imu0", "update_rate": report["rate_hz"],
        "gyroscope_noise_density": report["gyroscope"]["noise_density"],
        "gyroscope_random_walk": report["gyroscope"]["random_walk"],
        "accelerometer_noise_density": report["accelerometer"]["noise_density"],
        "accelerometer_random_walk": report["accelerometer"]["random_walk"],
        "model": "calibrated", "timeframe": "ros", "use_accel": True,
    })
    report["evidence"]["imu_kalibr_yaml"] = str(imu_yaml.resolve())
    report["evidence"]["imu_kalibr_yaml_sha256"] = sha256_file(imu_yaml)
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    return finish_stage(
        report["result"], report_path, args.product_id,
        "calibrate_04_d405_factory.sh",
    )


def run_intrinsic(args) -> int:
    session = open_session(args)
    stage = "imu_multipose"
    require_stage_ready(session, stage)
    attempt = next_attempt(session, stage)
    if args.input_csv:
        csv_path = args.input_csv.resolve()
        poses = []
        mode = "offline_reanalysis"
    else:
        args.port = find_imu_port(args.port)
        csv_path, poses = capture_pose_csv(
            port=args.port,
            baud=args.baud,
            protocol=args.protocol,
            pose_duration_s=args.pose_duration,
            attempt=attempt,
        )
        mode = "live_capture"
    fit, validation = load_pose_means(csv_path)
    report = fit_and_validate(fit, validation)
    report["mode"] = mode
    report["pose_capture_health"] = poses
    report["evidence"] = {"pose_csv": str(csv_path), "pose_csv_sha256": sha256_file(csv_path)}
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    return finish_optional_diagnostic(report["result"], report_path, args.product_id)


def _passed_stage_report(session: CalibrationSession, stage: str) -> dict:
    path = session.root / session.workflow.stages[stage].evidence
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if document.get("result") != "PASS":
        raise WorkflowError(f"{stage}没有可用PASS报告")
    return document


def _verified_report_evidence(report: dict, key: str) -> Path:
    """Resolve a stage artifact and fail closed if its recorded hash changed."""
    evidence = report.get("evidence", {})
    raw_path = evidence.get(key)
    expected_hash = evidence.get(f"{key}_sha256")
    if not raw_path or not expected_hash:
        raise WorkflowError(f"前置报告缺少{key}路径或SHA-256")
    path = Path(raw_path).resolve()
    if not path.is_file():
        raise WorkflowError(f"前置证据不存在: {path}")
    if sha256_file(path) != expected_hash:
        raise WorkflowError(f"前置证据SHA-256不匹配: {path}")
    return path


def run_stereo(args) -> int:
    session = open_session(args)
    stage = "d405_stereo"
    require_stage_ready(session, stage)
    attempt = next_attempt(session, stage)

    legacy = bool(args.input_camchain or args.input_results or args.legacy_reference_only)
    if legacy:
        if not args.input_camchain or not args.input_results:
            raise WorkflowError("历史工程回归必须同时给--input-camchain和--input-results")
        camchain, results = args.input_camchain.resolve(), args.input_results.resolve()
        validation_recording = args.input_validation.resolve() if args.input_validation else None
        if validation_recording is None and not args.legacy_reference_only:
            raise WorkflowError(
                "离线第4步必须提供--input-validation；只有历史金样回归可显式使用"
                "--legacy-reference-only（该结果不可发布）"
            )
        if args.legacy_reference_only:
            baseline = yaml.safe_load(BASELINE.read_text(encoding="utf-8")) or {}
            expected = baseline.get("source_artifacts", {}).get("files", {})
            expected_hashes = {
                "calib_intrinsics-camchain.yaml": sha256_file(camchain),
                "calib_intrinsics-results-cam.txt": sha256_file(results),
            }
            if any(expected.get(name) != digest for name, digest in expected_hashes.items()):
                raise WorkflowError("--legacy-reference-only只允许与内置金样SHA-256完全一致的文件")
        report = stereo_report(camchain, results, BASELINE)
        camera_health_pass = True
        if validation_recording is not None:
            camera_health = camera_capture_health(validation_recording)
            heldout = heldout_epipolar_report(validation_recording, camchain)
            camera_health_pass = camera_health["result"] == "PASS"
            report["camera_capture_health"] = {"heldout_validation": camera_health}
            report["heldout_epipolar"] = heldout
            if heldout["result"] != "PASS" or not camera_health_pass:
                report["result"] = "FAIL"
        else:
            report["heldout_epipolar"] = {
                "result": "NOT_AVAILABLE_LEGACY_REFERENCE",
                "reason": "历史离线输入未提供与求解集独立的同步左右IR留出图像",
            }
        report["mode"] = "legacy_engineering_reanalysis"
        report["runtime_policy"] = "REFERENCE_ONLY_DO_NOT_INSTALL_FREE_FIT_INTRINSICS"
        report["release_eligible"] = False
    else:
        identity = _passed_stage_report(session, "identity")
        expected_serial = str(
            identity.get("devices", {}).get("d405", {}).get("serial", "")
        )
        if not expected_serial:
            raise WorkflowError("产品identity报告缺少D405序列号")

    if not legacy and args.input_factory_calibration:
        if not args.input_validation:
            raise WorkflowError("离线factory复验必须提供--input-validation")
        source = args.input_factory_calibration.resolve()
        factory = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if str(factory.get("device", {}).get("serial", "")) != expected_serial:
            raise WorkflowError("factory参数中的D405序列号与产品identity不一致")
        factory_export = attempt / "d405_factory_calibration.yaml"
        shutil.copy2(source, factory_export)
        camchain = factory_calibration_to_camchain(
            factory, attempt / "d405_factory_camchain.yaml"
        )
        validation_recording = args.input_validation.resolve()
        report = factory_stereo_report(
            factory, camchain, BASELINE, export_path=factory_export
        )
        camera_health = camera_capture_health(validation_recording)
        heldout = heldout_epipolar_report(validation_recording, camchain)
        report["camera_capture_health"] = {"heldout_validation": camera_health}
        report["heldout_epipolar"] = heldout
        report["mode"] = "offline_factory_reanalysis"
        report["release_eligible"] = False
        if camera_health["result"] != "PASS" or heldout["result"] != "PASS":
            report["result"] = "FAIL"
    elif not legacy:
        require_capture_stack(args.capture_runtime, args.rsusb_runtime, imu=True)
        port = find_imu_port(args.port)
        device = detect_d405(args.capture_runtime, args.rsusb_runtime)
        if device["serial"] != expected_serial:
            raise WorkflowError(
                f"当前D405序列号{device['serial']}与产品identity {expected_serial}不一致"
            )
        factory = read_d405_factory_calibration(
            args.capture_runtime, args.rsusb_runtime, expected_serial
        )
        factory_export = attempt / "d405_factory_calibration.yaml"
        dump_yaml(factory_export, factory)
        camchain = factory_calibration_to_camchain(
            factory, attempt / "d405_factory_camchain.yaml"
        )
        validation_root = attempt / "heldout_validation"
        validation_root.mkdir()
        input(
            "D405出厂参数已导出。保持整机固定，只移动AprilGrid；预览将从左上到"
            "右下逐格高亮，九格全部通过才结束。此数据只做独立极线验收，不拟合"
            "相机内参；按回车开始……"
        )
        validation_recording = collect_known_good(
            attempt=validation_root, mode="camera_validation", port=port, baud=args.baud,
            phase_seconds=args.validation_phase_seconds, capture_root=args.capture_runtime,
            rsusb_root=args.rsusb_runtime, preview=not args.no_preview,
        )
        report = factory_stereo_report(
            factory, camchain, BASELINE, export_path=factory_export
        )
        validation_health = camera_capture_health(validation_recording)
        heldout = heldout_epipolar_report(validation_recording, camchain)
        report["camera_capture_health"] = {"heldout_validation": validation_health}
        report["heldout_epipolar"] = heldout
        report["release_eligible"] = (
            report["result"] == "PASS" and heldout["result"] == "PASS"
            and validation_health["result"] == "PASS"
        )
        report["mode"] = "live_factory_export_and_validation"
        if heldout["result"] != "PASS" or validation_health["result"] != "PASS":
            report["result"] = "FAIL"
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    return finish_stage(
        report["result"], report_path, args.product_id,
        "calibrate_05_camera_imu.sh",
    )

def _run_one_imucam(args, attempt: Path, index: int,
                    stereo_camchain: Path) -> tuple[Path, Path, dict, dict, dict, dict]:
    run_dir = attempt / f"run_{index}"
    run_dir.mkdir()
    port = find_imu_port(args.port)
    input(f"第{index}/2份独立相机-IMU采集：固定AprilGrid，移动整套刚体；准备好后按回车……")
    recording = collect_known_good(
        attempt=run_dir, mode="imucam", port=port, baud=args.baud,
        phase_seconds=args.phase_seconds, capture_root=args.capture_runtime,
        rsusb_root=args.rsusb_runtime, preview=not args.no_preview,
    )
    bag = run_dir / "imucam.bag"
    convert_to_bag(recording, bag, args.capture_runtime)
    camchain, results = solve_camera_imu(
        bag, stereo_camchain, args.imu_yaml,
        Path(args.capture_runtime) / "config/aprilgrid_6x6_35mm.yaml", run_dir / "solve",
    )
    unit = recording / "left_hand"
    source_evidence = {
        "recording": str(recording.resolve()),
        "kalibr_bag": str(bag.resolve()),
        "kalibr_bag_sha256": sha256_file(bag),
        "raw_imu": str((unit / "imu.bin").resolve()),
        "raw_imu_sha256": sha256_file(unit / "imu.bin"),
    }
    return (
        camchain,
        results,
        imucam_residuals(results),
        camera_capture_health(recording),
        imu_capture_health(recording),
        source_evidence,
    )


def run_camera_imu(args) -> int:
    session = open_session(args)
    stage = "camera_imu"
    require_stage_ready(session, stage)
    attempt = next_attempt(session, stage)
    stereo = _passed_stage_report(session, "d405_stereo")
    stereo_camchain = _verified_report_evidence(stereo, "camchain")
    if args.run1 or args.run2:
        if not args.run1 or not args.run2 or not args.results1 or not args.results2:
            raise WorkflowError("离线复算必须提供--run1/--run2/--results1/--results2")
        run1, run2 = args.run1.resolve(), args.run2.resolve()
        residual1, residual2 = imucam_residuals(args.results1), imucam_residuals(args.results2)
        camera_health1 = camera_health2 = {"result": "NOT_AVAILABLE_OFFLINE_REANALYSIS"}
        imu_health1 = imu_health2 = {"result": "NOT_AVAILABLE_OFFLINE_REANALYSIS"}
        source1 = source2 = {"release_evidence": "NOT_AVAILABLE_OFFLINE_REANALYSIS"}
        mode = "offline_reanalysis"
    else:
        require_executable("kalibr_calibrate_imu_camera")
        require_capture_stack(args.capture_runtime, args.rsusb_runtime, imu=True)
        args.port = find_imu_port(args.port)
        identity = _passed_stage_report(session, "identity")
        bound_devices = identity.get("devices", {})
        bound_serial = str(bound_devices.get("d405", {}).get("serial", ""))
        bound_port = str(bound_devices.get("imu_port", ""))
        connected_d405 = detect_d405(args.capture_runtime, args.rsusb_runtime)
        if connected_d405.get("serial") != bound_serial:
            raise WorkflowError(
                f"当前D405序列号{connected_d405.get('serial')}与产品identity {bound_serial}不一致"
            )
        if str(Path(args.port).resolve()) != str(Path(bound_port).resolve()):
            raise WorkflowError("当前IMU串口与产品identity绑定端口不一致")
        allan = _passed_stage_report(session, "imu_allan")
        bound_imu_yaml = _verified_report_evidence(allan, "imu_kalibr_yaml")
        if args.imu_yaml is not None and args.imu_yaml.resolve() != bound_imu_yaml:
            raise WorkflowError(
                "正式第5步--imu-yaml必须是第2步绑定的imu_kalibr.yaml；"
                "自定义模型只能走研发复算"
            )
        args.imu_yaml = bound_imu_yaml
        if stereo.get("release_eligible") is not True:
            raise WorkflowError("正式第5步要求第4步live factory留出验收PASS")
        run1, results1, residual1, camera_health1, imu_health1, source1 = _run_one_imucam(
            args, attempt, 1, stereo_camchain
        )
        run2, results2, residual2, camera_health2, imu_health2, source2 = _run_one_imucam(
            args, attempt, 2, stereo_camchain
        )
        args.results1, args.results2 = results1, results2
        mode = "live_capture"
    report = compare_runs(run1, run2, BASELINE)
    residual_checks = {
        "run1_reprojection_mean_le_0_5px": residual1["reprojection_mean_px"] <= 0.5,
        "run2_reprojection_mean_le_0_5px": residual2["reprojection_mean_px"] <= 0.5,
        "run1_gyro_mean_le_0_02rad_s": residual1["gyroscope_mean_rad_s"] <= 0.02,
        "run2_gyro_mean_le_0_02rad_s": residual2["gyroscope_mean_rad_s"] <= 0.02,
        "run1_accel_mean_le_0_25m_s2": residual1["accelerometer_mean_m_s2"] <= 0.25,
        "run2_accel_mean_le_0_25m_s2": residual2["accelerometer_mean_m_s2"] <= 0.25,
    }
    report["result"] = "PASS" if report["result"] == "PASS" and all(residual_checks.values()) else "FAIL"
    capture_health_pass = mode == "live_capture" and all(
        item["result"] == "PASS"
        for item in (camera_health1, camera_health2, imu_health1, imu_health2)
    )
    if mode == "live_capture" and not capture_health_pass:
        report["result"] = "FAIL"
    numerical_result = report["result"]
    if mode == "offline_reanalysis":
        report["result"] = "BLOCKED"
        report["blocking_reason"] = (
            "离线输入未包含两次原始录制及相机/IMU正式窗口健康证据；"
            "数值结果仅供研发复算，不能推进产品第6步"
        )
    report["mode"] = mode
    report["numerical_result"] = numerical_result
    report["release_eligible"] = (
        mode == "live_capture" and numerical_result == "PASS" and capture_health_pass
    )
    report["residuals"] = {"run1": residual1, "run2": residual2}
    if mode == "live_capture":
        report["bound_device_identity"] = {
            "d405": connected_d405,
            "imu_port": args.port,
        }
    report["accelerometer_intrinsic_application"] = {
        "policy": "NOT_APPLIED_PRODUCT_BASELINE",
        "input": "raw_imu",
        "vins_bias": "estimated_online",
    }
    report["camera_capture_health"] = {"run1": camera_health1, "run2": camera_health2}
    report["imu_capture_health"] = {"run1": imu_health1, "run2": imu_health2}
    report["residual_checks"] = residual_checks
    report["evidence"] = {
        "input_stereo_camchain": str(stereo_camchain),
        "input_stereo_camchain_sha256": sha256_file(stereo_camchain),
        "run1_camchain": str(run1.resolve()), "run1_camchain_sha256": sha256_file(run1),
        "run2_camchain": str(run2.resolve()), "run2_camchain_sha256": sha256_file(run2),
        "run1_results": str(Path(args.results1).resolve()), "run1_results_sha256": sha256_file(args.results1),
        "run2_results": str(Path(args.results2).resolve()), "run2_results_sha256": sha256_file(args.results2),
        "run1_source": source1,
        "run2_source": source2,
    }
    if mode == "live_capture":
        report["evidence"].update({
            "input_imu_yaml": str(args.imu_yaml.resolve()),
            "input_imu_yaml_sha256": sha256_file(args.imu_yaml),
        })
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    return finish_stage(
        report["result"], report_path, args.product_id,
        "calibrate_06_world_z.sh",
    )


def _capture_world_z(args, attempt: Path, name: str, instruction: str,
                     runtime_candidate: dict) -> Path:
    input(f"{instruction}；完成准备后按回车开始 {args.duration:.0f} 秒录制……")
    runtime = Path(args.vins_runtime).resolve()
    launcher = runtime / "run_vins_realtime.sh"
    if not launcher.is_file():
        raise WorkflowError(f"product-live入口不存在: {launcher}")
    run_dir = attempt / f"{name}_runtime"
    environment = os.environ.copy()
    environment.update({
        "EGO_VIO_PRODUCT_LIVE_DEVICE_CONFIG": runtime_candidate["device_config"],
        "EGO_VIO_PRODUCT_LIVE_CONFIG": runtime_candidate["vins_config"],
        "EGO_VIO_PRODUCT_CALIBRATION_LABEL": "本产品必需前置阶段隔离候选",
        "EGO_VIO_RUN_DIR": str(run_dir),
        "EGO_VIO_DISABLE_VIEWER": "1",
    })
    log = attempt / f"{name}_capture.log"
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(
            [str(launcher), "product-live", "--duration-s", str(args.duration)],
            cwd=runtime, env=environment, stdout=stream, stderr=subprocess.STDOUT,
        )
    if completed.returncode:
        raise WorkflowError(f"{name}候选product-live录制失败，见{log}")
    if not (run_dir / "odometry_rect.csv").is_file():
        raise WorkflowError(f"{name}没有生成odometry_rect.csv，见{log}")
    destination = attempt / f"{name}_odometry_rect.csv"
    shutil.copy2(run_dir / "odometry_rect.csv", destination)
    return destination


def run_world_z(args) -> int:
    session = open_session(args)
    stage = "world_z"
    require_stage_ready(session, stage)
    attempt = next_attempt(session, stage)
    if args.planar or args.elevation:
        if len(args.planar or []) < 3 or len(args.elevation or []) < 2:
            raise WorkflowError("世界Z离线复算要求至少3条平面和2条真实升降轨迹")
        planar, elevation = args.planar, args.elevation
        mode = "offline_reanalysis"
        runtime_candidate = None
    else:
        runtime_candidate = build_stage6_runtime(
            destination=attempt / "candidate_runtime",
            runtime_root=Path(args.vins_runtime).resolve(),
            identity=_passed_stage_report(session, "identity"),
            stereo=_passed_stage_report(session, "d405_stereo"),
            camera_imu=_passed_stage_report(session, "camera_imu"),
        )
        planar = []
        for index in range(1, 4):
            path = _capture_world_z(args, attempt, f"planar_{index}",
                                    f"平面正例{index}/3：只在已知水平面内做丰富二维运动",
                                    runtime_candidate)
            planar.append(f"planar_{index}={path}")
        elevation = []
        for index in range(1, 3):
            path = _capture_world_z(args, attempt, f"elevation_{index}",
                                    f"升降负例{index}/2：执行有量具真值的真实上下运动",
                                    runtime_candidate)
            elevation.append(f"elevation_{index}={path}")
        mode = "live_capture"
    raw_report = attempt / "world_z_fit.json"
    command = [sys.executable, str(ROOT / "fit_multisession_world_z.py")]
    for item in planar:
        command.extend(["--planar", item])
    for item in elevation:
        command.extend(["--elevation", item])
    command.extend(["--out", str(raw_report)])
    completed = subprocess.run(command)
    if completed.returncode not in {0, 3} or not raw_report.is_file():
        raise WorkflowError("世界Z自动求解未生成有效报告")
    report = json.loads(raw_report.read_text(encoding="utf-8"))
    report["mode"] = mode
    if mode == "live_capture":
        report["runtime_candidate"] = runtime_candidate
        report["activation"] = "CANDIDATE_ONLY_NOT_INSTALLED"
        report["release_eligible"] = False
        report["release_blocking_reason"] = (
            "世界Z本阶段即使PASS，仍须完成历史/新数据SLAM A/B和长稳验收；"
            "脚本不会覆盖已签发product-live配置。"
        )
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    return finish_stage(report["result"], report_path, args.product_id, None)


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description="产品分步骤采集与自动标定")
    sub = top.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("product_id")
    init.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    init.add_argument("--port")
    init.add_argument("--capture-runtime", type=Path, default=DEFAULT_CAPTURE_RUNTIME)
    init.add_argument("--rsusb-runtime", type=Path, default=DEFAULT_RSUSB_RUNTIME)
    init.add_argument("--firmware-bin", type=Path)
    init.add_argument("--flash-evidence", type=Path)

    def common(name):
        item = sub.add_parser(name)
        item.add_argument("product_id")
        item.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
        item.add_argument("--port")
        item.add_argument("--baud", type=int, default=921600)
        item.add_argument("--protocol", choices=("auto", "kt_ex9_37", "stm32_combined_v1"), default="auto")
        return item

    static = common("imu-static")
    static.add_argument("--duration", type=float, default=600.0)
    static.add_argument("--warmup", type=float, default=120.0)
    static.add_argument("--formal", type=float, default=480.0)
    static.add_argument("--input-capture", type=Path)

    allan = common("imu-noise")
    allan.add_argument("--duration", type=float, default=10 * 3600.0)
    allan.add_argument("--minimum-duration", type=float, default=6 * 3600.0)
    allan.add_argument("--input-capture", type=Path)

    intrinsic = common("imu-intrinsic")
    intrinsic.add_argument("--pose-duration", type=float, default=30.0)
    intrinsic.add_argument("--input-csv", type=Path)

    def visual(name):
        item = common(name)
        item.add_argument("--capture-runtime", type=Path, default=DEFAULT_CAPTURE_RUNTIME)
        item.add_argument("--rsusb-runtime", type=Path, default=DEFAULT_RSUSB_RUNTIME)
        item.add_argument("--phase-seconds", type=float, default=20.0)
        item.add_argument("--no-preview", action="store_true")
        return item

    def add_factory_arguments(item):
        item.add_argument("--input-factory-calibration", type=Path,
                          help="离线复验用的librealsense factory导出YAML")
        item.add_argument("--input-validation", type=Path,
                          help="离线验收用的双IR九宫格录制目录")
        item.add_argument("--input-camchain", type=Path,
                          help="仅供历史工程回归，不用于客户产品参数")
        item.add_argument("--input-results", type=Path,
                          help="仅供历史工程回归，不用于客户产品参数")
        item.add_argument("--legacy-reference-only", action="store_true",
                          help="仅允许内置历史金样回归；报告不可发布")
        item.add_argument("--validation-phase-seconds", type=float, default=8.0)

    factory = visual("d405-factory")
    add_factory_arguments(factory)
    stereo = visual("d405-stereo")
    add_factory_arguments(stereo)

    camera_imu = visual("camera-imu")
    camera_imu.add_argument("--imu-yaml", type=Path)
    camera_imu.add_argument("--run1", type=Path)
    camera_imu.add_argument("--run2", type=Path)
    camera_imu.add_argument("--results1", type=Path)
    camera_imu.add_argument("--results2", type=Path)

    world_z = sub.add_parser("world-z")
    world_z.add_argument("product_id")
    world_z.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    world_z.add_argument(
        "--vins-runtime", type=Path,
        default=Path(os.environ.get("EGO_VIO_VINS_RUNTIME", "/home/robot/ego_vio_humble")),
    )
    world_z.add_argument("--duration", type=float, default=120.0)
    world_z.add_argument("--planar", action="append")
    world_z.add_argument("--elevation", action="append")
    return top


def main() -> int:
    args = parser().parse_args()
    try:
        return {
            "init": init_product,
            "imu-static": run_static,
            "imu-noise": run_allan,
            "imu-intrinsic": run_intrinsic,
            "d405-factory": run_stereo,
            "d405-stereo": run_stereo,
            "camera-imu": run_camera_imu,
            "world-z": run_world_z,
        }[args.command](args)
    except (WorkflowError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"BLOCKED：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
