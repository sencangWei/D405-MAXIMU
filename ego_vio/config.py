"""配置加载。读取 config/devices.yaml。"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class IMUConfig:
    port: str
    baud: int = 921600
    calibration: str = ""
    protocol: str = "auto"


@dataclass
class CameraConfig:
    serial: str = ""
    width: int = 640
    height: int = 480
    fps: int = 30
    enable_depth: bool = False
    stereo_ir: bool = False
    rgb_preview: bool = False
    auto_exposure: bool = True
    exposure_us: int = 20000
    gain: int = 48
    auto_exposure_limit_us: float = 0.0
    auto_gain_limit: float = 0.0
    cam_latency_ms: float = 0.0   # 相机传输延迟, 非 global_time 时从 fitted_arrival 里减去


@dataclass
class VIOConfig:
    backend: str = "stub"          # stub | openvins_socket | openvins_ros2 | vins_fusion_ros2
    host: str = "auto"             # openvins_socket 用: auto=自动探测 WSL IP; 否则填 IP
    port: int = 12345              # openvins_socket 用发送端口 / openvins_ros2 用队列长度
    cam_topic: str = "/cam0/image_raw"   # openvins_ros2 用
    cam1_topic: str = "/cam1/image_raw"  # openvins_ros2 双目右相机
    stereo: bool = False
    imu_topic: str = "/imu0"             # openvins_ros2 用
    qos_reliable: bool = True            # openvins_ros2 用
    odom_topic: str = "/odometry"         # ROS2 VIO/SLAM 轨迹可视化输入
    imu_level_calibration: str = ""      # 离线/实时VINS共用的固定IMU调平旋转


@dataclass
class UnitConfig:
    name: str
    role: str                      # realtime_vio | record_only
    imu: IMUConfig
    camera: CameraConfig
    vio: VIOConfig = field(default_factory=VIOConfig)


@dataclass
class AppConfig:
    units: List[UnitConfig] = field(default_factory=list)
    out_dir: str = "./recordings"
    jpg_quality: int = 90
    save_depth: bool = False
    imu_bin: bool = True

    def realtime_units(self) -> List[UnitConfig]:
        return [u for u in self.units if u.role == "realtime_vio"]

    def record_units(self) -> List[UnitConfig]:
        """全部要录制的单元(实时单元也录)。"""
        return list(self.units)


def load_config(path: str | Path = None) -> AppConfig:
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "devices.yaml"
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    rec = raw.get("recording", {})
    units = []
    for u in raw.get("units", []):
        imu_raw = u.get("imu", {})
        cam_raw = u.get("camera", {})
        vio_raw = u.get("vio", {})
        units.append(UnitConfig(
            name=u["name"],
            role=u.get("role", "record_only"),
            imu=IMUConfig(
                port=imu_raw.get("port", ""),
                baud=imu_raw.get("baud", 921600),
                protocol=imu_raw.get("protocol", "auto"),
                calibration=(
                    str((path.parent / imu_raw["calibration"]).resolve())
                    if imu_raw.get("calibration")
                    and not Path(imu_raw["calibration"]).is_absolute()
                    else imu_raw.get("calibration", "")
                ),
            ),
            camera=CameraConfig(
                serial=cam_raw.get("serial", ""),
                width=cam_raw.get("width", 640),
                height=cam_raw.get("height", 480),
                fps=cam_raw.get("fps", 30),
                enable_depth=cam_raw.get("enable_depth", False),
                stereo_ir=cam_raw.get("stereo_ir", False),
                rgb_preview=cam_raw.get("rgb_preview", False),
                auto_exposure=cam_raw.get("auto_exposure", True),
                exposure_us=cam_raw.get("exposure_us", 20000),
                gain=cam_raw.get("gain", 48),
                auto_exposure_limit_us=cam_raw.get(
                    "auto_exposure_limit_us", 0.0
                ),
                auto_gain_limit=cam_raw.get("auto_gain_limit", 0.0),
                cam_latency_ms=cam_raw.get("cam_latency_ms", 0.0),
            ),
            vio=VIOConfig(
                backend=vio_raw.get("backend", "stub"),
                host=vio_raw.get("host", "auto"),
                port=vio_raw.get("port", 12345),
                cam_topic=vio_raw.get("cam_topic", "/cam0/image_raw"),
                cam1_topic=vio_raw.get("cam1_topic", "/cam1/image_raw"),
                stereo=vio_raw.get("stereo", False),
                imu_topic=vio_raw.get("imu_topic", "/imu0"),
                qos_reliable=vio_raw.get("qos_reliable", True),
                odom_topic=vio_raw.get("odom_topic", "/odometry"),
                imu_level_calibration=(
                    str((path.parent / vio_raw["imu_level_calibration"]).resolve())
                    if vio_raw.get("imu_level_calibration")
                    and not Path(vio_raw["imu_level_calibration"]).is_absolute()
                    else vio_raw.get("imu_level_calibration", "")
                ),
            ),
        ))

    return AppConfig(
        units=units,
        out_dir=rec.get("out_dir", "./recordings"),
        jpg_quality=rec.get("jpg_quality", 90),
        save_depth=rec.get("save_depth", False),
        imu_bin=rec.get("imu_bin", True),
    )
