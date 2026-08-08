#!/usr/bin/env python3
"""Kalibr 标定数据采集脚本。

把 120s 采集拆成 6 个阶段, 默认按固定时长推进, 实时显示质量指标作为参考。
如需阻塞式质检, 加 --strict。

用法:
  python scripts/collect_calib_data.py --phase-secs 20
  python scripts/collect_calib_data.py --phase-secs 20 --strict
"""
import argparse
import csv
import math
import struct
import sys
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import load_config
from ego_vio.imu.imu_reader import ImuReader
from ego_vio.camera.realsense_capture import RealSenseCapture, CameraFrame


IMU_PACK_FMT = "<dI7f"
IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)
PREVIEW_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

# 标定板固定墙上, 手持相机+IMU 移动。
# 六个阶段: (名称, 最小持续时间, 检查指标)
# PnP 测相机-标定板相对运动; IMU 激励确保陀螺/加计有足够信号。
PHASES = [
    ("平移: 沿相机/IMU X轴横向移动(保持板子在画面内)", 10, {"tags_min": 6, "tx": 0.07, "imu_excite": 0.02}),
    ("平移: 沿相机/IMU Y轴上下移动", 10, {"tags_min": 6, "ty": 0.07, "imu_excite": 0.02}),
    ("平移: 沿相机/IMU Z轴前后移动", 10, {"tags_min": 6, "tz": 0.08, "imu_excite": 0.02}),
    ("旋转: 握相机缓慢俯仰(pitch)", 10, {"tags_min": 4, "pitch": 12.0, "imu_excite": 0.05}),
    ("旋转: 握相机缓慢横滚(roll)",  10, {"tags_min": 4, "roll": 12.0, "imu_excite": 0.05}),
    ("旋转: 握相机缓慢偏航(yaw)",   10, {"tags_min": 4, "yaw": 15.0, "imu_excite": 0.05}),
]


def load_aprilgrid_config(path: Path) -> dict:
    """读取 Kalibr 风格 AprilGrid yaml。"""
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return {
        "tagCols": int(cfg["tagCols"]),
        "tagRows": int(cfg["tagRows"]),
        "tagSize": float(cfg["tagSize"]),
        "tagSpacing": float(cfg["tagSpacing"]),
    }


class AprilGridPoseTracker:
    """基于 AprilGrid PnP 的相机位姿跟踪。

    直接在相机-标定板坐标系下测量运动, 比 IMU 积分准确得多。
    板子坐标系: X 水平向右, Y 竖直向下, Z 垂直板面向外(指向相机)。
    """

    def __init__(self, detector, grid_cfg: dict, K: np.ndarray, D: np.ndarray,
                 track_camera_motion: bool = False):
        self.detector = detector
        self.tag_cols = grid_cfg["tagCols"]
        self.tag_rows = grid_cfg["tagRows"]
        self.tag_size = grid_cfg["tagSize"]
        self.tag_spacing = grid_cfg["tagSpacing"]
        self.K = K
        self.D = D
        self.track_camera_motion = track_camera_motion
        self._init_rvec = None
        self._init_tvec = None
        self._last_rvec = None
        self._last_tvec = None
        self.max_t = np.zeros(3)
        self.min_t = np.zeros(3)
        self.max_position_t = np.zeros(3)
        self.span_t = np.zeros(3)
        self.current_t = np.zeros(3)
        self.path_length = 0.0
        self.path_gaps = 0
        self._smoothed_t = None
        self.max_r = np.zeros(3)

    def reset(self):
        self._init_rvec = None
        self._init_tvec = None
        self._last_rvec = None
        self._last_tvec = None
        self.max_t = np.zeros(3)
        self.min_t = np.zeros(3)
        self.max_position_t = np.zeros(3)
        self.span_t = np.zeros(3)
        self.current_t = np.zeros(3)
        self.path_length = 0.0
        self.path_gaps = 0
        self._smoothed_t = None
        self.max_r = np.zeros(3)

    def _tag_corners_3d(self, tag_id: int):
        """返回某个 tag 的 4 个角点在板子坐标系下的坐标。"""
        row = tag_id // self.tag_cols
        col = tag_id % self.tag_cols
        pitch = self.tag_size * (1.0 + self.tag_spacing)
        x0 = col * pitch
        y0 = row * pitch
        s = self.tag_size
        return np.array([
            [x0, y0, 0.0],
            [x0 + s, y0, 0.0],
            [x0 + s, y0 + s, 0.0],
            [x0, y0 + s, 0.0],
        ], dtype=np.float32)

    def feed_detections(self, img: np.ndarray, dets) -> bool:
        """直接传入已经检测到的 AprilGrid 结果, 避免重复检测。"""
        if self.detector is None or len(dets) < 4:
            return False
        obj_pts = []
        img_pts = []
        for d in dets:
            tag_id = int(d.tag_id)
            if tag_id < 0 or tag_id >= self.tag_cols * self.tag_rows:
                continue
            corners = getattr(d, "corners", None)
            if corners is None:
                continue
            corners = np.asarray(corners, dtype=np.float32)
            if corners.size == 8:
                corners = corners.reshape(4, 2)
            if corners.shape != (4, 2):
                continue
            obj_pts.append(self._tag_corners_3d(tag_id))
            img_pts.append(corners)
        if len(obj_pts) < 4:
            return False

        obj_pts = np.vstack(obj_pts)
        img_pts = np.vstack(img_pts)
        ok, rvec, tvec = cv2.solvePnP(
            obj_pts, img_pts, self.K, self.D,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok:
            return False

        projected, _ = cv2.projectPoints(obj_pts, rvec, tvec, self.K, self.D)
        reprojection_rms = float(np.sqrt(np.mean(np.sum(
            (projected.reshape(-1, 2) - img_pts) ** 2, axis=1
        ))))
        if reprojection_rms > 2.0:
            return False

        self._last_rvec = rvec
        self._last_tvec = tvec
        if self._init_rvec is None:
            self._init_rvec = rvec.copy()
            self._init_tvec = tvec.copy()
            self._smoothed_t = np.zeros(3)
            return True

        R_init, _ = cv2.Rodrigues(self._init_rvec)
        R_last, _ = cv2.Rodrigues(rvec)
        if self.track_camera_motion:
            # solvePnP 返回标定板到相机的变换 X_c = R_cb*X_b + t_cb。
            # 联合标定时标定板固定，应比较相机中心在标定板坐标系中的
            # 位置 C_b = -R_cb^T*t_cb；直接相减 t_cb 会把原地旋转误判
            # 成大幅平移。
            init_position = -R_init.T @ self._init_tvec
            last_position = -R_last.T @ tvec
            # 把标定板坐标系中的位移转换到“阶段开始时的相机光学坐标系”。
            # 这样 X/Y/Z 分别对应画面横向、画面纵向、镜头前后，不再受
            # AprilGrid 的摆放/旋转方向影响。
            t_rel = (R_init @ (last_position - init_position)).flatten()
            R_rel = R_last.T @ R_init
        else:
            t_rel = (tvec - self._init_tvec).flatten()
            R_rel = R_last @ R_init.T
        roll, pitch, yaw = self._R_to_euler(R_rel)

        self.current_t = t_rel
        self.max_t = np.maximum(self.max_t, np.abs(t_rel))
        self.min_t = np.minimum(self.min_t, t_rel)
        self.max_position_t = np.maximum(self.max_position_t, t_rel)
        self.span_t = self.max_position_t - self.min_t

        # 累计路径只用于人机反馈，不参与标定质量门控。EMA 抑制亚毫米级
        # PnP 抖动；若标定板遮挡后重新出现并产生大跳变，则重新锚定，
        # 不把不可观测的间隔误算成路径。
        if self._smoothed_t is None:
            self._smoothed_t = t_rel.copy()
        else:
            smoothed_t = 0.15 * t_rel + 0.85 * self._smoothed_t
            step = float(np.linalg.norm(smoothed_t - self._smoothed_t))
            if step <= 0.08:
                # 静止实测的平滑后单帧抖动低于约 0.15 mm；扣除该
                # 噪声底，避免静止数分钟也累计出虚假的长路径。
                self.path_length += math.sqrt(max(step * step - 0.00015 ** 2, 0.0))
            else:
                self.path_gaps += 1
            self._smoothed_t = smoothed_t
        self.max_r = np.maximum(self.max_r, np.abs(np.degrees([roll, pitch, yaw])))
        return True

    def feed_image(self, img: np.ndarray) -> bool:
        """处理一帧图像, 更新位姿和运动极值。返回是否成功解出位姿。"""
        if self.detector is None:
            return False
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            dets = self.detector.detect(gray)
        except Exception:
            return False
        return self.feed_detections(img, dets)

    @staticmethod
    def _R_to_euler(R: np.ndarray):
        """XYZ 欧拉角 (roll, pitch, yaw), 单位弧度。"""
        roll = math.atan2(R[2, 1], R[2, 2])
        pitch = math.asin(np.clip(-R[2, 0], -1.0, 1.0))
        yaw = math.atan2(R[1, 0], R[0, 0])
        return roll, pitch, yaw


class StageQuality:
    """单阶段质量评估(基于 AprilGrid PnP, IMU 仅作备用)。"""

    def __init__(self, pose_tracker: Optional[AprilGridPoseTracker] = None):
        self.frames = 0
        self.detected = 0
        self.tag_counts = []
        self.pose_tracker = pose_tracker
        self.pose_ok_frames = 0
        self.last_image = None
        self.last_tag_count = 0
        self.last_detections = []
        self.last_pose_ok = False

        # IMU 激励检测
        self.q = np.array([0.0, 0.0, 0.0, 1.0])
        self.last_ts = None
        self.angle_max = np.zeros(3)
        self.accel_std = 0.0    # 三轴中最大的标准差(激励指标, g)
        self.max_accel_std = 0.0
        self.accel_samples = []

    def reset(self):
        self.__init__(self.pose_tracker)
        if self.pose_tracker is not None:
            self.pose_tracker.reset()

    def feed_imu(self, s):
        w = np.radians([s.gx, s.gy, s.gz])
        # 记录三轴分量变化。旋转时重力在各轴间重新分配，但模长仍接近 1g，
        # 因此不能用“模长偏离 1g”判断 IMU 是否得到激励。
        self.accel_samples.append([s.ax, s.ay, s.az])
        if len(self.accel_samples) > 400:
            self.accel_samples = self.accel_samples[-200:]
        if len(self.accel_samples) > 10:
            self.accel_std = float(np.max(np.std(np.asarray(self.accel_samples), axis=0)))
            self.max_accel_std = max(self.max_accel_std, self.accel_std)
        else:
            self.accel_std = 0.0

        if self.last_ts is None:
            self.last_ts = s.ts
            return
        dt = s.ts - self.last_ts
        self.last_ts = s.ts
        if dt <= 0 or dt > 0.1:
            return
        x, y, z, wq = self.q
        dq = np.array([
            0.5 * (wq * w[0] * dt + y * w[2] * dt - z * w[1] * dt),
            0.5 * (wq * w[1] * dt + z * w[0] * dt - x * w[2] * dt),
            0.5 * (wq * w[2] * dt + x * w[1] * dt - y * w[0] * dt),
            -0.5 * (x * w[0] * dt + y * w[1] * dt + z * w[2] * dt),
        ])
        self.q += dq
        self.q /= np.linalg.norm(self.q)
        roll, pitch, yaw = self._quat_to_euler(self.q)
        self.angle_max = np.maximum(self.angle_max, np.abs([roll, pitch, yaw]))

    @staticmethod
    def _quat_to_euler(q):
        x, y, z, w = q
        roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
        pitch = math.asin(max(-1, min(1, 2 * (w * y - z * x))))
        yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        return roll, pitch, yaw

    def feed_image(self, img, detector):
        self.frames += 1
        n = 0
        dets = []
        if detector is not None:
            try:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                dets = detector.detect(gray)
                n = len(dets)
            except Exception:
                pass
        self.tag_counts.append(n)
        if len(self.tag_counts) > 150:
            self.tag_counts = self.tag_counts[-150:]
        self.last_image = img
        self.last_tag_count = n
        self.last_detections = dets
        if n >= 4:
            self.detected += 1

        self.last_pose_ok = False
        if self.pose_tracker is not None and dets:
            if self.pose_tracker.feed_detections(img, dets):
                self.pose_ok_frames += 1
                self.last_pose_ok = True

    def _pose_motion(self, requirement: dict) -> dict:
        """返回当前可用的运动量字典。"""
        if self.pose_tracker is not None and self.pose_ok_frames >= 3:
            return {
                "tx": float(self.pose_tracker.span_t[0]),
                "ty": float(self.pose_tracker.span_t[1]),
                "tz": float(self.pose_tracker.span_t[2]),
                "path": float(self.pose_tracker.path_length),
                "net": float(np.linalg.norm(self.pose_tracker.current_t)),
                "span_3d": float(np.linalg.norm(self.pose_tracker.span_t)),
                "roll": float(self.pose_tracker.max_r[0]),
                "pitch": float(self.pose_tracker.max_r[1]),
                "yaw": float(self.pose_tracker.max_r[2]),
            }
        # 备用: IMU 积分角度
        return {
            "roll": float(np.degrees(self.angle_max[0])),
            "pitch": float(np.degrees(self.angle_max[1])),
            "yaw": float(np.degrees(self.angle_max[2])),
        }

    def check(self, requirement: dict) -> tuple:
        """返回 (是否达标, 提示列表)。"""
        ok_list = []
        fail_list = []

        detect_rate = (
            sum(n >= 4 for n in self.tag_counts) / len(self.tag_counts)
            if self.tag_counts else 0.0
        )
        avg_tags = np.mean(self.tag_counts) if self.tag_counts else 0

        tags_min = requirement.get("tags_min", 6)
        if avg_tags >= tags_min and detect_rate >= 0.6:
            ok_list.append(f"AprilGrid识别 OK ({avg_tags:.1f} tags, {detect_rate*100:.0f}%)")
        else:
            fail_list.append(f"AprilGrid识别不足 ({avg_tags:.1f} tags, {detect_rate*100:.0f}%), 请让板子占满画面")

        motion = self._pose_motion(requirement)
        using_pnp = self.pose_tracker is not None and self.pose_ok_frames >= 3

        for key in ("roll", "pitch", "yaw"):
            if key in requirement:
                need = requirement[key]
                got = motion.get(key, 0.0)
                src = "(PnP)" if using_pnp else "(IMU备用)"
                if got >= need:
                    ok_list.append(f"{key}角度 OK {src} ({got:.1f}deg/{need:.1f}deg)")
                else:
                    fail_list.append(f"{key}角度不够 {src} ({got:.1f}deg/{need:.1f}deg), 请继续旋转")

        for key in ("tx", "ty", "tz"):
            if key in requirement:
                need = requirement[key]
                got = motion.get(key, 0.0)
                src = "(PnP)" if using_pnp else "(IMU备用, 不准)"
                if got >= need:
                    ok_list.append(f"{key}覆盖范围 OK {src} ({got*1000:.1f}mm/{need*1000:.1f}mm)")
                else:
                    fail_list.append(f"{key}覆盖范围不够 {src} ({got*1000:.1f}mm/{need*1000:.1f}mm), 请继续移动")

        # IMU 激励检查 (确保陀螺和加计有足够信号做外参估计)
        imu_excite = requirement.get("imu_excite", 0.0)
        if imu_excite > 0:
            if self.max_accel_std >= imu_excite:
                ok_list.append(f"IMU激励 OK ({self.max_accel_std:.3f}g/{imu_excite:.3f}g)")
            else:
                fail_list.append(f"IMU激励不足 ({self.max_accel_std:.3f}g/{imu_excite:.3f}g), 晃动幅度大一些!")

        # 调试信息: 显示全部 PnP 运动量, 帮助用户理解当前运动
        if using_pnp:
            ok_list.append(
                f"PnP覆盖范围: X={motion.get('tx',0)*1000:.1f}mm Y={motion.get('ty',0)*1000:.1f}mm "
                f"Z={motion.get('tz',0)*1000:.1f}mm 路径≈{motion.get('path',0):.2f}m "
                f"roll={motion.get('roll',0):.1f} "
                f"pitch={motion.get('pitch',0):.1f} yaw={motion.get('yaw',0):.1f}"
            )

        return len(fail_list) == 0, fail_list, ok_list


def collect_calib_data(config_path, duration_per_phase: float, out_root: Path,
                       strict: bool = False, aprilgrid_cfg: Path = None,
                       tag_family: str = "t36h11", mode: str = "imucam",
                       preview: bool = False):
    cfg = load_config(config_path)
    unit = cfg.units[0]

    session = f"calib_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir = out_root / session / unit.name
    frames_dir = out_dir / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(exist_ok=True)

    # AprilGrid 检测器
    detector = None
    try:
        from aprilgrid import Detector
        detector = Detector(tag_family)
        print(f"[AprilGrid] 检测器家族: {tag_family}")
    except Exception as e:
        print(f"[警告] 无法加载 AprilGrid 检测器家族 {tag_family}: {e}")

    # AprilGrid 配置(用于 PnP 实时位姿跟踪)
    grid_cfg = None
    if aprilgrid_cfg is None:
        aprilgrid_cfg = Path(__file__).resolve().parent.parent / "config" / "aprilgrid_6x6_35mm.yaml"
    try:
        grid_cfg = load_aprilgrid_config(Path(aprilgrid_cfg))
        print(f"[AprilGrid] 配置文件: {aprilgrid_cfg}")
        print(f"[AprilGrid] {grid_cfg['tagCols']}x{grid_cfg['tagRows']}, "
              f"tagSize={grid_cfg['tagSize']*1000:.1f}mm, spacing={grid_cfg['tagSpacing']}")
    except Exception as e:
        print(f"[警告] 无法读取 AprilGrid 配置 {aprilgrid_cfg}: {e}")
        print("[AprilGrid] 使用硬编码 6x6 tagSize=35.2mm spacing=0.3 作为 fallback")
        grid_cfg = {"tagCols": 6, "tagRows": 6, "tagSize": 0.0352, "tagSpacing": 0.3}

    # 两个独立队列, 避免写盘线程和质检线程竞争同一份数据
    q_write = Queue(maxsize=120)
    q_quality = Queue(maxsize=60)  # 质检队列满时丢帧, 不阻塞采集

    def on_imu(s):
        item = ("imu", s)
        q_write.put(item)
        try:
            q_quality.put_nowait(item)
        except Exception:
            pass

    preview_latest = {"image": None}
    preview_lock = threading.Lock()

    def on_frame(f: CameraFrame):
        if f.color is not None:
            if preview:
                with preview_lock:
                    preview_latest["image"] = f.color.copy()
            item = ("img", f.frame_idx, f.ts, f.color, time.time(), f.frame_number)
            q_write.put(item)
            try:
                q_quality.put_nowait(item)
            except Exception:
                pass

    imu_reader = ImuReader(port=unit.imu.port, baud=unit.imu.baud,
                           on_sample=on_imu, name=unit.name)
    cam = RealSenseCapture(
        serial=unit.camera.serial,
        width=unit.camera.width, height=unit.camera.height,
        fps=unit.camera.fps, enable_depth=unit.camera.enable_depth,
        # 必须传 stereo_ir! 否则默认 False, 抓的是彩色 BGR, 不是 SLAM 用的左 IR
        stereo_ir=bool(getattr(unit.camera, "stereo_ir", False)),
        auto_exposure=unit.camera.auto_exposure,
        exposure_us=unit.camera.exposure_us,
        gain=unit.camera.gain,
        on_frame=on_frame, name=unit.name,
    )

    cam_csv = open(out_dir / "camera_ts.csv", "w", newline="")
    cam_w = csv.writer(cam_csv)
    cam_w.writerow(["idx", "frame_number", "ts_mono", "ts_wall", "has_depth"])
    imu_bin = open(out_dir / "imu.bin", "wb")
    imu_csv = open(out_dir / "imu_ts.csv", "w", newline="")
    imu_w = csv.writer(imu_csv)
    imu_w.writerow(["counter", "ts_mono", "rx_mono", "ts_wall"])

    written_frames = 0
    written_imu = 0

    def writer_loop():
        nonlocal written_frames, written_imu
        while True:
            item = q_write.get()
            if item is None:
                break
            kind = item[0]
            try:
                if kind == "img":
                    _, idx, ts, img, ts_wall, fnum = item
                    path = frames_dir / f"{idx:06d}.jpg"
                    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                    if ok:
                        buf.tofile(path)
                        written_frames += 1
                    cam_w.writerow([idx, fnum, f"{ts:.9f}", f"{ts_wall:.6f}", 0])
                elif kind == "imu":
                    s = item[1]
                    imu_bin.write(struct.pack(
                        IMU_PACK_FMT, s.ts, s.counter,
                        s.gx, s.gy, s.gz, s.ax, s.ay, s.az, s.temp,
                    ))
                    imu_w.writerow([
                        s.counter,
                        f"{s.ts:.9f}",
                        f"{s.rx_time:.9f}",
                        f"{time.time():.6f}",
                    ])
                    written_imu += 1
            except Exception as e:
                print(f"[写盘错误] {e}")

    writer = threading.Thread(target=writer_loop, daemon=True)
    writer.start()

    # RealSense 初始化可能让 IMU 线程短暂停顿。先完成相机启动，再打开
    # 并清空 IMU 串口，避免启动积压污染两路时间同步。
    cam.start()
    imu_ok = imu_reader.start()
    if not imu_ok:
        print("=" * 60)
        print("!! IMU 串口打不开!")
        print(f"   端口: {unit.imu.port}")
        print("   检查: IMU 是否上电/连接; 确认 --config 用的是 Ubuntu 配置 "
              "(config/devices_ubuntu.yaml), 不是 Windows 默认(COM9)")
        if mode == "imucam":
            print("   (外参标定必须 IMU, 已终止)")
            print("=" * 60)
            cam.stop()
            return
        print("   (内参标定不需要 IMU, 继续; 但外参标定必须先修好 IMU)")
        print("=" * 60)
    if not getattr(unit.camera, "stereo_ir", False):
        print("=" * 60)
        print("!! 警告: 相机配置 stereo_ir=False, 标定的是彩色相机, "
              "不是 SLAM 用的左 IR!")
        print("   请用 --config config/devices_ubuntu.yaml (stereo_ir: true)")
        print("=" * 60)

    # 获取相机内参并创建 PnP 位姿跟踪器
    pose_tracker = None
    intrinsics = cam.get_intrinsics()
    if intrinsics is not None:
        K, D = intrinsics
        print(f"[相机内参] fx={K[0,0]:.1f} fy={K[1,1]:.1f} cx={K[0,2]:.1f} cy={K[1,2]:.1f}")
    else:
        # 内参获取失败时用 D405 640x480 典型值作为 fallback, 仅用于实时反馈
        K = np.array([[600.0, 0.0, 320.0],
                      [0.0, 600.0, 240.0],
                      [0.0, 0.0, 1.0]], dtype=np.float64)
        D = np.zeros(5, dtype=np.float64)
        print("[相机内参] 使用 D405 640x480 典型值做 fallback (fx=600, cx=320)")

    if grid_cfg is not None and detector is not None:
        pose_tracker = AprilGridPoseTracker(
            detector, grid_cfg, K, D,
            track_camera_motion=(mode == "imucam"),
        )
        print("[PnP] 已启用 AprilGrid 实时位姿跟踪")
    else:
        reason = []
        if grid_cfg is None:
            reason.append("AprilGrid 配置未加载")
        if detector is None:
            reason.append("AprilGrid 检测器未加载")
        print(f"[PnP] 未启用: {', '.join(reason)}, 回退到 IMU 积分")
        print("      建议检查: pip install aprilgrid; 确认 config/aprilgrid_6x6_35mm.yaml 存在")

    # 根据模式选择阶段
    if mode == "camera":
        phases = [
            ("近距离: 板子占画面70-80%, 缓慢移动覆盖四角", 8, {"tags_min": 8, "tx": 0.02, "ty": 0.02}),
            ("中距离: 板子占画面40-60%, 倾斜±30°拍四角", 8, {"tags_min": 6, "tx": 0.03, "ty": 0.03, "pitch": 15.0}),
            ("远距离: 板子占画面20-30%, 缓慢旋转拍全", 8, {"tags_min": 4, "pitch": 20.0, "yaw": 20.0}),
            ("旋转: 板子在画面中心, 缓慢旋转相机/板子", 8, {"tags_min": 4, "roll": 20.0}),
            ("倾斜: 板子倾斜45°, 从四角拍摄", 8, {"tags_min": 4, "pitch": 25.0, "roll": 25.0}),
        ]
        preview_hints = [
            "近距离移动标定板，覆盖画面四个角",
            "中距离倾斜标定板约正负30度，并覆盖四角",
            "拉远标定板，缓慢做左右偏转和画面内旋转",
            "让标定板保持在中心，做上下倾斜",
            "标定板倾斜约45度，并移动到画面四角",
        ]
        mode_hint = "手持标定板多角度晃动, 覆盖画面全区域"
    else:
        phases = PHASES  # use the imucam phases defined above
        preview_hints = [
            "保持标定板固定，沿X轴缓慢横向移动相机和IMU",
            "保持标定板固定，沿Y轴缓慢上下移动相机和IMU",
            "保持标定板固定，沿Z轴缓慢靠近和远离标定板",
            "保持标定板可见，让相机和IMU上下俯仰",
            "保持标定板可见，让相机和IMU左右横滚",
            "保持标定板可见，让相机和IMU左右偏航",
        ]
        mode_hint = "标定板固定墙上, 相机+IMU 晃动"

    print("=" * 60)
    if mode == "camera":
        print("Kalibr 相机内参标定数据采集")
        print("输出目录:", out_dir)
        print("手持标定板在相机前多角度晃动, 覆盖画面四角+中心")
        print("距离: 近(板子占80%画面) → 中 → 远(板子占30%画面)")
    else:
        print("Kalibr 相机-IMU 外参标定数据采集")
        print("输出目录:", out_dir)
        print("⚠️  标定板固定贴墙上, 手持相机+IMU 晃动!")
        print("   板子始终在画面内, 相机多方向平移+旋转")
    if strict:
        print("模式: 严格 — 每步必须达标才进入下一阶段")
    else:
        print("模式: 标准 — 每步按固定时长进行, 质量指标仅作参考")
        print(f"动作提示: {mode_hint}")
    print("=" * 60)

    stage = StageQuality(pose_tracker=pose_tracker)

    preview_enabled = bool(preview)
    preview_font = None
    if preview_enabled:
        try:
            cv2.namedWindow("ego_vio calibration camera", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("ego_vio calibration camera", 1280, 720)
            preview_font = ImageFont.truetype(PREVIEW_FONT_PATH, 25)
            print("[预览] 已打开实时相机窗口，按 q 可在完成当前保存后退出")
        except Exception as e:
            preview_enabled = False
            print(f"[预览] 无法打开窗口，继续无预览采集: {e}")

    # 启动验证
    print("\n[初始化验证] 等待首帧并检测 AprilGrid... 请把板子放在相机视野内")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 2.5:
        while not q_quality.empty():
            try:
                item = q_quality.get_nowait()
                if item[0] == "img":
                    stage.feed_image(item[3], detector)
                elif item[0] == "imu":
                    stage.feed_imu(item[1])
            except Exception as e:
                print(f"[初始化验证异常] {e}")
                break
        time.sleep(0.05)
    if stage.frames > 0:
        detect_rate = stage.detected / stage.frames
        print(f"[初始化验证] 收到 {stage.frames} 帧, AprilGrid 识别率 {detect_rate*100:.0f}%")
        if detect_rate < 0.5:
            print("!! AprilGrid 识别率过低, 请检查板子是否在画面内、是否平整、光线是否充足")
        if pose_tracker is not None and stage.pose_ok_frames < 3:
            print("!! PnP 位姿解算未成功, 可能是 tag 数量不足或板子参数不匹配")
        if pose_tracker is not None and stage.pose_ok_frames >= 3:
            print("[初始化验证] PnP 位姿解算 OK")
    else:
        print("!! 初始化验证未收到任何帧, 请检查相机连接")
    stage.reset()

    phase_idx = 0
    t_phase_start = time.monotonic()
    last_report = time.monotonic()
    last_gate_report = 0.0

    while phase_idx < len(phases):
        name, min_secs, req = phases[phase_idx]
        required_secs = max(float(duration_per_phase), float(min_secs))
        now = time.monotonic()
        elapsed_phase = now - t_phase_start

        # 消费队列用于质检(不阻塞写线程)
        while not q_quality.empty():
            try:
                item = q_quality.get_nowait()
                if item[0] == "imu":
                    stage.feed_imu(item[1])
                elif item[0] == "img":
                    stage.feed_image(item[3], detector)
            except Exception as e:
                print(f"[质检异常] {e}")
                break

        if preview_enabled:
            if stage.last_image is not None:
                vis = stage.last_image.copy()
                for det in stage.last_detections:
                    corners = np.asarray(getattr(det, "corners", []), dtype=np.int32)
                    if corners.size == 8:
                        cv2.polylines(vis, [corners.reshape(4, 2)], True, (0, 255, 0), 2)
            else:
                with preview_lock:
                    preview_img = preview_latest["image"]
                vis = preview_img.copy() if preview_img is not None else None

            if vis is not None:
                status_ok, status_fails, _ = stage.check(req)
                detect_rate = (
                    sum(n >= 4 for n in stage.tag_counts) / len(stage.tag_counts)
                    if stage.tag_counts else 0.0
                )
                avg_tags = float(np.mean(stage.tag_counts)) if stage.tag_counts else 0.0
                motion = stage._pose_motion(req)
                time_ok = elapsed_phase >= required_secs
                status = "已达标，等待计时完成" if status_ok and not time_ok else (
                    "已通过" if status_ok else "未通过"
                )
                status_color = (0, 220, 0) if status_ok else (255, 165, 0)

                lines = [
                    (f"当前步骤 {phase_idx + 1}/{len(phases)}    状态：{status}", status_color),
                    (f"动作：{preview_hints[phase_idx]}", (255, 255, 0)),
                    (f"当前识别 {stage.last_tag_count}/36    平均 {avg_tags:.1f}    最近有效率 {detect_rate*100:.0f}%", (255, 255, 255)),
                    (f"位姿解算：{'有效' if stage.last_pose_ok else '未更新'}    计时 {elapsed_phase:.0f}/{required_secs:.0f} 秒", (255, 255, 255)),
                ]
                required_motion = []
                for key in ("tx", "ty", "tz"):
                    if key in req:
                        required_motion.append(f"{key[1].upper()}轴覆盖 {motion.get(key, 0.0)*1000:.0f}/{req[key]*1000:.0f}毫米")
                rotation_names = {
                    "roll": "绕X（上下倾斜）",
                    "pitch": "绕Y（左右偏转）",
                    "yaw": "绕Z（画面内旋转）",
                }
                for key in ("roll", "pitch", "yaw"):
                    if key in req:
                        required_motion.append(f"{rotation_names[key]} {motion.get(key, 0.0):.1f}/{req[key]:.1f}度")
                if req.get("imu_excite", 0.0) > 0.0:
                    required_motion.append(
                        f"IMU激励 {stage.max_accel_std:.3f}/{req['imu_excite']:.3f}g"
                    )
                if required_motion:
                    lines.append(("  ".join(required_motion), (255, 255, 255)))
                if stage.pose_tracker is not None and stage.pose_ok_frames >= 3:
                    lines.append((
                        f"三维覆盖 {motion.get('span_3d', 0.0)*1000:.0f}毫米    "
                        f"当前离起点 {motion.get('net', 0.0)*1000:.0f}毫米    "
                        f"累计路径约 {motion.get('path', 0.0):.2f}米",
                        (255, 255, 255),
                    ))
                if stage.last_tag_count < req.get("tags_min", 4):
                    lines.append(("提示：把标定板靠近，保持正面和整块板可见", (255, 64, 64)))
                elif status_fails:
                    for fail in status_fails[:2]:
                        lines.append((f"未通过原因：{fail}", (255, 165, 0)))
                else:
                    lines.append(("提示：保持缓慢运动，程序将自动进入下一步", (0, 220, 0)))

                panel_h = 18 + 34 * len(lines)
                overlay = vis.copy()
                cv2.rectangle(overlay, (0, 0), (vis.shape[1], panel_h), (0, 0, 0), -1)
                cv2.addWeighted(overlay, 0.72, vis, 0.28, 0, vis)
                rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
                pil_image = Image.fromarray(rgb)
                draw = ImageDraw.Draw(pil_image)
                for row, (text_line, color) in enumerate(lines):
                    draw.text((16, 5 + row * 34), text_line, font=preview_font, fill=color)
                vis = cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)

                cv2.imshow("ego_vio calibration camera", vis)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    print("[预览] 收到 q，结束采集")
                    phase_idx = len(phases)
                    break

        # 阶段结束判断
        if elapsed_phase >= required_secs:
            ok, fails, oks = stage.check(req)
            if strict and not ok:
                if now - last_gate_report >= 2.0:
                    print(f"\n❌ [{name}] 未达标, 请继续:")
                    for f in fails:
                        print(f"   - {f}")
                    last_gate_report = now
                continue

            print(f"\n✅ [{name}] 完成")
            for o in oks:
                print(f"   {o}")
            if not ok:
                print("   (质量指标未完全达标, 但已继续; 建议本阶段动作再大一些)")
            phase_idx += 1
            stage.reset()
            t_phase_start = now
            last_gate_report = 0.0
            if phase_idx < len(phases):
                print(f"\n>>> 下一步: {phases[phase_idx][0]}")
            continue

        # 实时提示
        if now - last_report >= 2.0:
            print(f"\n[{name}] 已{elapsed_phase:.0f}s")
            ok, fails, oks = stage.check(req)
            if not ok:
                print("  当前指标参考:")
                for f in fails:
                    print(f"    - {f}")
            else:
                print("  当前指标已满足, 继续保持...")
            # 总是打印关键参考信息(PnP运动量 / AprilGrid状态)
            for o in oks:
                if "AprilGrid识别" in o or "PnP覆盖范围" in o:
                    print(f"    {o}")
            last_report = now

        time.sleep(0.05)

    print("\n" + "=" * 60)
    print("✅ 全部阶段完成, 采集结束")
    print("=" * 60)

    print("\n正在关闭设备并保存文件...")
    cam.stop()
    imu_reader.stop()
    q_write.put(None)
    writer.join(timeout=5.0)

    cam_csv.close()
    imu_bin.close()
    imu_csv.close()
    if preview_enabled:
        cv2.destroyWindow("ego_vio calibration camera")

    print(f"\n图像帧: {written_frames}  |  IMU帧: {written_imu}")
    print(f"数据保存到: {out_dir}")
    print("下一步: python scripts\\convert_to_kalibr_bag.py --input "
          f"{out_root / session} --output calib.bag")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--phase-secs", type=float, default=10,
                    help="每个阶段最短秒数")
    ap.add_argument("--out", default="recordings")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式: 每步必须满足运动指标才进入下一阶段")
    ap.add_argument("--aprilgrid", default=None,
                    help="AprilGrid 配置文件路径(默认 config/aprilgrid_6x6_35mm.yaml)")
    ap.add_argument("--tag-family", default="t36h11",
                    help="AprilTag 家族: t36h11 (Kalibr 2-bit, 默认) 或 t36h11b1 (1-bit)")
    ap.add_argument("--preview", action="store_true",
                    help="打开实时相机预览窗口, 与采集同步显示")
    ap.add_argument("--mode", default="imucam", choices=["imucam", "camera"],
                    help="imucam=相机+IMU外参(板子固定晃相机) camera=纯相机内参(晃板子拍全)")
    args = ap.parse_args()

    if args.mode == "camera":
        print("=== 相机内参标定: 手持标定板在相机前多角度晃动, 覆盖画面 ===")
    else:
        print("=== 相机-IMU 外参标定: 标定板固定墙上, 手持相机+IMU晃动 ===")

    collect_calib_data(
        args.config, args.phase_secs, Path(args.out),
        strict=args.strict,
        aprilgrid_cfg=Path(args.aprilgrid) if args.aprilgrid else None,
        tag_family=args.tag_family,
        mode=args.mode,
        preview=args.preview,
    )


if __name__ == "__main__":
    main()
