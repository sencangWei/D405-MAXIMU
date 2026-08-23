"""Build an isolated product-live configuration from passed calibration stages."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import yaml

from .workflow import WorkflowError, sha256_file


# Fixed product-family mapping between Kalibr's physical IMU frame and the
# body frame used by the validated VINS publisher.  It is intentionally kept
# separate from every unit's T_cam_imu.  The value reconstructs the selected
# 2026-08-22 product-live configuration from its four-run Kalibr consensus.
RUNTIME_BODY_FROM_KALIBR_IMU = np.asarray([
    [0.999881837221, -0.011523450028, -0.010174561122, 0.002637189180],
    [-0.010464566468, -0.025392287741, -0.999622791142, 0.000043755853],
    [0.011260747896, 0.999611145307, -0.025509875226, -0.000368624857],
    [0.0, 0.0, 0.0, 1.0],
], dtype=float)


def vins_body_t_camera(t_camera_imu: list[list[float]]) -> np.ndarray:
    transform = np.asarray(t_camera_imu, dtype=float)
    if transform.shape != (4, 4) or not np.all(np.isfinite(transform)):
        raise WorkflowError("camera-IMU候选外参必须是有限4x4矩阵")
    rotation = transform[:3, :3]
    if (
        not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-5)
        or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-5)
    ):
        raise WorkflowError("camera-IMU候选外参旋转不是合法SO(3)")
    return RUNTIME_BODY_FROM_KALIBR_IMU @ np.linalg.inv(transform)


def _replace_scalar(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^({re.escape(key)}:\s*).*$")
    updated, count = pattern.subn(rf"\g<1>{value}", text)
    if count != 1:
        raise WorkflowError(f"VINS模板字段{key}数量异常: {count}")
    return updated


def _replace_opencv_matrix(text: str, key: str, matrix: np.ndarray) -> str:
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines)
              if line.startswith(f"{key}: !!opencv-matrix")]
    if len(starts) != 1:
        raise WorkflowError(f"VINS模板矩阵{key}数量异常: {len(starts)}")
    start = starts[0]
    data_start = next(
        (index for index in range(start + 1, len(lines))
         if lines[index].lstrip().startswith("data: [")),
        None,
    )
    if data_start is None:
        raise WorkflowError(f"VINS模板矩阵{key}缺少data")
    end = next(
        (index for index in range(data_start, len(lines)) if "]" in lines[index]),
        None,
    )
    if end is None:
        raise WorkflowError(f"VINS模板矩阵{key}未闭合")
    values = matrix.reshape(-1)
    rows = []
    for row in range(4):
        rendered = ", ".join(f"{value:.12f}" for value in values[row * 4:(row + 1) * 4])
        prefix = "   data: [ " if row == 0 else "           "
        suffix = " ]" if row == 3 else ","
        rows.append(prefix + rendered + suffix)
    lines[data_start:end + 1] = rows
    return "\n".join(lines) + "\n"


def write_factory_camera_model(camera: dict, output: Path) -> Path:
    values = [camera.get(key) for key in ("fx", "fy", "cx", "cy")]
    if not all(value is not None and np.isfinite(float(value)) for value in values):
        raise WorkflowError("factory相机内参不完整")
    output = Path(output)
    output.write_text(
        "%YAML:1.0\n---\n"
        "model_type: PINHOLE\n"
        "camera_name: camera\n"
        "image_width: 1280\n"
        "image_height: 720\n"
        "distortion_parameters:\n"
        "   k1: 0.0\n   k2: 0.0\n   p1: 0.0\n   p2: 0.0\n"
        "projection_parameters:\n"
        f"   fx: {float(camera['fx']):.12f}\n"
        f"   fy: {float(camera['fy']):.12f}\n"
        f"   cx: {float(camera['cx']):.12f}\n"
        f"   cy: {float(camera['cy']):.12f}\n",
        encoding="utf-8",
    )
    return output


def write_vins_candidate(template: Path, output: Path, *, candidate: dict,
                         output_root: Path) -> Path:
    text = Path(template).read_text(encoding="utf-8")
    td = float(candidate["td_s"])
    body0 = vins_body_t_camera(candidate["T_cam0_imu"])
    body1 = vins_body_t_camera(candidate["T_cam1_imu"])
    text = _replace_scalar(text, "td", f"{td:.9f}")
    text = _replace_scalar(text, "output_path", f'"{output_root}/"')
    text = _replace_scalar(
        text, "pose_graph_save_path", f'"{output_root}/pose_graph/"'
    )
    text = _replace_opencv_matrix(text, "body_T_cam0", body0)
    text = _replace_opencv_matrix(text, "body_T_cam1", body1)
    output = Path(output)
    output.write_text(text, encoding="utf-8")
    return output


def write_device_candidate(template: Path, output: Path, *, serial: str,
                           port: str) -> Path:
    document = yaml.safe_load(Path(template).read_text(encoding="utf-8")) or {}
    units = document.get("units", [])
    if len(units) != 1:
        raise WorkflowError("product-live设备模板必须且只能包含一个VIO单元")
    unit = units[0]
    unit["camera"]["serial"] = str(serial)
    unit["imu"]["port"] = str(port)
    unit["imu"]["protocol"] = "stm32_combined_v1"
    unit["imu"]["calibration"] = ""
    output = Path(output)
    output.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return output


def build_stage6_runtime(*, destination: Path, runtime_root: Path,
                         identity: dict, stereo: dict,
                         camera_imu: dict) -> dict:
    """Create a self-contained, non-installed runtime candidate for stage 6."""
    if (
        camera_imu.get("result") != "PASS"
        or camera_imu.get("release_eligible") is not True
        or "candidate" not in camera_imu
    ):
        raise WorkflowError("第5步没有通过正式采集健康门的可发布共识候选")
    if (
        stereo.get("result") != "PASS"
        or stereo.get("release_eligible") is not True
        or stereo.get("runtime_policy") != "USE_INTEL_FACTORY_RECTIFIED_INTRINSICS"
    ):
        raise WorkflowError("第4步不是通过正式留出验收的Intel factory参数报告")
    serial = str(identity.get("devices", {}).get("d405", {}).get("serial", ""))
    port = str(identity.get("devices", {}).get("imu_port", ""))
    if not serial or not port.startswith("/dev/serial/by-id/"):
        raise WorkflowError("identity缺少D405序列号或稳定IMU端口")
    metrics = stereo.get("metrics", {})
    cam0, cam1 = metrics.get("cam0_intrinsics", {}), metrics.get("cam1_intrinsics", {})
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "output/pose_graph").mkdir(parents=True)
    left = write_factory_camera_model(cam0, destination / "left.yaml")
    right = write_factory_camera_model(cam1, destination / "right.yaml")
    sources = destination / "source_stage_values.yaml"
    sources.write_text(
        yaml.safe_dump({
            "identity": {"devices": identity.get("devices", {})},
            "d405_factory": {
                "method": stereo.get("method"),
                "runtime_policy": stereo.get("runtime_policy"),
                "metrics": stereo.get("metrics", {}),
            },
            "imu_runtime_policy": {
                "accelerometer_matrix": "NOT_APPLIED",
                "device_calibration": "",
                "reason": "held_out_same_replay_ab_rejected_stage3_candidate",
                "vins_bias": "estimated_online",
            },
            "camera_imu": {
                "method": camera_imu.get("method"),
                "candidate": camera_imu.get("candidate"),
                "candidate_repeatability": camera_imu.get("candidate_repeatability"),
            },
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    runtime_root = Path(runtime_root)
    vins = write_vins_candidate(
        runtime_root / "config/product_live_stm32/vins_config.yaml",
        destination / "vins_config.yaml",
        candidate=camera_imu["candidate"],
        output_root=destination / "output",
    )
    devices = write_device_candidate(
        runtime_root / "config/devices_product_live_stm32.yaml",
        destination / "devices.yaml",
        serial=serial,
        port=port,
    )
    files = (left, right, sources, vins, devices)
    manifest = {
        "format_version": 1,
        "result": "CANDIDATE",
        "activation": "STAGE6_ONLY_DO_NOT_OVERWRITE_PRODUCT_LIVE",
        "runtime_body_mapping": RUNTIME_BODY_FROM_KALIBR_IMU.tolist(),
        "td_s": float(camera_imu["candidate"]["td_s"]),
        "files": {path.name: sha256_file(path) for path in files},
    }
    manifest_path = destination / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "directory": str(destination.resolve()),
        "vins_config": str(vins.resolve()),
        "device_config": str(devices.resolve()),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
    }
