#!/usr/bin/env python3
"""Separate customer capture-and-solve commands for product calibration stages 0-6."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from product_calibration.imu_analysis import analyze_allan, analyze_static, load_capture
from product_calibration.imu_ellipsoid import fit_and_validate, load_pose_means
from product_calibration.imu_stream import capture_serial
from product_calibration.compare_camera_imu import compare_runs
from product_calibration.kalibr_pipeline import (
    DEFAULT_CAPTURE_RUNTIME,
    DEFAULT_RSUSB_RUNTIME,
    collect_known_good,
    convert_to_bag,
    apply_accelerometer_calibration,
    detect_d405,
    d405_factory_stereo_baseline,
    imucam_residuals,
    heldout_epipolar_report,
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
DEFAULT_SESSION_ROOT = ROOT / "calibration_sessions"


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
    session = CalibrationSession.create(workflow, root, args.product_id, BASELINE)
    report = {
        "format_version": 1,
        "result": "PASS",
        "mode": "live_device_identity",
        "product_id": args.product_id,
        "host": socket.gethostname(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "devices": {"d405": d405, "imu_port": imu_port},
        "checks": {"one_d405": True, "imu_port_by_id_exists": imu_port.startswith("/dev/serial/by-id/")},
    }
    report_path = root / "identity/attempt_001/report.yaml"
    dump_yaml(report_path, report)
    session.record_result("identity", "PASS", report_path)
    print(f"PASS：产品档案已创建 {root}")
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
            output_dir=capture_dir, protocol=args.protocol,
        )
        mode = "live_capture"
    report = analyze_static(capture_dir, warmup_s=args.warmup, formal_s=args.formal)
    attach_provenance(report, capture_dir, mode)
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    print(f"{report['result']}：{report_path}")
    return 0 if report["result"] == "PASS" else 1


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
    print(f"{report['result']}：{report_path}")
    return 0 if report["result"] == "PASS" else 1


def pose_stable(samples) -> tuple[bool, dict]:
    gyro = np.asarray(samples["gyro_deg_s"], dtype=float)
    accel = np.asarray(samples["accel_g"], dtype=float)
    metrics = {
        "gyro_std_max_deg_s": float(np.max(np.std(gyro, axis=0))),
        "accel_std_max_g": float(np.max(np.std(accel, axis=0))),
    }
    return metrics["gyro_std_max_deg_s"] <= 0.15 and metrics["accel_std_max_g"] <= 0.006, metrics


def capture_pose_csv(args, attempt: Path) -> tuple[Path, list[dict]]:
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
                input(f"摆到分散姿态 {index + 1}/30（{split}），放稳后按回车采集……")
                pose_dir = attempt / "poses" / f"{pose_id}_try_{trial:02d}"
                capture_serial(
                    port=find_imu_port(args.port), baud=args.baud, duration_s=args.pose_duration,
                    output_dir=pose_dir, protocol=args.protocol, write_timestamp_csv=False,
                )
                samples = load_capture(pose_dir / "imu.bin")
                stable, metrics = pose_stable(samples)
                if stable:
                    break
                print(f"{pose_id}本次有移动，已保留失败数据并重采；指标={metrics}")
            mean = np.mean(np.asarray(samples["accel_g"], dtype=float), axis=0) * 9.80665
            writer.writerow({"pose_id": pose_id, "split": split, "ax": mean[0], "ay": mean[1], "az": mean[2]})
            pose_reports.append({"pose_id": pose_id, "split": split, **metrics, "capture_sha256": sha256_file(pose_dir / "imu.bin")})
    return csv_path, pose_reports


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
        csv_path, poses = capture_pose_csv(args, attempt)
        mode = "live_capture"
    fit, validation = load_pose_means(csv_path)
    report = fit_and_validate(fit, validation)
    report["mode"] = mode
    report["pose_capture_health"] = poses
    report["evidence"] = {"pose_csv": str(csv_path), "pose_csv_sha256": sha256_file(csv_path)}
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    print(f"{report['result']}：{report_path}")
    return 0 if report["result"] == "PASS" else 1


def run_stereo(args) -> int:
    session = open_session(args)
    stage = "d405_stereo"
    require_stage_ready(session, stage)
    attempt = next_attempt(session, stage)
    if args.input_camchain or args.input_results:
        if not args.input_camchain or not args.input_results:
            raise WorkflowError("离线复算必须同时给--input-camchain和--input-results")
        camchain, results = args.input_camchain.resolve(), args.input_results.resolve()
        factory_reference = None
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
        mode = "offline_reanalysis"
    else:
        # Fail before occupying devices or asking the operator to collect.
        require_executable("kalibr_calibrate_cameras")
        require_capture_stack(args.capture_runtime, args.rsusb_runtime, imu=True)
        port = find_imu_port(args.port)
        device = detect_d405(args.capture_runtime, args.rsusb_runtime)
        factory_reference = d405_factory_stereo_baseline(
            args.capture_runtime, args.rsusb_runtime, device["serial"]
        )
        training_root = attempt / "training"
        training_root.mkdir()
        input("求解集：固定整机，只移动AprilGrid；准备覆盖近中远、四角和多倾角后按回车……")
        recording = collect_known_good(
            attempt=training_root, mode="camera", port=port, baud=args.baud,
            phase_seconds=args.phase_seconds, capture_root=args.capture_runtime,
            rsusb_root=args.rsusb_runtime, preview=not args.no_preview,
        )
        validation_root = attempt / "heldout_validation"
        validation_root.mkdir()
        input("独立留出集：不移动整机，重新独立移动AprilGrid覆盖九宫格；这批图不参与求解，按回车开始……")
        validation_recording = collect_known_good(
            attempt=validation_root, mode="camera", port=port, baud=args.baud,
            phase_seconds=args.validation_phase_seconds, capture_root=args.capture_runtime,
            rsusb_root=args.rsusb_runtime, preview=not args.no_preview,
        )
        bag = attempt / "stereo.bag"
        convert_to_bag(recording, bag, args.capture_runtime)
        camchain, results = solve_stereo(
            bag, Path(args.capture_runtime) / "config/aprilgrid_6x6_35mm.yaml",
            attempt / "solve",
        )
        mode = "live_capture"
    report = stereo_report(camchain, results, BASELINE, factory_reference)
    if validation_recording is not None:
        heldout = heldout_epipolar_report(validation_recording, camchain)
        report["heldout_epipolar"] = heldout
        report["release_eligible"] = (
            mode == "live_capture" and report["result"] == "PASS"
            and heldout["result"] == "PASS"
        )
        if heldout["result"] != "PASS":
            report["result"] = "FAIL"
    else:
        report["heldout_epipolar"] = {
            "result": "NOT_AVAILABLE_LEGACY_REFERENCE",
            "reason": "历史离线输入未提供与求解集独立的同步左右IR留出图像",
        }
        report["release_eligible"] = False
    report["mode"] = mode
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    print(f"{report['result']}：{report_path}")
    return 0 if report["result"] == "PASS" else 1


def _passed_stage_report(session: CalibrationSession, stage: str) -> dict:
    path = session.root / session.workflow.stages[stage].evidence
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if document.get("result") != "PASS":
        raise WorkflowError(f"{stage}没有可用PASS报告")
    return document


def _run_one_imucam(args, attempt: Path, index: int, stereo_camchain: Path,
                    intrinsic: dict) -> tuple[Path, Path, dict, dict]:
    run_dir = attempt / f"run_{index}"
    run_dir.mkdir()
    port = find_imu_port(args.port)
    input(f"第{index}/2份独立相机-IMU采集：固定AprilGrid，移动整套刚体；准备好后按回车……")
    recording = collect_known_good(
        attempt=run_dir, mode="imucam", port=port, baud=args.baud,
        phase_seconds=args.phase_seconds, capture_root=args.capture_runtime,
        rsusb_root=args.rsusb_runtime, preview=not args.no_preview,
    )
    applied = apply_accelerometer_calibration(recording, intrinsic)
    bag = run_dir / "imucam.bag"
    convert_to_bag(recording, bag, args.capture_runtime)
    camchain, results = solve_camera_imu(
        bag, stereo_camchain, args.imu_yaml,
        Path(args.capture_runtime) / "config/aprilgrid_6x6_35mm.yaml", run_dir / "solve",
    )
    return camchain, results, imucam_residuals(results), applied


def run_camera_imu(args) -> int:
    session = open_session(args)
    stage = "camera_imu"
    require_stage_ready(session, stage)
    attempt = next_attempt(session, stage)
    stereo = _passed_stage_report(session, "d405_stereo")
    intrinsic = _passed_stage_report(session, "imu_multipose")
    stereo_camchain = Path(stereo["evidence"]["camchain"])
    if args.run1 or args.run2:
        if not args.run1 or not args.run2 or not args.results1 or not args.results2:
            raise WorkflowError("离线复算必须提供--run1/--run2/--results1/--results2")
        run1, run2 = args.run1.resolve(), args.run2.resolve()
        residual1, residual2 = imucam_residuals(args.results1), imucam_residuals(args.results2)
        applied1 = applied2 = {"mode": "already_applied_by_input_owner"}
        mode = "offline_reanalysis"
    else:
        require_executable("kalibr_calibrate_imu_camera")
        require_capture_stack(args.capture_runtime, args.rsusb_runtime, imu=True)
        args.port = find_imu_port(args.port)
        detect_d405(args.capture_runtime, args.rsusb_runtime)
        if args.imu_yaml is None:
            allan = _passed_stage_report(session, "imu_allan")
            args.imu_yaml = Path(allan["evidence"]["imu_kalibr_yaml"])
        run1, results1, residual1, applied1 = _run_one_imucam(args, attempt, 1, stereo_camchain, intrinsic)
        run2, results2, residual2, applied2 = _run_one_imucam(args, attempt, 2, stereo_camchain, intrinsic)
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
    report["mode"] = mode
    report["residuals"] = {"run1": residual1, "run2": residual2}
    report["accelerometer_intrinsic_application"] = {"run1": applied1, "run2": applied2}
    report["residual_checks"] = residual_checks
    report["evidence"] = {
        "run1_camchain": str(run1.resolve()), "run1_camchain_sha256": sha256_file(run1),
        "run2_camchain": str(run2.resolve()), "run2_camchain_sha256": sha256_file(run2),
        "run1_results": str(Path(args.results1).resolve()), "run1_results_sha256": sha256_file(args.results1),
        "run2_results": str(Path(args.results2).resolve()), "run2_results_sha256": sha256_file(args.results2),
    }
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    print(f"{report['result']}：{report_path}")
    return 0 if report["result"] == "PASS" else 1


def _capture_world_z(args, attempt: Path, name: str, instruction: str) -> Path:
    input(f"{instruction}；完成准备后按回车开始 {args.duration:.0f} 秒录制……")
    runtime = Path(args.vins_runtime).resolve()
    launcher = runtime / "run_vins_realtime.sh"
    if not launcher.is_file():
        raise WorkflowError(f"冻结SLAM入口不存在: {launcher}")
    before = set(Path("/tmp").glob("ego_vio_vins_live_*"))
    log = attempt / f"{name}_capture.log"
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(
            [str(launcher), "frozen-record", "--duration", str(args.duration)],
            cwd=runtime, stdout=stream, stderr=subprocess.STDOUT,
        )
    if completed.returncode:
        raise WorkflowError(f"{name}冻结链录制失败，见{log}")
    created = sorted(set(Path("/tmp").glob("ego_vio_vins_live_*")) - before)
    if len(created) != 1 or not (created[0] / "odometry_rect.csv").is_file():
        raise WorkflowError(f"{name}没有生成唯一odometry_rect.csv")
    destination = attempt / f"{name}_odometry_rect.csv"
    shutil.copy2(created[0] / "odometry_rect.csv", destination)
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
    else:
        planar = []
        for index in range(1, 4):
            path = _capture_world_z(args, attempt, f"planar_{index}",
                                    f"平面正例{index}/3：只在已知水平面内做丰富二维运动")
            planar.append(f"planar_{index}={path}")
        elevation = []
        for index in range(1, 3):
            path = _capture_world_z(args, attempt, f"elevation_{index}",
                                    f"升降负例{index}/2：执行有量具真值的真实上下运动")
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
    exit_code = 0 if report["result"] == "PASS" else 1
    if mode == "live_capture":
        report["fit_result_before_runtime_gate"] = report["result"]
        report["result"] = "BLOCKED"
        report["activation"] = "FORBIDDEN_HISTORICAL_RUNTIME_NOT_CANDIDATE_CONFIG"
        report["blocking_reason"] = (
            "实时入口仍使用历史冻结配置，尚未把本产品第1至5步候选参数和63字节IMU"
            "校正链接入冻结VINS；本次轨迹只作开发证据。"
        )
        exit_code = 2
    report_path = attempt / "report.yaml"
    dump_yaml(report_path, report)
    session.record_result(stage, report["result"], report_path)
    print(f"{report['result']}：{report_path}")
    return exit_code


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description="产品分步骤采集与自动标定")
    sub = top.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("product_id")
    init.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    init.add_argument("--port")
    init.add_argument("--capture-runtime", type=Path, default=DEFAULT_CAPTURE_RUNTIME)
    init.add_argument("--rsusb-runtime", type=Path, default=DEFAULT_RSUSB_RUNTIME)

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
    allan.add_argument("--duration", type=float, default=16 * 3600.0)
    allan.add_argument("--minimum-duration", type=float, default=15 * 3600.0)
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

    stereo = visual("d405-stereo")
    stereo.add_argument("--input-camchain", type=Path)
    stereo.add_argument("--input-results", type=Path)
    stereo.add_argument("--input-validation", type=Path,
                        help="离线验收用、与求解集独立的双IR录制目录")
    stereo.add_argument("--legacy-reference-only", action="store_true",
                        help="仅允许无留出图的历史金样回归；报告不可发布")
    stereo.add_argument("--validation-phase-seconds", type=float, default=8.0)

    camera_imu = visual("camera-imu")
    camera_imu.add_argument("--imu-yaml", type=Path)
    camera_imu.add_argument("--run1", type=Path)
    camera_imu.add_argument("--run2", type=Path)
    camera_imu.add_argument("--results1", type=Path)
    camera_imu.add_argument("--results2", type=Path)

    world_z = sub.add_parser("world-z")
    world_z.add_argument("product_id")
    world_z.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    world_z.add_argument("--vins-runtime", type=Path, default=Path("/home/robot/ego_vio_humble"))
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
            "d405-stereo": run_stereo,
            "camera-imu": run_camera_imu,
            "world-z": run_world_z,
        }[args.command](args)
    except (WorkflowError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"BLOCKED：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
