"""主运行时: 串起采集 → 录制 → VIO → 可视化。

启动顺序:
  1. load_config() → 三套单元配置
  2. Recorder (三路录制)
  3. RerunVisualizer (双手可视化)
  4. 每个单元: ImuReader + RealSenseCapture (+ 双手加 VIO 后端)
  5. 回调串联:
       相机帧 → 录制 + VIO.feed_camera + 可视化图像
       IMU样本 → 录制 + VIO.feed_imu + 可视化位姿
  6. 主循环: 定期打印统计 + watchdog

Ctrl-C 优雅停止。
"""

from __future__ import annotations
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .config import AppConfig, UnitConfig, load_config
from .imu.imu_reader import ImuReader, ImuSample
from .imu.calibration import IMUCalibration
from .camera.realsense_capture import RealSenseCapture, CameraFrame
from .recorder.recorder import Recorder
from .vio.base import VIOBackend
from .vio.stub import StubVIO


@dataclass
class UnitRuntime:
    cfg: UnitConfig
    imu: Optional[ImuReader] = None
    cam: Optional[RealSenseCapture] = None
    vio: Optional[VIOBackend] = None          # 数据桥接(发 ROS2)
    vio_viz: Optional[VIOBackend] = None      # 即时可视化(StubVIO, 不等 OpenVINS)
    imu_calibration: Optional[IMUCalibration] = None


class Runtime:
    def __init__(self, config: AppConfig, session_name: Optional[str] = None):
        self.cfg = config
        self.units: Dict[str, UnitRuntime] = {}
        self.recorder: Optional[Recorder] = None
        self.viz = None
        self._running = False
        self._stop_evt = threading.Event()

        # 录制目录
        if session_name is None:
            session_name = time.strftime("session_%Y%m%d_%H%M%S")
        self.out_dir = Path(config.out_dir) / session_name

        # OpenVINS 位姿回传接收器(仅当有 openvins_socket 单元时, 即 Windows-WSL 旧模式)
        self._odom_receiver: Optional[object] = None
        self._epoch_offset = time.time() - time.monotonic()

    # ---------- 组装 ----------
    def _make_vio(self, unit: UnitConfig) -> Optional[VIOBackend]:
        if unit.role != "realtime_vio":
            return None
        if unit.vio.backend == "openvins_socket":
            from .vio.openvins_bridge import OpenVINSSocketBridge
            host = unit.vio.host
            if host == "auto":
                host = self._resolve_wsl_host()
                if host is None:
                    print(f"[{unit.name}] 无法自动探测 WSL IP, 请手动设置 vio.host")
                    return None
            return OpenVINSSocketBridge(
                name=f"ov_{unit.name}", host=host, port=unit.vio.port,
                epoch_offset=self._epoch_offset,
                cam_latency_ms=unit.camera.cam_latency_ms,
            )
        if unit.vio.backend in ("openvins_ros2", "orbslam3_ros2"):
            from .vio.openvins_ros2_bridge import OpenVINSROS2Bridge
            return OpenVINSROS2Bridge(
                name=(
                    f"orb3_{unit.name}"
                    if unit.vio.backend == "orbslam3_ros2"
                    else f"ov_{unit.name}"
                ),
                cam_topic=unit.vio.cam_topic,
                cam1_topic=unit.vio.cam1_topic,
                imu_topic=unit.vio.imu_topic,
                stereo=unit.vio.stereo,
                queue_size=unit.vio.port if unit.vio.port else 100,
                qos_reliable=unit.vio.qos_reliable,
                epoch_offset=self._epoch_offset,
                cam_latency_ms=unit.camera.cam_latency_ms,
            )
        # 默认 stub
        return StubVIO(name=f"stub_{unit.name}")

    @staticmethod
    def _resolve_wsl_host() -> Optional[str]:
        """Windows 上通过 wsl hostname -I 获取 WSL IP。"""
        import subprocess
        try:
            out = subprocess.check_output(["wsl", "hostname", "-I"], timeout=5, stderr=subprocess.STDOUT)
            text = out.decode("utf-8", errors="ignore").strip()
            return text.split()[0]
        except Exception as e:
            print(f"[runtime] 探测 WSL IP 失败: {e}")
            return None

    def setup(self, record: bool = True, visualize: bool = True):
        # 录制器(全部单元都录)
        if record:
            self.recorder = Recorder(
                [u.name for u in self.cfg.record_units()],
                self.out_dir,
                jpg_quality=self.cfg.jpg_quality,
                save_depth=self.cfg.save_depth,
                imu_bin=self.cfg.imu_bin,
            )
            self.recorder.start()

        # 可视化(双手实时单元)
        if visualize:
            viz_names = [u.name for u in self.cfg.realtime_units()]
            if viz_names:
                from .visualizer.rerun_viz import RerunVisualizer
                # 保留完整运动轨迹，历史姿态轴按弧长抽样，适合现场监督和回看。
                self.viz = RerunVisualizer(
                    viz_names,
                    purge_sec=None,
                    max_points=6000,
                    epoch_offset=self._epoch_offset,
                )
                # 如果有 openvins_socket 单元, 启动位姿回传接收器
                ov_socket_units = [u.name for u in self.cfg.realtime_units() if u.vio.backend == "openvins_socket"]
                if ov_socket_units:
                    from .vio.openvins_odom_receiver import OpenVINSOdomReceiver
                    target_unit = ov_socket_units[0]
                    self._odom_receiver = OpenVINSOdomReceiver(
                        on_pose=lambda p, name=target_unit: self._on_vio_pose(name, p),
                        host="0.0.0.0", port=12346,
                        epoch_offset=self._epoch_offset,
                    )
                # openvins_ros2 单元: 直接从 ROS2 订阅 /ov_msckf/odomimu
                ov_ros2_units = [u.name for u in self.cfg.realtime_units() if u.vio.backend == "openvins_ros2"]
                if ov_ros2_units:
                    self._start_ros2_odom_subscriber(
                        ov_ros2_units, "/ov_msckf/odomimu", "OpenVINS"
                    )
                orb_ros2_units = [
                    u.name
                    for u in self.cfg.realtime_units()
                    if u.vio.backend == "orbslam3_ros2"
                ]
                if orb_ros2_units:
                    self._start_ros2_odom_subscriber(
                        orb_ros2_units, "/orbslam3/odom", "ORB-SLAM3"
                    )

        # 各单元
        for u_cfg in self.cfg.units:
            ur = UnitRuntime(cfg=u_cfg)

            if u_cfg.imu.calibration:
                ur.imu_calibration = IMUCalibration.load(u_cfg.imu.calibration)
                print(
                    f"[{u_cfg.name}] IMU 标定已加载: "
                    f"{ur.imu_calibration.calibration_id}"
                )

            # IMU
            ur.imu = ImuReader(
                port=u_cfg.imu.port, baud=u_cfg.imu.baud,
                on_sample=lambda s, name=u_cfg.name: self._on_imu(name, s),
                name=u_cfg.name,
            )
            # 相机
            ur.cam = RealSenseCapture(
                serial=u_cfg.camera.serial,
                width=u_cfg.camera.width, height=u_cfg.camera.height,
                fps=u_cfg.camera.fps, enable_depth=u_cfg.camera.enable_depth,
                stereo_ir=u_cfg.camera.stereo_ir,
                auto_exposure=u_cfg.camera.auto_exposure,
                exposure_us=u_cfg.camera.exposure_us,
                gain=u_cfg.camera.gain,
                on_frame=lambda f, name=u_cfg.name: self._on_camera(name, f),
                name=u_cfg.name,
            )
            # VIO(仅实时单元)
            ur.vio = self._make_vio(u_cfg)
            # ROS2 模式只显示 OpenVINS 的真实里程计结果。初始化前若用
            # StubVIO 做纯 IMU 积分，必然漂移，而且会被误认为视觉惯性
            # 结果；因此初始化完成前保持轨迹为空。

            self.units[u_cfg.name] = ur

    def _start_ros2_odom_subscriber(
        self, unit_names: list, topic: str, source_label: str
    ):
        """订阅 ROS2 odom, 将真实 VIO/SLAM 位姿直接送入可视化。"""
        import rclpy
        from rclpy.node import Node
        from nav_msgs.msg import Odometry
        import numpy as np
        from .vio.base import Pose

        if not rclpy.ok():
            rclpy.init(args=None)

        class _OdomNode(Node):
            def __init__(self_, runtime):
                super().__init__("ego_vio_odom_bridge")
                self_.runtime = runtime
                self_.create_subscription(Odometry, topic, self_.on_odom, 10)

            def on_odom(self_, msg):
                stamp = msg.header.stamp
                ts = stamp.sec + stamp.nanosec * 1e-9
                p = msg.pose.pose.position
                q = msg.pose.pose.orientation
                pose = Pose(
                    ts=ts - self_.runtime._epoch_offset,
                    t=np.array([p.x, p.y, p.z]),
                    q=np.array([q.x, q.y, q.z, q.w]),
                )
                for name in unit_names:
                    self_.runtime._on_vio_pose(name, pose, source_label)

        node = _OdomNode(self)
        spin_thread = threading.Thread(target=lambda: rclpy.spin(node), name="odom-spin", daemon=True)
        spin_thread.start()
        print(f"[runtime] ROS2 odom subscriber started → {topic} ({source_label})")

    _vio_pose_count = 0
    _vio_active = False
    def _on_vio_pose(self, unit: str, pose, source_label: str = "VIO"):
        self._vio_pose_count += 1
        if not self._vio_active:
            self._vio_active = True
            print(f"[runtime] {source_label} 已初始化, 切换到真实位姿")
            # 清掉 StubVIO 的轨迹, 切换到 OpenVINS
            for name in self.units:
                ur = self.units[name]
                if ur.vio_viz:
                    ur.vio_viz = None  # 停掉 StubVIO
                if self.viz:
                    self.viz.clear_unit(name)
        if self.viz:
            if self._vio_pose_count <= 3 or self._vio_pose_count % 100 == 0:
                print(f"[runtime] odom pose #{self._vio_pose_count}: t={pose.t} q={pose.q[:2]}...")
            self.viz.log_pose(unit, pose)

    # ---------- 回调 ----------
    def _on_imu(self, unit: str, s: ImuSample):
        ur = self.units.get(unit)
        if not ur:
            return
        if self.recorder:
            self.recorder.get(unit).put_imu(s)
        if ur.imu_calibration:
            s = ur.imu_calibration.apply(s)
        pose = ur.vio.feed_imu(s) if ur.vio else None
        if self.viz:
            self.viz.log_imu(unit, s)
            # Only the explicitly selected stub backend may draw its own
            # IMU-only pose. OpenVINS modes remain empty until real odometry
            # arrives and can never silently fall back to this trajectory.
            if pose is not None and ur.cfg.vio.backend == "stub":
                self.viz.log_pose(unit, pose)
        # 仅供明确配置的辅助后端使用；openvins_ros2 不创建 vio_viz。
        if ur.vio_viz and self.viz:
            pose = ur.vio_viz.feed_imu(s)
            if pose:
                self.viz.log_pose(unit, pose)

    def _on_camera(self, unit: str, f: CameraFrame):
        ur = self.units.get(unit)
        if not ur:
            return
        ts_wall = time.time()
        if self.recorder:
            rec = self.recorder.get(unit)
            if f.color is not None:
                rec.put_color(
                    f.frame_idx,
                    f.frame_number,
                    f.ts,
                    f.ts_arrival,
                    f.color,
                    ts_wall,
                )
            if f.depth is not None:
                rec.put_depth(f.frame_idx, f.ts, f.depth, ts_wall)
        if ur.vio:
            ur.vio.feed_camera(f)
        if self.viz and f.color is not None:
            self.viz.log_image(unit, f.color, f.ts)

    # ---------- 启停 ----------
    def start(self):
        self._running = True
        # RealSense 初始化会短暂占用 Python 解释器。先完成相机初始化，
        # 再清空并打开 IMU 串口，避免 IMU 积压样本污染在线时钟拟合。
        for name, ur in self.units.items():
            if ur.cam:
                ok = ur.cam.start()
                print(f"[{name}] 相机 {'OK' if ok else 'FAIL'} serial={ur.cfg.camera.serial!r}")
        for name, ur in self.units.items():
            if ur.imu:
                ok = ur.imu.start()
                print(f"[{name}] IMU {'OK' if ok else 'FAIL'} @ {ur.cfg.imu.port}")

    def stop(self):
        self._running = False
        self._stop_evt.set()
        print("\n[runtime] 停止中...")
        for name, ur in self.units.items():
            if ur.cam:
                ur.cam.stop()
            if ur.imu:
                ur.imu.stop()
            if ur.vio:
                ur.vio.close()
        if self.recorder:
            self.recorder.stop()
        if self._odom_receiver:
            self._odom_receiver.close()
        print("[runtime] 已停止。")

    def run(self, stat_interval: float = 3.0):
        def handler(signum, frame):
            self._stop_evt.set()
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

        print(f"[runtime] 录制目录: {self.out_dir}")
        print("[runtime] Ctrl-C 停止。")
        last_stat = time.monotonic()
        while not self._stop_evt.is_set():
            time.sleep(0.2)
            now = time.monotonic()
            if now - last_stat >= stat_interval:
                last_stat = now
                stats = {}
                for name, ur in self.units.items():
                    s = {"imu_ok": ur.imu.frames_ok if ur.imu else 0}
                    if ur.imu:
                        s.update(ur.imu.stats())
                    if ur.cam:
                        cs = ur.cam.stats()
                        s["cam_fps"] = round(cs["rate_hz"], 1)
                        s["cam_jit_ms"] = round(cs["dt_jitter_ms"], 1)
                    if ur.vio and hasattr(ur.vio, "transport_stats"):
                        s.update(ur.vio.transport_stats())
                    stats[name] = s
                self._print_stats(stats)
                if self.viz:
                    self.viz.log_stats(stats, now)

    def _print_stats(self, stats: dict):
        print("---- stats ----")
        for name, s in stats.items():
            line = f"[{name}] "
            if "rate_hz" in s:
                line += (
                    f"IMU {s['rate_hz']:.0f}Hz ok={s.get('imu_ok',0)} "
                    f"drop={s.get('dropped_frames',0)} "
                    f"reset={s.get('counter_resets',0)} "
                    f"stall={s.get('counter_stalls',0)} "
                    f"reconn={s.get('serial_reconnects',0)} "
                    f"bad={s.get('frames_bad',0)} "
                    f"jit={s.get('dt_jitter_ms',0):.1f}ms"
                )
            if "cam_fps" in s:
                line += f" | CAM {s['cam_fps']}fps jit={s['cam_jit_ms']}ms"
            if "ros_cam_pub" in s:
                line += (
                    f" | ROS pub imu={s['ros_imu_pub']} cam={s['ros_cam_pub']}"
                    f" drop={s['ros_cam_drop']} q={s['ros_cam_queue']}"
                )
            print(line)


def run_default(config_path=None, record=True, visualize=True, session=None, backend=None):
    cfg = load_config(config_path)
    # 命令行可强制指定后端
    if backend:
        for u in cfg.units:
            if u.role == "realtime_vio":
                u.vio.backend = backend
    rt = Runtime(cfg, session_name=session)
    rt.setup(record=record, visualize=visualize)
    rt.start()
    try:
        rt.run()
    finally:
        rt.stop()
