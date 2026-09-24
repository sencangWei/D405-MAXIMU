#!/usr/bin/env python3
"""Fail-closed preflight for Docker2 SLAM evaluation with Lighthouse ground truth."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKER2_CONFIG = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
    "formal_runtime_calibration/vins_config.yaml"
)
DOCKER2_CONFIG_SHA256 = "87338c341a7bfea71194528fc2c735a55ee85d1d1558141f096dd00a97059331"
FROZEN_LIGHTHOUSE_CONFIG = (
    ROOT
    / "reports/lighthouse_recalibration_world_v12_20260924_consensus/"
    "libsurvive_config_frozen_v12.json"
)
FROZEN_LIGHTHOUSE_CONFIG_SHA256 = (
    "63bd7d886bf6f8b46735a5f38047af8902d1f79b60a6feb57b3e3233e5bc6e99"
)
TRACKER_SERIAL = "LHR-A2A59C7D"
D405_SERIAL = "260322279785"
IMU_PORT = Path(
    "/dev/serial/by-id/"
    "usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_"
    "c48df736b505f011adda8d1272aab386-if00-port0"
)
MIN_DISK_FREE_BYTES = 20 * 1024**3
CAMERA_BYTES_PER_SECOND = 1280 * 720 * 4 * 30
STAGING_HEADROOM = 1.15
EXPECTED_SLAM_BINARY_SHA256 = {
    str((ROOT / "build/vins_fusion_ros2/vins_fusion_ros2_node").resolve()):
        "5af167654b949e9944d93dc972d431f46b7a4fde4ffa976439ad1f5dcd950a3a",
    str((ROOT / "build/vins_fusion_ros2/loop_fusion/loop_fusion_node").resolve()):
        "8c48398c6ac6f17d8753652cc1ec11507f1bfbaa0081c74dd00ddb743209464d",
    str((ROOT / "build/vins_fusion_ros2/db3_replay_cpp").resolve()):
        "41b380578fe28dd6c9a60b85df371925265dd564bcb060d3e0502d43ae283798",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add(checks: list[dict], name: str, passed: bool, details: object) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "details": details})


def validate_docker2_config(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    required_lines = {
        "estimate_td: 0",
        "td: -0.009109323",
        "max_cnt: 400",
        "min_dist: 20",
        "max_num_iterations: 8",
        "keyframe_parallax: 10.0",
    }
    missing = sorted(line for line in required_lines if line not in text.splitlines())
    digest = sha256(path)
    return {
        "passed": not missing and digest == DOCKER2_CONFIG_SHA256,
        "path": str(path.resolve()),
        "sha256": digest,
        "expected_sha256": DOCKER2_CONFIG_SHA256,
        "missing_contract_lines": missing,
        "replay_imu_shift_ms": 0,
    }


def executable_report(path: Path) -> dict[str, object]:
    resolved = path.resolve(strict=False)
    digest = sha256(resolved) if resolved.is_file() else None
    expected = EXPECTED_SLAM_BINARY_SHA256.get(str(resolved))
    return {
        "path": str(path),
        "resolved": str(resolved),
        "exists": resolved.is_file(),
        "executable": os.access(resolved, os.X_OK),
        "sha256": digest,
        "expected_sha256": expected,
        "matches_expected": expected is None or digest == expected,
    }


def check_d405_serial() -> dict[str, object]:
    try:
        import pyrealsense2 as rs

        serials = [
            device.get_info(rs.camera_info.serial_number)
            for device in rs.context().query_devices()
        ]
        return {"passed": D405_SERIAL in serials, "expected": D405_SERIAL, "found": serials}
    except Exception as exc:
        return {"passed": False, "expected": D405_SERIAL, "error": str(exc)}


def check_imu_port() -> dict[str, object]:
    try:
        resolved = IMU_PORT.resolve(strict=True)
        mode = resolved.stat().st_mode
        return {
            "passed": stat.S_ISCHR(mode) and os.access(resolved, os.R_OK),
            "path": str(IMU_PORT),
            "resolved": str(resolved),
            "readable": os.access(resolved, os.R_OK),
        }
    except OSError as exc:
        return {"passed": False, "path": str(IMU_PORT), "error": str(exc)}


def run_tracker_check(output: Path, duration_s: float) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output.with_name(output.stem + "_tracker.csv")
    report_path = output.with_name(output.stem + "_tracker.json")
    runtime_config = output.with_name(output.stem + "_libsurvive_runtime.json")
    shutil.copyfile(FROZEN_LIGHTHOUSE_CONFIG, runtime_config)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/lighthouse_reference_check.py"),
            "record",
            "--duration",
            str(duration_s),
            "--warmup",
            "1",
            "--output",
            str(csv_path),
            "--report",
            str(report_path),
            "--config",
            str(runtime_config),
            "--lighthouse-gen",
            "2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not report_path.is_file():
        return {
            "passed": False,
            "returncode": completed.returncode,
            "stderr": completed.stderr[-2000:],
        }
    report = json.loads(report_path.read_text(encoding="utf-8"))
    serials = report.get("metrics", {}).get("serials", [])
    return {
        "passed": report.get("status") == "PASS" and serials == [TRACKER_SERIAL],
        "expected_serial": TRACKER_SERIAL,
        "csv": str(csv_path.resolve()),
        "report": str(report_path.resolve()),
        "runtime_config": str(runtime_config.resolve()),
        "runtime_config_sha256": sha256(runtime_config),
        "measurement": report,
    }


def run_preflight(mode: str, output: Path, capture_duration_s: float,
                  tracker_duration_s: float) -> dict[str, object]:
    checks: list[dict] = []
    config = validate_docker2_config(DOCKER2_CONFIG)
    add(checks, "docker2_formal_config_frozen", bool(config["passed"]), config)

    camera_calibrations = []
    for name in ("left.yaml", "right.yaml"):
        path = DOCKER2_CONFIG.parent / name
        camera_calibrations.append(
            {"path": str(path), "exists": path.is_file(), "sha256": sha256(path) if path.is_file() else None}
        )
    add(
        checks,
        "docker2_camera_calibrations",
        all(item["exists"] for item in camera_calibrations),
        camera_calibrations,
    )

    frozen_exists = FROZEN_LIGHTHOUSE_CONFIG.is_file()
    frozen_hash = sha256(FROZEN_LIGHTHOUSE_CONFIG) if frozen_exists else None
    add(
        checks,
        "lighthouse_coordinate_frame_frozen",
        frozen_exists and frozen_hash == FROZEN_LIGHTHOUSE_CONFIG_SHA256,
        {
            "frozen": str(FROZEN_LIGHTHOUSE_CONFIG),
            "frozen_sha256": frozen_hash,
            "expected_sha256": FROZEN_LIGHTHOUSE_CONFIG_SHA256,
            "runtime_policy": (
                "copy frozen config per Tracker process; pass with -c; "
                "never use mutable ~/.config/libsurvive/config.json"
            ),
        },
    )

    executables = [
        Path("/home/robot/.local/bin/vive_pose_stream"),
        ROOT / "capture_d405_720p_rgb_stereo_ir.sh",
        ROOT / "install/vins_fusion_ros2/lib/vins_fusion_ros2/vins_fusion_ros2_node",
        ROOT / "install/vins_fusion_ros2/lib/vins_fusion_ros2/loop_fusion_node",
        ROOT / "install/vins_fusion_ros2/lib/vins_fusion_ros2/db3_replay_cpp",
    ]
    executable_reports = [executable_report(path) for path in executables]
    add(
        checks,
        "required_executables",
        all(
            item["exists"] and item["executable"] and item["matches_expected"]
            for item in executable_reports
        ),
        executable_reports,
    )

    imports = {}
    for module in ("cv2", "numpy", "scipy", "yaml", "rclpy", "pyrealsense2"):
        try:
            imported = importlib.import_module(module)
            imports[module] = {"ok": True, "version": getattr(imported, "__version__", None)}
        except Exception as exc:
            imports[module] = {"ok": False, "error": str(exc)}
    add(checks, "python_runtime", all(item["ok"] for item in imports.values()), imports)

    lsusb = subprocess.run(["lsusb"], check=False, capture_output=True, text=True)
    add(
        checks,
        "watchman_dongle",
        lsusb.returncode == 0 and "28de:2101" in lsusb.stdout.lower(),
        {"usb_id": "28de:2101", "found": "28de:2101" in lsusb.stdout.lower()},
    )
    tracker_check = run_tracker_check(output, tracker_duration_s)
    add(
        checks,
        "lighthouse_live_pose",
        bool(tracker_check["passed"]),
        tracker_check,
    )

    disk = shutil.disk_usage(ROOT)
    required_staging = int(CAMERA_BYTES_PER_SECOND * capture_duration_s * STAGING_HEADROOM)
    shm = shutil.disk_usage("/dev/shm")
    storage = {
        "capture_duration_s": capture_duration_s,
        "disk_free_bytes": disk.free,
        "disk_required_bytes": MIN_DISK_FREE_BYTES,
        "shm_free_bytes": shm.free,
        "shm_required_bytes": required_staging,
    }
    add(
        checks,
        "capture_storage",
        disk.free >= MIN_DISK_FREE_BYTES and shm.free >= required_staging,
        storage,
    )

    stale_names = ("vins_fusion_ros2_node", "loop_fusion_node", "db3_replay_cpp")
    stale = {}
    for name in stale_names:
        result = subprocess.run(["pgrep", "-x", name], check=False, capture_output=True, text=True)
        stale[name] = result.stdout.split()
    add(checks, "no_stale_slam_processes", not any(stale.values()), stale)

    deferred = []
    if mode == "full":
        d405 = check_d405_serial()
        add(checks, "d405_identity", bool(d405["passed"]), d405)
        imu = check_imu_port()
        add(checks, "external_imu_identity", bool(imu["passed"]), imu)
    else:
        deferred = [
            f"D405 serial {D405_SERIAL} presence and profiles",
            f"external IMU device {IMU_PORT}",
            "Tracker-to-UMI rigid mounting",
            "Tracker-to-VINS-body extrinsic and residual time-offset calibration",
        ]

    failures = [check["name"] for check in checks if check["status"] != "PASS"]
    report = {
        "schema": "lighthouse_umi_preflight_v1",
        "status": "PASS" if not failures else "FAIL",
        "mode": mode,
        "created_at": datetime.now().astimezone().isoformat(),
        "checks": checks,
        "failures": failures,
        "deferred_until_umi_arrives": deferred,
        "safety_mode": "bench_no_motion",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preumi", "full"), default="preumi")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-duration-s", type=float, default=60.0)
    parser.add_argument("--tracker-duration-s", type=float, default=3.0)
    args = parser.parse_args()
    report = run_preflight(
        args.mode, args.output, args.capture_duration_s, args.tracker_duration_s
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
