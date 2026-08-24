"""Reuse the proven D405 collector/converter and run fail-closed Kalibr solves."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml
import cv2

from .compatible_imu_reader import CompatibleImuReader
from .workflow import WorkflowError, sha256_file
from .imu_stream import NORMALIZED_FORMAT, NORMALIZED_SIZE


DEFAULT_CAPTURE_RUNTIME = Path(
    os.environ.get("EGO_VIO_CAPTURE_RUNTIME", "/home/robot/ego_vio_humble")
)
DEFAULT_RSUSB_RUNTIME = Path(
    os.environ.get("EGO_VIO_RSUSB_RUNTIME", "/home/robot/ego_vio_humble")
)


def rsusb_python_path(root: Path) -> Path:
    candidates = sorted((Path(root) / ".deps").glob("librealsense-rsusb-*/python"))
    if len(candidates) != 1:
        raise WorkflowError(f"RSUSB Python运行库不唯一或缺失: {root}/.deps")
    return candidates[0]


def prepare_capture_imports(capture_root: Path, rsusb_root: Path) -> None:
    capture_root = Path(capture_root).resolve()
    rsusb = rsusb_python_path(rsusb_root).resolve()
    for path in (str(rsusb), str(capture_root)):
        if path not in sys.path:
            sys.path.insert(0, path)


def detect_d405(capture_root: Path, rsusb_root: Path) -> dict:
    prepare_capture_imports(capture_root, rsusb_root)
    import pyrealsense2 as rs

    devices = []
    for device in rs.context().query_devices():
        name = device.get_info(rs.camera_info.name)
        if "D405" in name:
            devices.append({
                "name": name,
                "serial": device.get_info(rs.camera_info.serial_number),
                "firmware": device.get_info(rs.camera_info.firmware_version),
            })
    if len(devices) != 1:
        raise WorkflowError(f"必须且只能连接1台D405，当前识别到{len(devices)}台")
    return devices[0]


def _d405_factory_ir_profiles(capture_root: Path, rsusb_root: Path,
                              serial: str):
    prepare_capture_imports(capture_root, rsusb_root)
    import pyrealsense2 as rs

    device = next((item for item in rs.context().query_devices()
                   if item.get_info(rs.camera_info.serial_number) == serial), None)
    if device is None:
        raise WorkflowError(f"D405序列号已变化或断开: {serial}")
    profiles = {}
    for sensor in device.query_sensors():
        for profile in sensor.get_stream_profiles():
            try:
                video = profile.as_video_stream_profile()
                if (video.stream_type() == rs.stream.infrared and
                        video.width() == 1280 and video.height() == 720 and
                        video.fps() == 30 and video.format() == rs.format.y8 and
                        video.stream_index() in {1, 2}):
                    profiles[video.stream_index()] = video
            except RuntimeError:
                continue
    if set(profiles) != {1, 2}:
        raise WorkflowError("D405缺少双IR 1280x720@30 Y8 factory profile")
    return rs, device, profiles


def read_d405_factory_calibration(capture_root: Path, rsusb_root: Path,
                                  serial: str) -> dict:
    """Export the active D405 dual-IR factory profile without refitting it."""
    rs, device, profiles = _d405_factory_ir_profiles(
        capture_root, rsusb_root, serial
    )
    camera_names = {1: "cam0_left_ir", 2: "cam1_right_ir"}
    cameras = {}
    for index, profile in profiles.items():
        intrinsic = profile.get_intrinsics()
        cameras[camera_names[index]] = {
            "fx": float(intrinsic.fx),
            "fy": float(intrinsic.fy),
            "cx": float(intrinsic.ppx),
            "cy": float(intrinsic.ppy),
            "distortion_model": str(intrinsic.model).rsplit(".", 1)[-1],
            "coeffs": [float(value) for value in intrinsic.coeffs],
        }
    extrinsic = profiles[1].get_extrinsics_to(profiles[2])
    rotation = np.asarray(extrinsic.rotation, dtype=float).reshape(3, 3)
    translation = np.asarray(extrinsic.translation, dtype=float)
    return {
        "format_version": 1,
        "source": "librealsense_active_factory_profile",
        "device": {
            "name": device.get_info(rs.camera_info.name),
            "serial": serial,
            "firmware": device.get_info(rs.camera_info.firmware_version),
        },
        "stream": {"width": 1280, "height": 720, "fps": 30, "format": "y8"},
        **cameras,
        "T_cam1_cam0": {
            "rotation": rotation.tolist(),
            "translation_m": translation.tolist(),
        },
    }


def d405_factory_stereo_baseline(capture_root: Path, rsusb_root: Path,
                                 serial: str) -> float:
    """Read the active 1280x720@30 IR1->IR2 factory extrinsic from the device."""
    calibration = read_d405_factory_calibration(capture_root, rsusb_root, serial)
    translation = calibration["T_cam1_cam0"]["translation_m"]
    return float(np.linalg.norm(np.asarray(translation, dtype=float)))


def factory_calibration_to_camchain(calibration: dict, output: Path) -> Path:
    """Write a fixed Kalibr-format camchain from a librealsense factory export."""
    stream = calibration.get("stream", {})
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    transform = calibration.get("T_cam1_cam0", {})
    rotation = np.asarray(transform.get("rotation", []), dtype=float)
    translation = np.asarray(transform.get("translation_m", []), dtype=float)
    if rotation.shape != (3, 3) or translation.shape != (3,):
        raise WorkflowError("D405 factory外参形状错误")
    matrix = np.eye(4, dtype=float)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = translation

    document = {}
    for index, key in enumerate(("cam0_left_ir", "cam1_right_ir")):
        camera = calibration.get(key, {})
        coefficients = [float(value) for value in camera.get("coeffs", [])]
        if len(coefficients) < 4:
            raise WorkflowError(f"{key} factory畸变参数不足4项")
        item = {
            "camera_model": "pinhole",
            "intrinsics": [
                float(camera["fx"]), float(camera["fy"]),
                float(camera["cx"]), float(camera["cy"]),
            ],
            "distortion_model": "radtan",
            "distortion_coeffs": coefficients[:4],
            "resolution": [width, height],
            "rostopic": f"/cam{index}/image_raw",
        }
        if index == 1:
            item["T_cn_cnm1"] = matrix.tolist()
        document[f"cam{index}"] = item
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return output


def factory_stereo_report(calibration: dict, camchain: Path, baseline: Path,
                          *, export_path: Path | None = None) -> dict:
    """Validate that a connected D405 profile is safe for the fixed runtime model."""
    stream = calibration.get("stream", {})
    cameras = [calibration.get("cam0_left_ir", {}),
               calibration.get("cam1_right_ir", {})]
    transform = calibration.get("T_cam1_cam0", {})
    rotation = np.asarray(transform.get("rotation", []), dtype=float)
    translation = np.asarray(transform.get("translation_m", []), dtype=float)
    baseline_m = float(np.linalg.norm(translation)) if translation.shape == (3,) else math.inf
    golden = yaml.safe_load(Path(baseline).read_text(encoding="utf-8"))["camera_stereo"]
    reference_m = float(golden["factory_baseline_m"])

    intrinsics_valid = True
    rectified = True
    for camera in cameras:
        values = np.asarray([
            camera.get("fx"), camera.get("fy"), camera.get("cx"), camera.get("cy")
        ], dtype=float)
        coefficients = np.asarray(camera.get("coeffs", []), dtype=float)
        intrinsics_valid = intrinsics_valid and (
            values.shape == (4,) and np.all(np.isfinite(values))
            and values[0] > 0.0 and values[1] > 0.0
            and 0.0 <= values[2] < int(stream.get("width", 0))
            and 0.0 <= values[3] < int(stream.get("height", 0))
        )
        rectified = rectified and (
            coefficients.size >= 4 and np.all(np.isfinite(coefficients))
            and float(np.max(np.abs(coefficients))) <= 1e-9
        )
    rigid_rotation = (
        rotation.shape == (3, 3) and np.all(np.isfinite(rotation))
        and np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-6)
        and abs(float(np.linalg.det(rotation)) - 1.0) <= 1e-6
    )
    checks = {
        "source_is_active_librealsense_factory_profile": (
            calibration.get("source") == "librealsense_active_factory_profile"
        ),
        "device_is_d405_with_identity": (
            "D405" in str(calibration.get("device", {}).get("name", ""))
            and bool(str(calibration.get("device", {}).get("serial", "")))
        ),
        "dual_ir_profile_is_1280x720_30_y8": (
            int(stream.get("width", 0)) == 1280
            and int(stream.get("height", 0)) == 720
            and int(stream.get("fps", 0)) == 30
            and str(stream.get("format", "")).lower() == "y8"
        ),
        "factory_intrinsics_finite_and_in_frame": bool(intrinsics_valid),
        "factory_ir_profiles_rectified": bool(rectified),
        "factory_stereo_rotation_is_rigid": bool(rigid_rotation),
        "baseline_within_0_5mm_of_golden_factory": (
            math.isfinite(baseline_m) and abs(baseline_m - reference_m) <= 0.0005
        ),
    }
    evidence = {
        "camchain": str(Path(camchain).resolve()),
        "camchain_sha256": sha256_file(camchain),
        "factory_calibration_sha256": None,
    }
    if export_path is not None:
        evidence.update({
            "factory_calibration": str(Path(export_path).resolve()),
            "factory_calibration_sha256": sha256_file(export_path),
        })
    return {
        "format_version": 1,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "intel_factory_profile_export_and_fixed_camchain_validation",
        "runtime_policy": "USE_INTEL_FACTORY_RECTIFIED_INTRINSICS",
        "metrics": {
            "serial": calibration.get("device", {}).get("serial"),
            "firmware": calibration.get("device", {}).get("firmware"),
            "cam0_intrinsics": cameras[0],
            "cam1_intrinsics": cameras[1],
            "baseline_m": baseline_m,
            "golden_factory_baseline_m": reference_m,
            "baseline_delta_mm": abs(baseline_m - reference_m) * 1000.0,
        },
        "thresholds": {
            "max_abs_rectified_distortion": 1e-9,
            "max_factory_baseline_delta_mm": 0.5,
        },
        "checks": checks,
        "evidence": evidence,
    }


def require_capture_stack(capture_root: Path, rsusb_root: Path, *, imu: bool) -> None:
    capture_root = Path(capture_root)
    required = [
        capture_root / "scripts/collect_calib_data.py",
        capture_root / "scripts/convert_to_kalibr_bag.py",
        capture_root / "config/aprilgrid_6x6_35mm.yaml",
    ]
    for path in required:
        if not path.is_file():
            raise WorkflowError(f"历史已验证标定文件缺失: {path}")
    prepare_capture_imports(capture_root, rsusb_root)
    try:
        import pyrealsense2  # noqa: F401
        import aprilgrid  # noqa: F401
        import rosbags  # noqa: F401
    except ImportError as exc:
        raise WorkflowError(f"标定采集依赖缺失: {exc.name}") from exc
    if imu:
        try:
            import serial  # noqa: F401
        except ImportError as exc:
            raise WorkflowError("缺少pyserial，无法采集IMU") from exc


def write_device_config(path: Path, *, port: str, baud: int, serial: str) -> None:
    document = {
        "recording": {"out_dir": "./recordings", "jpg_quality": 90, "save_depth": False, "imu_bin": True},
        "units": [{
            "name": "left_hand", "role": "realtime_vio",
            "imu": {"port": port, "baud": baud, "calibration": ""},
            "camera": {
                "serial": serial, "width": 1280, "height": 720, "fps": 30,
                "enable_depth": False, "stereo_ir": True, "auto_exposure": False,
                "exposure_us": 30000, "gain": 100, "cam_latency_ms": 0.0,
            },
            "vio": {"backend": "vins_fusion_ros2", "stereo": True},
        }],
    }
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")


def collect_known_good(*, attempt: Path, mode: str, port: str, baud: int,
                       phase_seconds: float, capture_root: Path, rsusb_root: Path,
                       preview: bool = True) -> Path:
    """Call the accepted staged collector after replacing only its IMU transport."""
    # The proven converter currently expects imu.bin even for a camera-only
    # Kalibr bag, so both modes must prove the serial dependency up front.
    require_capture_stack(capture_root, rsusb_root, imu=True)
    device = detect_d405(capture_root, rsusb_root)
    config = attempt / "devices_capture.yaml"
    write_device_config(config, port=port, baud=baud, serial=device["serial"])
    collector_path = Path(capture_root) / "scripts/collect_calib_data.py"
    from ego_vio.imu import imu_reader as legacy_imu_module

    legacy_imu_module.ImuReader = CompatibleImuReader
    spec = importlib.util.spec_from_file_location("product_known_good_collector", collector_path)
    if spec is None or spec.loader is None:
        raise WorkflowError(f"无法加载采集脚本: {collector_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = set(attempt.glob("recordings/calib_*"))
    module.collect_calib_data(
        str(config), phase_seconds, attempt / "recordings", strict=True,
        aprilgrid_cfg=Path(capture_root) / "config/aprilgrid_6x6_35mm.yaml",
        mode=mode, preview=preview, exposure_us=30000, gain=100,
    )
    created = sorted(set(attempt.glob("recordings/calib_*")) - before)
    if len(created) != 1:
        raise WorkflowError(f"采集没有生成唯一会话目录: {created}")
    unit = created[0] / "left_hand"
    required = [unit / "frames", unit / "frames_right", unit / "camera_ts.csv",
                unit / "camera_capture_health.yaml"]
    if mode == "imucam":
        required.extend([unit / "imu.bin", unit / "imu_ts.csv",
                         unit / "imu_capture_health.yaml"])
    if any(not item.exists() for item in required):
        raise WorkflowError("采集不完整，缺少双IR或IMU证据")
    return created[0]


def camera_capture_health(recording: Path) -> dict:
    """Verify camera warm-up evidence and zero gaps in the saved formal window."""
    unit = Path(recording) / "left_hand"
    health_path = unit / "camera_capture_health.yaml"
    csv_path = unit / "camera_ts.csv"
    if not health_path.is_file() or not csv_path.is_file():
        return {
            "result": "FAIL",
            "method": "camera_warmup_and_formal_counter_audit",
            "checks": {"warmup_and_formal_evidence_present": False},
            "metrics": {},
            "evidence": {
                "recording": str(Path(recording).resolve()),
                "missing": [str(path) for path in (health_path, csv_path) if not path.is_file()],
            },
        }
    reported = yaml.safe_load(health_path.read_text(encoding="utf-8")) or {}
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    try:
        frame_numbers = [int(row["frame_number"]) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError(f"相机正式窗口时间戳格式错误: {csv_path}") from exc
    formal_drops = sum(
        max(0, current - previous - 1)
        for previous, current in zip(frame_numbers, frame_numbers[1:])
    )
    left_names = {path.name for path in (unit / "frames").glob("*.jpg")}
    right_names = {path.name for path in (unit / "frames_right").glob("*.jpg")}
    metrics = reported.get("metrics", {})
    warmup = metrics.get("warmup", {})
    checks = {
        "warmup_and_formal_evidence_present": True,
        "collector_health_pass": reported.get("result") == "PASS",
        "warmup_completed": warmup.get("completed") is True,
        "warmup_consecutive_frames_ge_60": (
            int(warmup.get("required_consecutive_frames", 0)) >= 60
        ),
        "formal_device_frame_drops_zero": formal_drops == 0,
        "reported_formal_drops_match_csv": (
            int(metrics.get("formal_dropped_frames", -1)) == formal_drops
        ),
        "all_formal_stereo_files_paired": left_names == right_names,
        "formal_frame_files_match_timestamps": len(left_names) == len(rows),
        "formal_stereo_pair_mismatches_zero": (
            int(metrics.get("formal_pair_mismatches", -1)) == 0
        ),
    }
    return {
        "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "camera_warmup_and_formal_counter_audit",
        "metrics": {
            "formal_frames": len(rows),
            "formal_device_frame_drops": formal_drops,
            "left_images": len(left_names),
            "right_images": len(right_names),
            "warmup": warmup,
            "guided_nine_grid": metrics.get("guided_nine_grid", {}),
        },
        "checks": checks,
        "evidence": {
            "recording": str(Path(recording).resolve()),
            "camera_capture_health": str(health_path.resolve()),
            "camera_capture_health_sha256": sha256_file(health_path),
            "camera_ts": str(csv_path.resolve()),
            "camera_ts_sha256": sha256_file(csv_path),
        },
    }


def imu_capture_health(recording: Path) -> dict:
    """Verify persisted reader counters and the saved formal IMU sequence."""
    unit = Path(recording) / "left_hand"
    health_path = unit / "imu_capture_health.yaml"
    csv_path = unit / "imu_ts.csv"
    if not health_path.is_file() or not csv_path.is_file():
        return {
            "result": "FAIL",
            "method": "persisted_reader_and_formal_counter_audit",
            "checks": {"reader_and_formal_evidence_present": False},
            "metrics": {},
            "evidence": {
                "recording": str(Path(recording).resolve()),
                "missing": [str(path) for path in (health_path, csv_path) if not path.is_file()],
            },
        }
    reported = yaml.safe_load(health_path.read_text(encoding="utf-8")) or {}
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    try:
        counters = [int(row["counter"]) for row in rows]
        timestamps = [float(row["ts_mono"]) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError(f"IMU正式窗口时间戳格式错误: {csv_path}") from exc
    gaps = 0
    resets = 0
    stalls = 0
    for previous, current in zip(counters, counters[1:]):
        delta = (current - previous) & 0xFFFFFFFF
        if delta == 0:
            stalls += 1
        elif current == 1 and previous != 0:
            resets += 1
        elif delta != 1:
            gaps += max(1, delta - 1) if delta < 4096 else 1
    regressions = sum(current <= previous for previous, current in zip(timestamps, timestamps[1:]))
    duration = timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0.0
    rate = (len(timestamps) - 1) / duration if duration > 0.0 else 0.0
    metrics = reported.get("metrics", {})
    reader = metrics.get("reader", {})
    zero_reader_fields = (
        "frames_bad", "resyncs", "dropped_frames",
        "counter_resets", "counter_stalls", "serial_errors", "serial_reconnects",
    )
    protocol = metrics.get("protocol")
    if protocol == "stm32_combined_v1":
        zero_reader_fields += (
            "sequence_gaps", "invalid_imu_flags", "queue_overflow_flags",
        )
    checks = {
        "reader_and_formal_evidence_present": True,
        "collector_health_pass": reported.get("result") == "PASS",
        "formal_frames_positive": len(rows) > 0,
        "reported_formal_frames_match_csv": int(metrics.get("formal_frames", -1)) == len(rows),
        "formal_counter_gaps_zero": gaps == 0,
        "formal_counter_resets_zero": resets == 0,
        "formal_counter_stalls_zero": stalls == 0,
        "formal_timestamps_strictly_increasing": regressions == 0,
        "formal_rate_380_to_420hz": 380.0 <= rate <= 420.0,
        "reader_transport_counters_zero": all(
            int(reader.get(name, -1)) == 0 for name in zero_reader_fields
        ),
        "user_not_aborted": metrics.get("user_aborted") is False,
    }
    return {
        "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "persisted_reader_and_formal_counter_audit",
        "metrics": {
            "formal_frames": len(rows),
            "formal_counter_gaps": gaps,
            "formal_counter_resets": resets,
            "formal_counter_stalls": stalls,
            "formal_timestamp_regressions": regressions,
            "formal_rate_hz": rate,
            "reader": reader,
            "protocol": protocol,
            "unavailable_in_protocol": metrics.get("unavailable_in_protocol", []),
        },
        "checks": checks,
        "evidence": {
            "recording": str(Path(recording).resolve()),
            "imu_capture_health": str(health_path.resolve()),
            "imu_capture_health_sha256": sha256_file(health_path),
            "imu_ts": str(csv_path.resolve()),
            "imu_ts_sha256": sha256_file(csv_path),
        },
    }


def convert_to_bag(recording: Path, output: Path, capture_root: Path) -> None:
    command = [sys.executable, str(Path(capture_root) / "scripts/convert_to_kalibr_bag.py"),
               "--input", str(recording), "--output", str(output), "--mono"]
    subprocess.run(command, check=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise WorkflowError("Kalibr bag转换未生成有效文件")


def apply_accelerometer_calibration(recording: Path, intrinsic_report: dict) -> dict:
    """Preserve the raw stream and create the intrinsically corrected Kalibr stream."""
    unit = Path(recording) / "left_hand"
    active = unit / "imu.bin"
    raw = unit / "imu_raw.bin"
    if raw.exists():
        raise WorkflowError(f"拒绝覆盖已有原始IMU: {raw}")
    bias = np.asarray(intrinsic_report["bias_m_s2"], dtype=float)
    correction = np.asarray(intrinsic_report["correction_matrix"], dtype=float)
    if bias.shape != (3,) or correction.shape != (3, 3):
        raise WorkflowError("IMU内参报告的bias或correction_matrix形状错误")
    temporary = unit / "imu_calibrated.tmp"
    count = 0
    with active.open("rb") as source, temporary.open("wb") as destination:
        while True:
            chunk = source.read(NORMALIZED_SIZE)
            if not chunk:
                break
            if len(chunk) != NORMALIZED_SIZE:
                raise WorkflowError("联合采集imu.bin末尾记录不完整")
            values = list(struct.unpack(NORMALIZED_FORMAT, chunk))
            accel_g = np.asarray(values[5:8], dtype=float)
            values[5:8] = (correction @ (accel_g * 9.80665 - bias) / 9.80665).tolist()
            destination.write(struct.pack(NORMALIZED_FORMAT, *values))
            count += 1
    active.rename(raw)
    temporary.rename(active)
    return {
        "samples": count,
        "raw_imu_bin": str(raw.resolve()), "raw_imu_bin_sha256": sha256_file(raw),
        "calibrated_imu_bin": str(active.resolve()), "calibrated_imu_bin_sha256": sha256_file(active),
        "method": "a_cal=M*(a_raw_m_s2-bias)/g",
    }


def require_executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        user_install = Path.home() / ".local/bin" / name
        if user_install.is_file() and os.access(user_install, os.X_OK):
            path = str(user_install)
    if not path:
        raise WorkflowError(f"缺少{name}；请先安装Kalibr环境，再开始硬件采集")
    return path


def run_command(
    command: list[str],
    cwd: Path,
    log: Path,
    *,
    completed_outputs: tuple[Path, ...] = (),
) -> None:
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
    if completed.returncode:
        solver_completed = (
            bool(completed_outputs)
            and all(path.is_file() and path.stat().st_size > 0 for path in completed_outputs)
            and "Calibration complete." in log.read_text(encoding="utf-8", errors="replace")
        )
        if solver_completed:
            return
        raise WorkflowError(f"求解命令失败({completed.returncode})，见 {log}")


def solve_stereo(bag: Path, target: Path, output_dir: Path) -> tuple[Path, Path]:
    executable = require_executable("kalibr_calibrate_cameras")
    output_dir.mkdir(parents=True, exist_ok=True)
    local_bag = output_dir / "stereo.bag"
    camchain = output_dir / "stereo-camchain.yaml"
    results = output_dir / "stereo-results-cam.txt"
    if bag.resolve() != local_bag.resolve():
        os.symlink(bag.resolve(), local_bag)
    run_command([
        executable, "--bag", str(local_bag),
        "--topics", "/cam0/image_raw", "/cam1/image_raw",
        "--models", "pinhole-radtan", "pinhole-radtan",
        "--target", str(target), "--dont-show-report",
    ], output_dir, output_dir / "kalibr_stereo.log", completed_outputs=(camchain, results))
    return camchain, results


def stereo_report(camchain: Path, results: Path, baseline: Path,
                  factory_reference_m: float | None = None) -> dict:
    document = yaml.safe_load(camchain.read_text(encoding="utf-8")) or {}
    try:
        transform = np.asarray(document["cam1"]["T_cn_cnm1"], dtype=float)
    except (KeyError, TypeError) as exc:
        raise WorkflowError("camchain缺少cam1.T_cn_cnm1") from exc
    baseline_m = float(np.linalg.norm(transform[:3, 3]))
    text = results.read_text(encoding="utf-8")
    matches = re.findall(r"cam([01]).*?reprojection error:\s*\[[^]]+\]\s*\+-\s*\[([^]]+)\]", text, re.S | re.I)
    rms = {}
    for camera, values in matches:
        sigma = [float(value) for value in values.replace(",", " ").split()]
        rms[f"cam{camera}"] = float(math.sqrt(sum(value * value for value in sigma)))
    if set(rms) != {"cam0", "cam1"}:
        raise WorkflowError("无法从Kalibr结果解析双目重投影误差")
    golden = yaml.safe_load(Path(baseline).read_text(encoding="utf-8"))["camera_stereo"]
    reference = float(golden["factory_baseline_m"] if factory_reference_m is None else factory_reference_m)
    checks = {
        "cam0_reprojection_rms_le_0_5px": rms["cam0"] <= 0.5,
        "cam1_reprojection_rms_le_0_5px": rms["cam1"] <= 0.5,
        "baseline_within_0_5mm_of_factory": abs(baseline_m - reference) <= 0.0005,
    }
    return {
        "format_version": 1, "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "known_good_dual_ir_collector_plus_kalibr",
        "metrics": {"reprojection_rms_px": rms, "baseline_m": baseline_m,
                    "factory_reference_m": reference,
                    "factory_reference_source": "golden_history" if factory_reference_m is None else "connected_d405_profile",
                    "baseline_delta_mm": abs(baseline_m - reference) * 1000.0},
        "thresholds": {"max_reprojection_rms_px": 0.5, "max_baseline_delta_mm": 0.5},
        "checks": checks,
        "evidence": {"camchain": str(camchain.resolve()), "camchain_sha256": sha256_file(camchain),
                     "results": str(results.resolve()), "results_sha256": sha256_file(results)},
    }


def _camera_matrix(camera: dict) -> np.ndarray:
    if camera.get("camera_model") != "pinhole" or camera.get("distortion_model") != "radtan":
        raise WorkflowError("留出极线验收当前只接受Kalibr pinhole-radtan模型")
    values = np.asarray(camera.get("intrinsics", []), dtype=float)
    if values.shape != (4,):
        raise WorkflowError("camchain内参形状错误")
    fx, fy, cx, cy = values
    return np.asarray([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=float)


def rectified_epipolar_errors(
    camchain: Path, matched_corners: list[tuple[np.ndarray, np.ndarray]]
) -> np.ndarray:
    """Return |v_left-v_right| after rectifying matched, never-optimized corners."""
    document = yaml.safe_load(Path(camchain).read_text(encoding="utf-8")) or {}
    try:
        cam0, cam1 = document["cam0"], document["cam1"]
        resolution0 = tuple(int(value) for value in cam0["resolution"])
        resolution1 = tuple(int(value) for value in cam1["resolution"])
        transform = np.asarray(cam1["T_cn_cnm1"], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError("camchain缺少留出极线验收所需字段") from exc
    if resolution0 != resolution1 or len(resolution0) != 2:
        raise WorkflowError("双目分辨率不一致")
    if transform.shape != (4, 4):
        raise WorkflowError("cam1.T_cn_cnm1形状错误")
    if not matched_corners:
        raise WorkflowError("留出集没有左右匹配AprilGrid角点")
    left = np.concatenate([np.asarray(pair[0], dtype=float).reshape(-1, 2)
                           for pair in matched_corners], axis=0)
    right = np.concatenate([np.asarray(pair[1], dtype=float).reshape(-1, 2)
                            for pair in matched_corners], axis=0)
    if left.shape != right.shape or left.shape[0] == 0:
        raise WorkflowError("留出集左右角点数量不一致")
    k0, k1 = _camera_matrix(cam0), _camera_matrix(cam1)
    d0 = np.asarray(cam0.get("distortion_coeffs", []), dtype=float)
    d1 = np.asarray(cam1.get("distortion_coeffs", []), dtype=float)
    r1, r2, p1, p2, _, _, _ = cv2.stereoRectify(
        k0, d0, k1, d1, resolution0, transform[:3, :3], transform[:3, 3],
        flags=cv2.CALIB_ZERO_DISPARITY, alpha=0,
    )
    left_rectified = cv2.undistortPoints(left.reshape(-1, 1, 2), k0, d0, R=r1, P=p1)
    right_rectified = cv2.undistortPoints(right.reshape(-1, 1, 2), k1, d1, R=r2, P=p2)
    return np.abs(left_rectified[:, 0, 1] - right_rectified[:, 0, 1])


def _coverage_cells(corners: list[np.ndarray], width: int, height: int) -> list[int]:
    cells = set()
    for array in corners:
        for x, y in np.asarray(array, dtype=float).reshape(-1, 2):
            if not (0.0 <= x < width and 0.0 <= y < height):
                continue
            column = min(2, int(3.0 * x / width))
            row = min(2, int(3.0 * y / height))
            cells.add(row * 3 + column)
    return sorted(cells)


def _aprilgrid_center(detections: dict[int, np.ndarray]) -> np.ndarray | None:
    """Project the known full 6x6 grid center from visible tag-corner correspondences."""
    object_corners = []
    image_corners = []
    pitch = 1.3
    for tag_id, corners in sorted(detections.items()):
        if tag_id < 0 or tag_id >= 36:
            continue
        row, column = divmod(tag_id, 6)
        x0, y0 = column * pitch, row * pitch
        object_corners.extend([
            [x0, y0], [x0 + 1.0, y0],
            [x0 + 1.0, y0 + 1.0], [x0, y0 + 1.0],
        ])
        image_corners.extend(np.asarray(corners, dtype=float).reshape(4, 2).tolist())
    if len(object_corners) < 16:
        return None
    homography, _ = cv2.findHomography(
        np.asarray(object_corners, dtype=float), np.asarray(image_corners, dtype=float), 0
    )
    if homography is None:
        return None
    grid_extent = 6.0 + 5.0 * 0.3
    projected = cv2.perspectiveTransform(
        np.asarray([[[grid_extent / 2.0, grid_extent / 2.0]]], dtype=float), homography
    ).reshape(2)
    return projected.reshape(1, 2) if np.all(np.isfinite(projected)) else None


def heldout_epipolar_report(
    recording: Path, camchain: Path, *, max_sampled_frames: int = 180
) -> dict:
    """Validate a solved camchain on a separately captured synchronized stereo set."""
    unit = Path(recording) / "left_hand"
    left_dir, right_dir = unit / "frames", unit / "frames_right"
    left_by_name = {path.name: path for path in left_dir.glob("*.jpg")}
    right_by_name = {path.name: path for path in right_dir.glob("*.jpg")}
    left_names, right_names = set(left_by_name), set(right_by_name)
    unpaired_left = sorted(left_names - right_names)
    unpaired_right = sorted(right_names - left_names)
    names = sorted(left_names & right_names)
    document = yaml.safe_load(Path(camchain).read_text(encoding="utf-8")) or {}
    try:
        expected_resolution = tuple(int(value) for value in document["cam0"]["resolution"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError("camchain缺少cam0分辨率") from exc
    if len(expected_resolution) != 2:
        raise WorkflowError("camchain的cam0分辨率形状错误")
    if len(names) > max_sampled_frames:
        indices = np.linspace(0, len(names) - 1, max_sampled_frames, dtype=int)
        names = [names[int(index)] for index in indices]

    from aprilgrid import Detector

    detector_left = Detector("t36h11")
    detector_right = Detector("t36h11")
    matched: list[tuple[np.ndarray, np.ndarray]] = []
    left_view_centers: list[np.ndarray] = []
    right_view_centers: list[np.ndarray] = []
    valid_views = 0
    invalid_images = 0
    resolution_mismatches = 0
    detector_errors = 0
    digest = hashlib.sha256()
    digest.update(("left=" + "\n".join(sorted(left_names)) + "\nright="
                   + "\n".join(sorted(right_names))).encode("utf-8"))
    width = height = 0
    for name in names:
        left_bytes, right_bytes = left_by_name[name].read_bytes(), right_by_name[name].read_bytes()
        digest.update(name.encode("utf-8") + b"\0" + left_bytes + b"\0" + right_bytes)
        left_image = cv2.imdecode(np.frombuffer(left_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        right_image = cv2.imdecode(np.frombuffer(right_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        if left_image is None or right_image is None or left_image.shape != right_image.shape:
            invalid_images += 1
            continue
        height, width = left_image.shape[:2]
        if (width, height) != expected_resolution:
            resolution_mismatches += 1
            continue
        try:
            detections_left = {
                int(item.tag_id): np.asarray(item.corners, dtype=float).reshape(4, 2)
                for item in detector_left.detect(left_image)
            }
            detections_right = {
                int(item.tag_id): np.asarray(item.corners, dtype=float).reshape(4, 2)
                for item in detector_right.detect(right_image)
            }
        except (cv2.error, RuntimeError, ValueError):
            detector_errors += 1
            continue
        common_ids = sorted(set(detections_left) & set(detections_right) & set(range(36)))
        if len(common_ids) < 4:
            continue
        for tag_id in common_ids:
            left = detections_left[tag_id]
            right = detections_right[tag_id]
            matched.append((left, right))
        left_center = _aprilgrid_center({tag_id: detections_left[tag_id] for tag_id in common_ids})
        right_center = _aprilgrid_center({tag_id: detections_right[tag_id] for tag_id in common_ids})
        if left_center is not None and right_center is not None:
            valid_views += 1
            left_view_centers.append(left_center)
            right_view_centers.append(right_center)

    errors = (
        rectified_epipolar_errors(camchain, matched)
        if matched else np.asarray([], dtype=float)
    )
    left_cells = _coverage_cells(left_view_centers, width, height)
    right_cells = _coverage_cells(right_view_centers, width, height)
    p95 = float(np.percentile(errors, 95.0)) if errors.size else None
    checks = {
        "synchronized_stereo_frames_present": bool(names),
        "all_left_right_files_paired": not unpaired_left and not unpaired_right,
        "all_sampled_images_valid": invalid_images == 0,
        "all_images_match_camchain_resolution": resolution_mismatches == 0,
        "independent_valid_views_ge_40": valid_views >= 40,
        "left_covers_all_9_cells": left_cells == list(range(9)),
        "right_covers_all_9_cells": right_cells == list(range(9)),
        "epipolar_vertical_p95_le_1px": p95 is not None and p95 <= 1.0,
    }
    return {
        "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "separate_capture_aprilgrid_matched_corner_rectification",
        "metrics": {
            "sampled_synchronized_frames": len(names),
            "unpaired_left_images": len(unpaired_left),
            "unpaired_right_images": len(unpaired_right),
            "invalid_or_mismatched_stereo_images": invalid_images,
            "resolution_mismatches": resolution_mismatches,
            "detector_errors": detector_errors,
            "valid_matched_views": valid_views,
            "matched_tags": len(matched),
            "matched_corners": int(errors.size),
            "epipolar_vertical_mean_px": float(np.mean(errors)) if errors.size else None,
            "epipolar_vertical_p95_px": p95,
            "epipolar_vertical_max_px": float(np.max(errors)) if errors.size else None,
            "left_coverage_cells": left_cells,
            "right_coverage_cells": right_cells,
        },
        "thresholds": {"min_common_tags_per_view": 4, "min_valid_views": 40,
                       "required_target_center_coverage_cells": list(range(9)),
                       "max_epipolar_vertical_p95_px": 1.0},
        "checks": checks,
        "evidence": {"validation_recording": str(Path(recording).resolve()),
                     "sampled_stereo_sha256": digest.hexdigest()},
    }


def solve_camera_imu(bag: Path, camchain: Path, imu_yaml: Path,
                     target: Path, output_dir: Path) -> tuple[Path, Path]:
    executable = require_executable("kalibr_calibrate_imu_camera")
    output_dir.mkdir(parents=True, exist_ok=True)
    local_bag = output_dir / "imucam.bag"
    solved_camchain = output_dir / "imucam-camchain-imucam.yaml"
    results = output_dir / "imucam-results-imucam.txt"
    if bag.resolve() != local_bag.resolve():
        os.symlink(bag.resolve(), local_bag)
    run_command([
        executable, "--bag", str(local_bag), "--cam", str(camchain),
        # This Kalibr build estimates camera-IMU temporal offset by default.
        # It exposes only --no-time-calibration; --time-calibration is invalid.
        "--imu", str(imu_yaml), "--target", str(target),
        "--dont-show-report",
    ], output_dir, output_dir / "kalibr_imucam.log",
        completed_outputs=(solved_camchain, results))
    return solved_camchain, results


def imucam_residuals(path: Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    patterns = {
        "reprojection_mean_px": r"Reprojection error \(cam0\) \[px\]:\s+mean ([0-9.eE+-]+)",
        "gyroscope_mean_rad_s": r"Gyroscope error \(imu0\) \[rad/s\]:\s+mean ([0-9.eE+-]+)",
        "accelerometer_mean_m_s2": r"Accelerometer error \(imu0\) \[m/s\^2\]:\s+mean ([0-9.eE+-]+)",
    }
    values = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text)
        if not match:
            raise WorkflowError(f"无法解析Kalibr残差: {name}")
        values[name] = float(match.group(1))
    return values
