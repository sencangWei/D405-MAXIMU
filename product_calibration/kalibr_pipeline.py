"""Reuse the proven D405 collector/converter and run fail-closed Kalibr solves."""

from __future__ import annotations

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

from .compatible_imu_reader import CompatibleImuReader
from .workflow import WorkflowError, sha256_file
from .imu_stream import NORMALIZED_FORMAT, NORMALIZED_SIZE


DEFAULT_CAPTURE_RUNTIME = Path("/home/robot/ego_vio_humble")
DEFAULT_RSUSB_RUNTIME = Path("/home/robot/D405-MAXIMU")


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


def d405_factory_stereo_baseline(capture_root: Path, rsusb_root: Path,
                                 serial: str) -> float:
    """Read the active 1280x720@30 IR1->IR2 factory extrinsic from the device."""
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
    extrinsic = profiles[1].get_extrinsics_to(profiles[2])
    return float(np.linalg.norm(np.asarray(extrinsic.translation, dtype=float)))


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
    required = [unit / "frames", unit / "frames_right", unit / "camera_ts.csv"]
    if mode == "imucam":
        required.extend([unit / "imu.bin", unit / "imu_ts.csv"])
    if any(not item.exists() for item in required):
        raise WorkflowError("采集不完整，缺少双IR或IMU证据")
    return created[0]


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
        raise WorkflowError(f"缺少{name}；请先安装Kalibr环境，再开始硬件采集")
    return path


def run_command(command: list[str], cwd: Path, log: Path) -> None:
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
    if completed.returncode:
        raise WorkflowError(f"求解命令失败({completed.returncode})，见 {log}")


def solve_stereo(bag: Path, target: Path, output_dir: Path) -> tuple[Path, Path]:
    executable = require_executable("kalibr_calibrate_cameras")
    output_dir.mkdir(parents=True, exist_ok=True)
    local_bag = output_dir / "stereo.bag"
    if bag.resolve() != local_bag.resolve():
        os.symlink(bag.resolve(), local_bag)
    run_command([
        executable, "--bag", str(local_bag),
        "--topics", "/cam0/image_raw", "/cam1/image_raw",
        "--models", "pinhole-radtan", "pinhole-radtan",
        "--target", str(target), "--dont-show-report",
    ], output_dir, output_dir / "kalibr_stereo.log")
    return output_dir / "stereo-camchain.yaml", output_dir / "stereo-results-cam.txt"


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


def solve_camera_imu(bag: Path, camchain: Path, imu_yaml: Path,
                     target: Path, output_dir: Path) -> tuple[Path, Path]:
    executable = require_executable("kalibr_calibrate_imu_camera")
    output_dir.mkdir(parents=True, exist_ok=True)
    local_bag = output_dir / "imucam.bag"
    if bag.resolve() != local_bag.resolve():
        os.symlink(bag.resolve(), local_bag)
    run_command([
        executable, "--bag", str(local_bag), "--cam", str(camchain),
        "--imu", str(imu_yaml), "--target", str(target), "--time-calibration",
        "--dont-show-report",
    ], output_dir, output_dir / "kalibr_imucam.log")
    return output_dir / "imucam-camchain-imucam.yaml", output_dir / "imucam-results-imucam.txt"


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
