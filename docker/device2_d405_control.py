#!/usr/bin/env python3
"""Fail-closed formal D405 controller for UMI device set 02.

The D405/VINS/SLAM implementation comes unchanged from container 1. This
controller owns only device identity, signed runtime calibration selection,
and the second gripper profile. It intentionally contains no D435i mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import yaml


DEVICE_SET_ID = "UMI_DEVICE_02_C48DF736"
CALIB_PRODUCT_ID = "UMI_DEVICE_02_D405_260322279785_20260828"
ROOT = Path("/home/robot/ego_vio_humble")
DATA = Path("/data")
MANIFEST_PATH = Path("/opt/umi/device_manifest.yaml")
BINDING_PATH = DATA / "device_binding.yaml"
ACTIVE_ROOT = DATA / "active_runtime_calibration"
ACTIVE_MANIFEST = ACTIVE_ROOT / "manifest.yaml"
GRIPPER_PROFILE = (
    ROOT / "config/gripper/umi_manual_gripper_c48df736_20260901_shell2_v2.yaml"
)
REQUIRED_RUNTIME_FILES = (
    "vins_config.yaml",
    "left.yaml",
    "right.yaml",
    "device_config.yaml",
)
CANDIDATE_RUNTIME_FILES = (
    "vins_config.yaml",
    "left.yaml",
    "right.yaml",
    "devices.yaml",
    "source_stage_values.yaml",
)

sys.path.insert(0, str(ROOT))


class Blocked(RuntimeError):
    pass


def read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise Blocked(f"缺少文件: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_yaml_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lexical_real(path: Path, *, must_exist: bool, label: str) -> Path:
    """Reject a symlink in the target or any ancestor component."""
    lexical = path.absolute()
    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise Blocked(f"{label}不存在: {lexical}") from exc
    if lexical != resolved:
        raise Blocked(f"{label}路径包含符号链接或非规范分量: {lexical}")
    return resolved


def immutable_manifest() -> dict:
    document = read_yaml(MANIFEST_PATH)
    if document.get("device_set_id") != DEVICE_SET_ID:
        raise Blocked("镜像设备集合身份错误")
    if document.get("software", {}).get("d435i_runtime_policy") != "EXCLUDED":
        raise Blocked("正式D405镜像未排除D435i运行链")
    return document


def expected_port() -> Path:
    basename = immutable_manifest()["hardware"]["stm32_imu_encoder"][
        "usb_by_id_basename"
    ]
    return Path("/dev/serial/by-id") / basename


def require_port() -> Path:
    port = expected_port()
    if not port.exists():
        raise Blocked(f"第二套STM32串口不存在: {port}")
    if not os.access(port, os.R_OK | os.W_OK):
        raise Blocked(f"第二套STM32串口不可读写: {port}")
    return port


def rs_module():
    frozen = ROOT / ".deps/librealsense-rsusb-2.58.2/python"
    sys.path.insert(0, str(frozen))
    import pyrealsense2 as rs

    return rs


def only_d405() -> dict:
    rs = rs_module()
    devices = []
    for device in rs.context().query_devices():
        name = device.get_info(rs.camera_info.name)
        if "D405" not in name.upper():
            continue
        identity = {
            "name": name,
            "serial": device.get_info(rs.camera_info.serial_number),
            "firmware": device.get_info(rs.camera_info.firmware_version),
        }
        for key, info in (
            ("product_id", rs.camera_info.product_id),
            ("physical_port", rs.camera_info.physical_port),
            ("usb_type", rs.camera_info.usb_type_descriptor),
        ):
            try:
                identity[key] = (
                    device.get_info(info) if device.supports(info) else None
                )
            except RuntimeError:
                identity[key] = None
        devices.append(identity)
    if len(devices) != 1:
        raise Blocked(f"必须且只能连接1台第二套D405，当前发现{len(devices)}台")
    return devices[0]


def camera_binding() -> dict:
    document = read_yaml(BINDING_PATH)
    if document.get("schema") != "umi_device_binding_v1":
        raise Blocked("D405绑定manifest格式错误")
    if document.get("device_set_id") != DEVICE_SET_ID:
        raise Blocked("D405绑定不属于第二套设备")
    serial = str(document.get("d405", {}).get("serial", ""))
    if not serial:
        raise Blocked("第二套D405尚未绑定")
    if str(document.get("stm32_usb_by_id", "")) != expected_port().name:
        raise Blocked("D405绑定manifest中的STM32身份不匹配")
    return document


def validate_device_config(path: Path, serial: str, port: Path) -> None:
    document = read_yaml(path)
    units = document.get("units") or []
    if len(units) != 1:
        raise Blocked("device_config必须且只能包含1个设备单元")
    matches = []
    for unit in units:
        camera = unit.get("camera") or {}
        imu = unit.get("imu") or {}
        if str(camera.get("serial", "")) != str(serial):
            continue
        if str(imu.get("port", "")) != str(port):
            continue
        if imu.get("protocol") != "stm32_combined_v1":
            continue
        if unit.get("role") != "realtime_vio":
            continue
        matches.append(unit)
    if len(matches) != 1:
        raise Blocked("device_config未唯一绑定本套D405与STM32 63字节链")


def validate_candidate_runtime(
    source: Path, serial: str, port: Path
) -> dict:
    """Validate a stage-6 candidate without activating or copying it."""
    source = lexical_real(source, must_exist=True, label="候选目录")
    session_root = lexical_real(
        DATA / "calibration_sessions" / CALIB_PRODUCT_ID,
        must_exist=True,
        label="产品标定档案",
    )
    attempts_root = lexical_real(
        session_root / "world_z" / "attempts",
        must_exist=True,
        label="world-Z attempts目录",
    )
    if (
        source.name != "candidate_runtime"
        or source.parent.parent != attempts_root
        or not source.parent.name.startswith("attempt_")
    ):
        raise Blocked("候选目录不属于当前产品档案的world-Z attempt")

    session_path = lexical_real(
        session_root / "session.yaml", must_exist=True, label="产品session清单"
    )
    session = read_yaml(session_path)
    if session.get("product_id") != CALIB_PRODUCT_ID:
        raise Blocked("产品档案身份不匹配")
    stage = (session.get("results") or {}).get("world_z") or {}
    if stage.get("result") != "PASS":
        raise Blocked("产品档案未签发world-Z PASS")
    artifact_rel = Path(str(stage.get("artifact", "")))
    if artifact_rel != Path("world_z/report.yaml"):
        raise Blocked("产品档案world-Z产物路径不符合合同")
    canonical_report = lexical_real(
        session_root / artifact_rel,
        must_exist=True,
        label="产品world-Z正式报告",
    )
    expected_report_sha = str(stage.get("artifact_sha256", ""))
    if (
        canonical_report.parent != (session_root / "world_z").resolve()
        or len(expected_report_sha) != 64
        or sha256(canonical_report) != expected_report_sha
    ):
        raise Blocked("产品档案world-Z产物哈希校验失败")

    manifest_path = lexical_real(
        source / "manifest.yaml", must_exist=True, label="候选manifest"
    )
    manifest = read_yaml(manifest_path)
    if manifest.get("result") != "CANDIDATE":
        raise Blocked("候选运行时manifest不是CANDIDATE")
    if (
        manifest.get("activation")
        != "STAGE6_ONLY_DO_NOT_OVERWRITE_PRODUCT_LIVE"
    ):
        raise Blocked("候选运行时不具备隔离A/B标记")
    files = manifest.get("files") or {}
    for name in CANDIDATE_RUNTIME_FILES:
        path = lexical_real(
            source / name, must_exist=True, label=f"候选文件{name}"
        )
        expected = str(files.get(name, ""))
        if not path.is_file() or len(expected) != 64 or sha256(path) != expected:
            raise Blocked(f"候选运行时文件哈希校验失败: {name}")

    attempt = source.parent
    attempt_report = lexical_real(
        attempt / "report.yaml", must_exist=True, label="候选attempt报告"
    )
    if sha256(attempt_report) != expected_report_sha:
        raise Blocked("候选attempt报告未被产品档案哈希签发")
    report = read_yaml(attempt_report)
    if report.get("result") != "PASS":
        raise Blocked("候选所属world-Z阶段不是PASS")
    if report.get("activation") != "CANDIDATE_ONLY_NOT_INSTALLED":
        raise Blocked("world-Z候选不是未安装隔离状态")
    if report.get("mode") != "live_capture":
        raise Blocked("world-Z候选不是实机采集模式")
    if report.get("release_eligible") is not False:
        raise Blocked("world-Z阶段不得提前标记release_eligible")
    runtime_candidate = report.get("runtime_candidate") or {}
    if Path(str(runtime_candidate.get("directory", ""))).resolve() != source:
        raise Blocked("world-Z报告候选目录与实际目录不一致")
    if Path(str(runtime_candidate.get("manifest", ""))).resolve() != manifest_path:
        raise Blocked("world-Z报告候选manifest路径不一致")
    if Path(str(runtime_candidate.get("vins_config", ""))).resolve() != (
        source / "vins_config.yaml"
    ):
        raise Blocked("world-Z报告候选VINS配置路径不一致")
    if Path(str(runtime_candidate.get("device_config", ""))).resolve() != (
        source / "devices.yaml"
    ):
        raise Blocked("world-Z报告候选设备配置路径不一致")
    declared_manifest_sha = str(
        runtime_candidate.get("manifest_sha256", "")
    )
    if declared_manifest_sha != sha256(manifest_path):
        raise Blocked("world-Z报告与候选manifest哈希不一致")
    if (
        (report.get("selection") or {}).get("selected_model")
        != "IDENTITY_NO_CORRECTION_REQUIRED"
    ):
        raise Blocked("当前运行链仅允许world-Z恒等候选进入A/B")
    rotation = np.asarray(
        (report.get("fit") or {}).get("R_world_z_from_vins_world"),
        dtype=float,
    )
    if rotation.shape != (3, 3) or not np.allclose(
        rotation, np.eye(3), rtol=0.0, atol=1e-12
    ):
        raise Blocked("world-Z报告不是严格恒等旋转")

    td = float(manifest["td_s"])
    if not -0.1 < td < 0.1:
        raise Blocked("候选camera_imu_td_s超出合理范围")
    validate_device_config(source / "devices.yaml", serial, port)
    return {
        "manifest": manifest,
        "source": source,
        "manifest_path": manifest_path,
        "report_path": attempt_report,
        "td_s": td,
        "vins_config": source / "vins_config.yaml",
        "device_config": source / "devices.yaml",
    }


def active_runtime() -> dict:
    binding = camera_binding()
    document = read_yaml(ACTIVE_MANIFEST)
    if document.get("schema") != "umi_device_runtime_calibration_v1":
        raise Blocked("运行时标定manifest格式错误")
    if document.get("result") != "PASS":
        raise Blocked("第二套运行时标定尚未PASS")
    if document.get("device_set_id") != DEVICE_SET_ID:
        raise Blocked("运行时标定不属于第二套设备")
    serial = str(binding["d405"]["serial"])
    if str(document.get("d405_serial", "")) != serial:
        raise Blocked("运行时标定与已绑定D405不匹配")
    files = document.get("files") or {}
    for name in REQUIRED_RUNTIME_FILES:
        path = ACTIVE_ROOT / name
        expected = str(files.get(name, ""))
        if not path.is_file() or len(expected) != 64 or sha256(path) != expected:
            raise Blocked(f"运行时标定文件校验失败: {name}")
    td = float(document["camera_imu_td_s"])
    if not -0.1 < td < 0.1:
        raise Blocked("camera_imu_td_s超出合理范围")
    validate_device_config(
        ACTIVE_ROOT / "device_config.yaml", serial, expected_port()
    )
    return document


def cmd_status(_: argparse.Namespace) -> int:
    manifest = immutable_manifest()
    profile = read_yaml(GRIPPER_PROFILE)
    binding = (
        yaml.safe_load(BINDING_PATH.read_text(encoding="utf-8"))
        if BINDING_PATH.is_file()
        else None
    )
    active = (
        yaml.safe_load(ACTIVE_MANIFEST.read_text(encoding="utf-8"))
        if ACTIVE_MANIFEST.is_file()
        else None
    )
    document = {
        "device_set_id": DEVICE_SET_ID,
        "software_base": manifest["software"],
        "alignment_group_id": manifest["alignment_group_id"],
        "stm32_imu_encoder": {
            "expected_port": str(expected_port()),
            "connected": expected_port().exists(),
        },
        "gripper": {
            "profile_id": profile.get("profile_id"),
            "status": profile.get("quality", {}).get(
                "manual_state_calibration"
            ),
        },
        "d405_binding": binding or "BLOCKED_PENDING_D405_BINDING",
        "camera_imu_runtime": (
            active or "BLOCKED_PENDING_D405_AND_JOINT_CALIBRATION"
        ),
        "release_status": manifest["release"]["status"],
    }
    print(yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
    return 0


def cmd_bind_camera(_: argparse.Namespace) -> int:
    require_port()
    camera = only_d405()
    expected = str(
        immutable_manifest()["hardware"]["d405"].get(
            "expected_serial_after_binding", ""
        )
    )
    if expected and str(camera["serial"]) != expected:
        raise Blocked(
            f"当前D405序列号{camera['serial']}与已枚举候选{expected}不一致"
        )
    if BINDING_PATH.exists():
        current = camera_binding()
        if str(current["d405"]["serial"]) != str(camera["serial"]):
            raise Blocked("第二套已经绑定另一台D405；拒绝静默替换")
        print(f"PASS：第二套D405绑定已存在 {camera['serial']}")
        return 0
    document = {
        "schema": "umi_device_binding_v1",
        "device_set_id": DEVICE_SET_ID,
        "bound_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stm32_usb_by_id": expected_port().name,
        "d405": camera,
    }
    write_yaml_atomic(BINDING_PATH, document)
    print(f"PASS：第二套D405已绑定 {camera['serial']} -> {BINDING_PATH}")
    print("BLOCKED：还需完成D405 factory验收和相机—IMU联合标定")
    return 0


def cmd_install_runtime(args: argparse.Namespace) -> int:
    binding = camera_binding()
    source = Path(args.directory).resolve()
    source_manifest = read_yaml(source / "manifest.yaml")
    if source_manifest.get("schema") != "umi_device_runtime_calibration_v1":
        raise Blocked("导入manifest格式错误")
    if source_manifest.get("result") != "PASS":
        raise Blocked("只允许导入PASS运行时标定")
    if source_manifest.get("device_set_id") != DEVICE_SET_ID:
        raise Blocked("导入标定不属于第二套设备")
    serial = str(binding["d405"]["serial"])
    if str(source_manifest.get("d405_serial", "")) != serial:
        raise Blocked("导入标定的D405与第二套绑定不一致")
    files = source_manifest.get("files") or {}
    for name in REQUIRED_RUNTIME_FILES:
        path = source / name
        if not path.is_file() or sha256(path) != str(files.get(name, "")):
            raise Blocked(f"导入标定文件或哈希错误: {name}")
    td = float(source_manifest["camera_imu_td_s"])
    if not -0.1 < td < 0.1:
        raise Blocked("导入camera_imu_td_s超出合理范围")
    validate_device_config(source / "device_config.yaml", serial, expected_port())
    if ACTIVE_MANIFEST.exists():
        raise Blocked("第二套已有激活标定；拒绝静默覆盖")
    temporary = DATA / ".active_runtime_calibration.tmp"
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary)
    if ACTIVE_ROOT.exists():
        shutil.rmtree(ACTIVE_ROOT)
    os.replace(temporary, ACTIVE_ROOT)
    active_runtime()
    print(f"PASS：第二套运行时标定已激活 {ACTIVE_MANIFEST}")
    return 0


def runtime_environment() -> tuple[dict, dict]:
    profile = active_runtime()
    binding = camera_binding()
    (DATA / "runtime/device2_d405_product_v1/output/pose_graph").mkdir(
        parents=True, exist_ok=True
    )
    env = os.environ.copy()
    env.update(
        {
            "EGO_VIO_D405_SERIAL": str(binding["d405"]["serial"]),
            "EGO_VIO_IMU_BY_ID": str(expected_port()),
            "EGO_VIO_CAMERA_IMU_TD_S": str(profile["camera_imu_td_s"]),
            "EGO_VIO_PRODUCT_LIVE_CONFIG": str(
                ACTIVE_ROOT / "vins_config.yaml"
            ),
            "EGO_VIO_PRODUCT_LIVE_DEVICE_CONFIG": str(
                ACTIVE_ROOT / "device_config.yaml"
            ),
            "EGO_VIO_PRODUCT_CALIBRATION_LABEL": (
                f"{DEVICE_SET_ID} signed D405 runtime calibration"
            ),
        }
    )
    return env, binding


def candidate_runtime_environment(source: Path) -> tuple[dict, dict, dict]:
    binding = camera_binding()
    serial = str(binding["d405"]["serial"])
    candidate = validate_candidate_runtime(source, serial, expected_port())
    device_document = read_yaml(candidate["device_config"])
    units = device_document.get("units") or []
    imu_lead_guard_ms = float(units[0].get("vio", {}).get("imu_lead_guard_ms", 10.0))
    env = os.environ.copy()
    env.update(
        {
            "EGO_VIO_D405_SERIAL": serial,
            "EGO_VIO_IMU_BY_ID": str(expected_port()),
            "EGO_VIO_CAMERA_IMU_TD_S": str(candidate["td_s"]),
            "EGO_VIO_PRODUCT_LIVE_CONFIG": str(candidate["vins_config"]),
            "EGO_VIO_PRODUCT_LIVE_DEVICE_CONFIG": str(
                candidate["device_config"]
            ),
            "EGO_VIO_PRODUCT_CALIBRATION_LABEL": (
                f"{DEVICE_SET_ID} isolated stage-6 candidate"
            ),
            "EGO_VIO_IMU_LEAD_GUARD_MS": str(imu_lead_guard_ms),
            # The estimator still records its complete raw/corrected outputs.
            # This delay only hides the known sub-mm pre-ZUPT display segment.
            "EGO_VIO_VIEWER_INITIAL_SETTLE_S": "1.2",
            # This host uses Rerun's CPU software rasterizer.  Lowering only
            # the observer priority prevents it from starving the 15 Hz VINS.
            "EGO_VIO_VIEWER_NICE": "10",
        }
    )
    return env, binding, candidate


def direct_child(
    path: Path, root: Path, *, must_exist: bool, label: str
) -> Path:
    """Require a non-symlink direct child of a real, non-symlink data root."""
    root = lexical_real(root, must_exist=True, label=f"{label}根目录")
    path = lexical_real(path, must_exist=must_exist, label=label)
    if path.parent != root or path.name in ("", ".", ".."):
        raise Blocked(f"{label}必须是{root}的直接子目录")
    if must_exist and not path.is_dir():
        raise Blocked(f"{label}不存在: {path}")
    if not must_exist and path.exists():
        raise Blocked(f"{label}已存在，拒绝覆盖: {path}")
    return path


def candidate_capture_binding(
    session: Path, candidate: dict, serial: str
) -> dict:
    db3_files = sorted(session.glob("*.db3"))
    if len(db3_files) != 1:
        raise Blocked("候选A/B会话必须且只能包含1个真实DB3文件")
    replay_inputs = {
        db3_files[0].name: lexical_real(
            db3_files[0], must_exist=True, label="候选DB3"
        ),
        "d405_frames.csv": lexical_real(
            session / "d405_frames.csv",
            must_exist=True,
            label="候选相机时间文件",
        ),
        "external_imu/imu.bin": lexical_real(
            session / "external_imu/imu.bin",
            must_exist=True,
            label="候选IMU文件",
        ),
    }
    print("正在生成候选A/B全部回放输入SHA-256，请勿修改会话……")
    document = {
        "schema": "umi_candidate_ab_capture_v1",
        "result": "PASS",
        "device_set_id": DEVICE_SET_ID,
        "calibration_product_id": CALIB_PRODUCT_ID,
        "d405_serial": serial,
        "camera_imu_td_s": candidate["td_s"],
        "candidate_source": str(candidate["source"]),
        "candidate_manifest_sha256": sha256(candidate["manifest_path"]),
        "world_z_report_sha256": sha256(candidate["report_path"]),
        "candidate_config_sha256": sha256(candidate["vins_config"]),
        "candidate_device_config_sha256": sha256(candidate["device_config"]),
        "replay_inputs": {
            relative: sha256(path) for relative, path in replay_inputs.items()
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_yaml_atomic(session / "candidate_ab_capture.yaml", document)
    return document


def verify_candidate_capture_binding(
    session: Path, candidate: dict, serial: str
) -> dict:
    document = read_yaml(session / "candidate_ab_capture.yaml")
    checks = {
        "schema": document.get("schema") == "umi_candidate_ab_capture_v1",
        "result": document.get("result") == "PASS",
        "device_set_id": document.get("device_set_id") == DEVICE_SET_ID,
        "product_id": document.get("calibration_product_id") == CALIB_PRODUCT_ID,
        "serial": str(document.get("d405_serial", "")) == serial,
        "candidate_source": Path(
            str(document.get("candidate_source", ""))
        ).resolve() == candidate["source"],
        "candidate_manifest": str(
            document.get("candidate_manifest_sha256", "")
        ) == sha256(candidate["manifest_path"]),
        "world_z_report": str(document.get("world_z_report_sha256", ""))
        == sha256(candidate["report_path"]),
        "candidate_config": str(
            document.get("candidate_config_sha256", "")
        ) == sha256(candidate["vins_config"]),
        "candidate_device_config": str(
            document.get("candidate_device_config_sha256", "")
        ) == sha256(candidate["device_config"]),
    }
    failed = [name for name, accepted in checks.items() if not accepted]
    if failed:
        raise Blocked(f"候选A/B采集绑定校验失败: {','.join(failed)}")
    recorded_inputs = document.get("replay_inputs") or {}
    db3_names = [name for name in recorded_inputs if name.endswith(".db3")]
    expected_names = set(db3_names) | {
        "d405_frames.csv",
        "external_imu/imu.bin",
    }
    if len(db3_names) != 1 or set(recorded_inputs) != expected_names:
        raise Blocked("候选A/B回放输入清单不完整")
    actual_db3 = sorted(session.glob("*.db3"))
    if len(actual_db3) != 1:
        raise Blocked("候选A/B会话当前必须且只能包含1个DB3")
    actual_db3_path = lexical_real(
        actual_db3[0], must_exist=True, label="候选实际DB3"
    )
    if actual_db3_path.name != db3_names[0]:
        raise Blocked("候选A/B实际DB3与绑定清单不一致")
    print("正在复核候选A/B全部回放输入SHA-256，确保两次求解输入完全相同……")
    for relative, expected in recorded_inputs.items():
        relative_path = Path(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise Blocked("候选A/B回放输入路径非法")
        path = lexical_real(
            session / relative_path,
            must_exist=True,
            label=f"候选回放输入{relative}",
        )
        if not path.is_file() or sha256(path) != str(expected):
            raise Blocked(f"候选A/B回放输入已改变: {relative}")
    return document


def cmd_imu_check(args: argparse.Namespace) -> int:
    from ego_vio.gripper import ManualGripperCalibration, ManualGripperTracker
    from ego_vio.imu.imu_reader import ImuReader

    port = require_port()
    tracker = ManualGripperTracker(ManualGripperCalibration.load(GRIPPER_PROFILE))
    samples = []
    valid = 0
    pair_gaps = []
    last_gripper_state = None

    def on_sample(sample):
        nonlocal valid, last_gripper_state
        is_valid = bool(sample.flags & 0x02) and not bool(sample.flags & 0x0C)
        raw_count = int(sample.encoder_response or 0) & 0x3FFF
        last_gripper_state = tracker.update(
            raw_count * 360.0 / 16384.0, encoder_valid=is_valid
        )
        samples.append(sample)
        valid += int(is_valid)
        if sample.encoder_sensor_gap_us is not None:
            pair_gaps.append(float(sample.encoder_sensor_gap_us))

    reader = ImuReader(
        port=str(port),
        baud=921600,
        protocol="stm32_combined_v1",
        warmup_frames=400,
        on_sample=on_sample,
        name="device2_d405_hil",
    )
    if not reader.start():
        raise Blocked("第二套串口打开失败")
    try:
        deadline = time.monotonic() + 5.0
        while not reader.warmup_stats() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not reader.warmup_stats():
            raise Blocked("STM32预热未在5秒内完成")
        time.sleep(args.duration)
    finally:
        reader.stop()
    stats = reader.stats_since_warmup()
    p95 = float(np.percentile(pair_gaps, 95)) if pair_gaps else None
    zero_fields = (
        "frames_bad",
        "resyncs",
        "dropped_frames",
        "counter_resets",
        "counter_stalls",
        "sequence_gaps",
        "invalid_imu_flags",
        "queue_overflow_flags",
        "serial_errors",
        "serial_reconnects",
    )
    accepted = (
        stats.get("protocol") == "stm32_combined_v1"
        and 395.0 <= float(stats.get("rate_hz", 0.0)) <= 405.0
        and len(samples) >= args.duration * 380.0
        and valid == len(samples)
        and p95 is not None
        and 0.0 <= p95 <= 250.0
        and tracker.calibration.gap_direction_mode == "independent"
        and last_gripper_state is not None
        and last_gripper_state.estimated_no_load_gap_mm is not None
        and 0.0
        <= float(last_gripper_state.estimated_no_load_gap_mm)
        <= tracker.calibration.fully_open_gap_mm
        and all(int(stats.get(key, -1)) == 0 for key in zero_fields)
    )
    report = {
        "schema": "umi_device2_d405_imu_encoder_hil_v1",
        "device_set_id": DEVICE_SET_ID,
        "result": "PASS" if accepted else "FAIL",
        "duration_s": args.duration,
        "samples": len(samples),
        "encoder_valid_samples": valid,
        "encoder_imu_delta_us_p95": p95,
        "transport": stats,
        "gripper_profile_id": tracker.calibration.profile_id,
        "gripper": {
            "gap_direction_mode": tracker.calibration.gap_direction_mode,
            "angle_deg": (
                float(last_gripper_state.angle_deg)
                if last_gripper_state is not None
                else None
            ),
            "estimated_no_load_gap_mm": (
                float(last_gripper_state.estimated_no_load_gap_mm)
                if last_gripper_state is not None
                and last_gripper_state.estimated_no_load_gap_mm is not None
                else None
            ),
            "closure_ratio": (
                float(last_gripper_state.closure_ratio)
                if last_gripper_state is not None
                and last_gripper_state.closure_ratio is not None
                else None
            ),
            "travel_direction_diagnostic": (
                last_gripper_state.direction
                if last_gripper_state is not None
                else None
            ),
        },
    }
    evidence = DATA / "hil_evidence" / time.strftime(
        "%Y%m%d_%H%M%S_device2_d405_imu_encoder.json"
    )
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"证据: {evidence}")
    return 0 if accepted else 1


def cmd_capture(args: argparse.Namespace) -> int:
    require_port()
    env, binding = runtime_environment()
    command = [
        str(ROOT / "capture_d405_720p_rgb_stereo_ir_rsusb.sh"),
        "--serial",
        str(binding["d405"]["serial"]),
        "--imu-port",
        str(expected_port()),
        "--duration",
        str(args.duration),
        "--capture-mode",
        "rgb_stereo_ir",
        "--output-root",
        "/data/recordings",
    ]
    if not args.preview:
        command.append("--no-preview")
    return subprocess.run(command, cwd=ROOT, env=env).returncode


def cmd_candidate_capture(args: argparse.Namespace) -> int:
    require_port()
    source = Path(args.candidate).absolute()
    env, binding, candidate = candidate_runtime_environment(source)
    output_root = DATA / "candidate_ab" / "recordings"
    output_root = lexical_real(
        output_root, must_exist=True, label="候选A/B录制根目录"
    )
    before = {path.name for path in output_root.iterdir() if path.is_dir()}
    command = [
        str(ROOT / "capture_d405_720p_rgb_stereo_ir_rsusb.sh"),
        "--serial",
        str(binding["d405"]["serial"]),
        "--imu-port",
        str(expected_port()),
        "--duration",
        str(args.duration),
        "--capture-mode",
        "rgb_stereo_ir",
        "--output-root",
        str(output_root),
    ]
    if not args.preview:
        command.append("--no-preview")
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode != 0:
        return result.returncode
    after = {path.name for path in output_root.iterdir() if path.is_dir()}
    created = sorted(after - before)
    if len(created) != 1:
        raise Blocked(f"候选采集后无法唯一确定新会话: {created}")
    session = direct_child(
        output_root / created[0], output_root, must_exist=True, label="候选会话"
    )
    candidate_capture_binding(
        session, candidate, str(binding["d405"]["serial"])
    )
    print(f"PASS：候选A/B会话已哈希绑定 {session.name}")
    return 0


def cmd_realtime(_: argparse.Namespace) -> int:
    require_port()
    env, _ = runtime_environment()
    env["EGO_VIO_RUN_DIR"] = (
        f"/data/logs/realtime_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    return run_product_live(env)


def run_product_live(env: dict) -> int:
    """Run the formal realtime chain and treat terminal Ctrl-C as a clean stop."""
    try:
        return subprocess.run(
            [str(ROOT / "run_vins_realtime.sh"), "product-live"],
            cwd=ROOT,
            env=env,
        ).returncode
    except KeyboardInterrupt:
        # The child receives the same terminal SIGINT and performs its own
        # process-group cleanup. Do not print a Python traceback for a normal
        # operator stop.
        print("实时链已由操作员停止。", file=sys.stderr)
        return 130


def cmd_candidate_realtime(args: argparse.Namespace) -> int:
    require_port()
    source = Path(args.candidate).absolute()
    env, binding, candidate = candidate_runtime_environment(source)
    logs_root = lexical_real(
        DATA / "candidate_ab" / "logs",
        must_exist=True,
        label="候选实时日志根目录",
    )
    run_dir = direct_child(
        logs_root / f"realtime_{time.strftime('%Y%m%d_%H%M%S')}",
        logs_root,
        must_exist=False,
        label="候选实时日志",
    )
    run_dir.mkdir()
    binding_document = {
        "schema": "umi_candidate_realtime_v1",
        "result": "STARTING",
        "device_set_id": DEVICE_SET_ID,
        "d405_serial": str(binding["d405"]["serial"]),
        "candidate_source": str(candidate["source"]),
        "candidate_manifest_sha256": sha256(candidate["manifest_path"]),
        "world_z_report_sha256": sha256(candidate["report_path"]),
        "vins_config_sha256": sha256(candidate["vins_config"]),
        "device_config_sha256": sha256(candidate["device_config"]),
        "camera_imu_td_s": candidate["td_s"],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_yaml_atomic(run_dir / "candidate_realtime_binding.yaml", binding_document)
    env["EGO_VIO_RUN_DIR"] = str(run_dir)
    return run_candidate_live(
        env=env,
        run_dir=run_dir,
        binding_document=binding_document,
    )


def run_candidate_live(
    *, env: dict, run_dir: Path, binding_document: dict
) -> int:
    """Run the isolated candidate entry and always finalize provenance."""
    return_code = 130
    terminal_result = "INTERRUPTED"
    try:
        result = subprocess.run(
            [str(ROOT / "run_vins_realtime_candidate.sh"), "product-live"],
            cwd=ROOT,
            env=env,
        )
        return_code = result.returncode
        terminal_result = "EXITED"
    except KeyboardInterrupt:
        # The child receives the same terminal SIGINT and performs its own
        # process-group cleanup. Preserve an honest terminal state as well.
        terminal_result = "STOPPED_BY_USER"
    finally:
        binding_document["result"] = terminal_result
        binding_document["exit_code"] = return_code
        binding_document["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_yaml_atomic(
            run_dir / "candidate_realtime_binding.yaml", binding_document
        )
    return return_code


def cmd_candidate_realtime_record(args: argparse.Namespace) -> int:
    if not 5.0 <= args.duration <= 3600.0:
        raise Blocked("实时同源录制时长必须在5到3600秒之间")
    require_port()
    source = Path(args.candidate).absolute()
    env, binding, candidate = candidate_runtime_environment(source)
    output_root = lexical_real(
        DATA / "candidate_ab" / "recordings",
        must_exist=True,
        label="候选实时同源录制根目录",
    )
    logs_root = lexical_real(
        DATA / "candidate_ab" / "logs",
        must_exist=True,
        label="候选实时同源日志根目录",
    )
    run_dir = direct_child(
        logs_root / f"realtime_record_{time.strftime('%Y%m%d_%H%M%S')}",
        logs_root,
        must_exist=False,
        label="候选实时同源日志",
    )
    run_dir.mkdir()
    before = {path.name for path in output_root.iterdir() if path.is_dir()}
    env.update(
        {
            "EGO_VIO_RUN_DIR": str(run_dir),
            "EGO_VIO_RECORD_DURATION_S": str(args.duration),
            "EGO_VIO_RECORD_OUTPUT_ROOT": str(output_root),
        }
    )
    binding_document = {
        "schema": "umi_candidate_realtime_record_v1",
        "result": "STARTING",
        "device_set_id": DEVICE_SET_ID,
        "d405_serial": str(binding["d405"]["serial"]),
        "duration_s": args.duration,
        "candidate_source": str(candidate["source"]),
        "candidate_manifest_sha256": sha256(candidate["manifest_path"]),
        "world_z_report_sha256": sha256(candidate["report_path"]),
        "vins_config_sha256": sha256(candidate["vins_config"]),
        "device_config_sha256": sha256(candidate["device_config"]),
        "camera_imu_td_s": candidate["td_s"],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_yaml_atomic(run_dir / "candidate_realtime_binding.yaml", binding_document)
    result_code = run_candidate_live(
        env=env,
        run_dir=run_dir,
        binding_document=binding_document,
    )
    if result_code != 0:
        return result_code
    after = {path.name for path in output_root.iterdir() if path.is_dir()}
    created = sorted(after - before)
    if len(created) != 1:
        raise Blocked(f"实时同源录制后无法唯一确定新会话: {created}")
    session = direct_child(
        output_root / created[0], output_root, must_exist=True, label="实时同源会话"
    )
    capture_document = candidate_capture_binding(
        session, candidate, str(binding["d405"]["serial"])
    )
    capture_document["acquisition_mode"] = "realtime_vins_same_source_full_raw"
    capture_document["realtime_log_directory"] = str(run_dir)
    write_yaml_atomic(session / "candidate_ab_capture.yaml", capture_document)
    print(f"PASS：实时轨迹与完整原始数据来自同一传感器流 {session.name}")
    print(f"后处理命令会话名：{session.name}")
    return 0


def cmd_postprocess(args: argparse.Namespace) -> int:
    env, _ = runtime_environment()
    session = direct_child(
        Path(args.session),
        DATA / "recordings",
        must_exist=True,
        label="正式后处理输入",
    )
    output = direct_child(
        Path(args.output),
        DATA / "slam_results",
        must_exist=False,
        label="正式后处理输出",
    )
    return subprocess.run(
        [
            "/usr/local/bin/umi-run-slam-postprocess-configurable",
            str(session),
            str(output),
            str(ACTIVE_ROOT / "vins_config.yaml"),
        ],
        cwd=ROOT,
        env=env,
    ).returncode


def cmd_candidate_postprocess(args: argparse.Namespace) -> int:
    source = Path(args.candidate).absolute()
    env, binding, candidate = candidate_runtime_environment(source)
    session = direct_child(
        Path(args.session),
        DATA / "candidate_ab" / "recordings",
        must_exist=True,
        label="候选A/B输入",
    )
    output = direct_child(
        Path(args.output),
        DATA / "candidate_ab" / "slam_results",
        must_exist=False,
        label="候选A/B输出",
    )
    capture_binding = verify_candidate_capture_binding(
        session, candidate, str(binding["d405"]["serial"])
    )
    if args.variant == "baseline":
        config = ROOT / "config/product_live_stm32/vins_config.yaml"
    else:
        config = candidate["vins_config"]
    result = subprocess.run(
        [
            "/usr/local/bin/umi-run-slam-postprocess-configurable",
            str(session),
            str(output),
            str(config),
        ],
        cwd=ROOT,
        env=env,
    )
    if result.returncode != 0:
        return result.returncode
    # The product hash manifest is KEY=VALUE, not YAML; retain its immutable
    # file digest and the output-specific provenance instead of parsing it.
    provenance = {
        "schema": "umi_candidate_ab_result_v1",
        "result": "PASS",
        "variant": args.variant,
        "session": session.name,
        "capture_binding_sha256": sha256(
            session / "candidate_ab_capture.yaml"
        ),
        "replay_inputs": capture_binding.get("replay_inputs"),
        "config": str(config),
        "config_sha256": sha256(config),
        "candidate_manifest_sha256": sha256(candidate["manifest_path"]),
        "world_z_report_sha256": sha256(candidate["report_path"]),
        "product_binary_hash_manifest_sha256": sha256(
            ROOT / ".product_live_build/product_live_hashes.env"
        ),
        "executed_artifacts": {
            "vins": sha256(
                ROOT
                / ".product_live_build/loop_ws/build/vins_fusion_ros2/vins_fusion_ros2_node"
            ),
            "vins_library": sha256(
                ROOT
                / ".product_live_build/loop_ws/build/vins_fusion_ros2/vins/libvins_lib.so"
            ),
            "loop": sha256(
                ROOT
                / ".product_live_build/loop_ws/build/vins_fusion_ros2/loop_fusion/loop_fusion_node"
            ),
            "replay": sha256(
                ROOT
                / ".product_live_build/loop_ws/build/vins_fusion_ros2/db3_replay_cpp"
            ),
        },
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_yaml_atomic(output / "candidate_ab_result.yaml", provenance)
    print(f"PASS：候选A/B {args.variant} 结果已绑定 {output}")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        description="UMI第二套正式D405控制器（容器一算法基线）"
    )
    sub = top.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("bind-camera")
    install = sub.add_parser("install-runtime-calibration")
    install.add_argument("directory")
    check = sub.add_parser("imu-check")
    check.add_argument("--duration", type=float, default=10.0)
    capture = sub.add_parser("capture")
    capture.add_argument("duration", type=float)
    capture.add_argument("--preview", action="store_true")
    candidate_capture = sub.add_parser("candidate-capture")
    candidate_capture.add_argument("candidate")
    candidate_capture.add_argument("duration", type=float)
    candidate_capture.add_argument("--preview", action="store_true")
    sub.add_parser("realtime")
    candidate_realtime = sub.add_parser("candidate-realtime")
    candidate_realtime.add_argument("candidate")
    candidate_realtime_record = sub.add_parser("candidate-realtime-record")
    candidate_realtime_record.add_argument("candidate")
    candidate_realtime_record.add_argument("duration", type=float)
    post = sub.add_parser("postprocess")
    post.add_argument("session")
    post.add_argument("output")
    candidate_post = sub.add_parser("candidate-postprocess")
    candidate_post.add_argument("candidate")
    candidate_post.add_argument("session")
    candidate_post.add_argument("output")
    candidate_post.add_argument(
        "--variant", choices=("baseline", "candidate"), required=True
    )
    return top


def main() -> int:
    args = parser().parse_args()
    if getattr(args, "duration", 1.0) <= 0:
        raise SystemExit("duration必须大于0")
    commands = {
        "status": cmd_status,
        "bind-camera": cmd_bind_camera,
        "install-runtime-calibration": cmd_install_runtime,
        "imu-check": cmd_imu_check,
        "capture": cmd_capture,
        "candidate-capture": cmd_candidate_capture,
        "realtime": cmd_realtime,
        "candidate-realtime": cmd_candidate_realtime,
        "candidate-realtime-record": cmd_candidate_realtime_record,
        "postprocess": cmd_postprocess,
        "candidate-postprocess": cmd_candidate_postprocess,
    }
    try:
        return commands[args.command](args)
    except (
        Blocked,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"BLOCKED：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
