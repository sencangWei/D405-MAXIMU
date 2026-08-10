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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import load_config
from ego_vio.imu.imu_reader import ImuReader
from ego_vio.camera.realsense_capture import RealSenseCapture, CameraFrame


IMU_PACK_FMT = "<dI7f"
IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)

# 标定板固定墙上, 手持相机+IMU 移动。
# 六个阶段: (名称, 最小持续时间, 检查指标)
# PnP 测相机-标定板相对运动; IMU 激励确保陀螺/加计有足够信号。
PHASES = [
    ("平移: 握相机缓慢左右移动(保持板子在画面内)", 10, {"tags_min": 6, "tx": 0.08, "imu_excite": 0.3}),
    ("平移: 握相机缓慢上下移动", 10, {"tags_min": 6, "ty": 0.08, "imu_excite": 0.3}),
    ("平移: 握相机缓慢前后移动", 10, {"tags_min": 6, "tz": 0.10, "imu_excite": 0.3}),
    ("旋转: 握相机缓慢俯仰(pitch)", 10, {"tags_min": 4, "pitch": 12.0, "imu_excite": 0.5}),
    ("旋转: 握相机缓慢横滚(roll)",  10, {"tags_min": 4, "roll": 12.0, "imu_excite": 0.5}),
    ("旋转: 握相机缓慢偏航(yaw)",   10, {"tags_min": 4, "yaw": 15.0, "imu_excite": 0.5}),
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

    def __init__(self, detector, grid_cfg: dict, K: np.ndarray, D: np.ndarray):
        self.detector = detector
        self.tag_cols = grid_cfg["tagCols"]
        self.tag_rows = grid_cfg["tagRows"]
        self.tag_size = grid_cfg["tagSize"]
        self.tag_spacing = grid_cfg["tagSpacing"]
        self.K = K
        self.D = D
        self._init_rvec = None
        self._init_tvec = None
        self._last_rvec = None
        self._last_tvec = None
        self.max_t = np.zeros(3)
        self.max_r = np.zeros(3)

    def reset(self):
        self._init_rvec = None
        self._init_tvec = None
        self._last_rvec = None
        self._last_tvec = None
        self.max_t = np.zeros(3)
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
            corners = getattr(d, "corners", None)
            if corners is None:
                continue
            corners = np.asarray(corners, dtype=np.float32)
            if corners.size == 8:
                corners = corners.reshape(4, 2)
            if corners.shape != (4, 2):
                continue
            obj_pts.append(self._tag_corners_3d(int(d.tag_id)))
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

        self._last_rvec = rvec
        self._last_tvec = tvec
        if self._init_rvec is None:
            self._init_rvec = rvec.copy()
            self._init_tvec = tvec.copy()
            return True

        R_init, _ = cv2.Rodrigues(self._init_rvec)
        R_last, _ = cv2.Rodrigues(rvec)
        R_rel = R_last @ R_init.T
        roll, pitch, yaw = self._R_to_euler(R_rel)
        t_rel = (tvec - self._init_tvec).flatten()

        self.max_t = np.maximum(self.max_t, np.abs(t_rel))
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

        # IMU 激励检测
        self.q = np.array([0.0, 0.0, 0.0, 1.0])
        self.last_ts = None
        self.angle_max = np.zeros(3)
        self.accel_std = 0.0    # 加速计标准差(激励指标)
        self.accel_samples = []

    def reset(self):
        self.__init__(self.pose_tracker)
        if self.pose_tracker is not None:
            self.pose_tracker.reset()

    def feed_imu(self, s):
        w = np.radians([s.gx, s.gy, s.gz])
        # 记录加速计幅值变化(去除重力后)
        accel_mag = np.sqrt(s.ax**2 + s.ay**2 + s.az**2) - 1.0  # 偏离 1g 的量
        self.accel_samples.append(abs(accel_mag))
        if len(self.accel_samples) > 400:
            self.accel_samples = self.accel_samples[-200:]
        self.accel_std = float(np.std(self.accel_samples)) if len(self.accel_samples) > 10 else 0.0

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
        if n >= 4:
            self.detected += 1

        if self.pose_tracker is not None and dets:
            if self.pose_tracker.feed_detections(img, dets):
                self.pose_ok_frames += 1

    def _pose_motion(self, requirement: dict) -> dict:
        """返回当前可用的运动量字典。"""
        if self.pose_tracker is not None and self.pose_ok_frames >= 3:
            return {
                "tx": float(self.pose_tracker.max_t[0]),
                "ty": float(self.pose_tracker.max_t[1]),
                "tz": float(self.pose_tracker.max_t[2]),
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

        detect_rate = self.detected / max(self.frames, 1)
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
                    ok_list.append(f"{key}平移 OK {src} ({got*1000:.1f}mm/{need*1000:.1f}mm)")
                else:
                    fail_list.append(f"{key}平移不够 {src} ({got*1000:.1f}mm/{need*1000:.1f}mm), 请继续移动")

        # IMU 激励检查 (确保陀螺和加计有足够信号做外参估计)
        imu_excite = requirement.get("imu_excite", 0.0)
        if imu_excite > 0:
            if self.accel_std >= imu_excite:
                ok_list.append(f"IMU激励 OK ({self.accel_std:.2f}g/{imu_excite:.2f}g)")
            else:
                fail_list.append(f"IMU激励不足 ({self.accel_std:.2f}g/{imu_excite:.2f}g), 晃动幅度大一些!")

        # 调试信息: 显示全部 PnP 运动量, 帮助用户理解当前运动
        if using_pnp:
            ok_list.append(
                f"PnP运动量: tx={motion.get('tx',0)*1000:.1f}mm ty={motion.get('ty',0)*1000:.1f}mm "
                f"tz={motion.get('tz',0)*1000:.1f}mm roll={motion.get('roll',0):.1f} "
                f"pitch={motion.get('pitch',0):.1f} yaw={motion.get('yaw',0):.1f}"
            )

        return len(fail_list) == 0, fail_list, ok_list


def collect_calib_data(config_path, duration_per_phase: float, out_root: Path,
                       strict: bool = False, aprilgrid_cfg: Path = None,
                       tag_family: str = "t36h11", mode: str = "imucam"):
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

    def on_frame(f: CameraFrame):
        if f.color is not None:
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
        on_frame=on_frame, name=unit.name,
    )

    cam_csv = open(out_dir / "camera_ts.csv", "w", newline="")
    cam_w = csv.writer(cam_csv)
    cam_w.writerow(["idx", "frame_number", "ts_mono", "ts_wall", "has_depth"])
    imu_bin = open(out_dir / "imu.bin", "wb")
    imu_csv = open(out_dir / "imu_ts.csv", "w", newline="")
    imu_w = csv.writer(imu_csv)
    imu_w.writerow(["counter", "ts_mono", "ts_wall"])

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
                    imu_w.writerow([s.counter, f"{s.ts:.9f}", f"{time.time():.6f}"])
                    written_imu += 1
            except Exception as e:
                print(f"[写盘错误] {e}")

    writer = threading.Thread(target=writer_loop, daemon=True)
    writer.start()

    imu_reader.start()
    cam.start()

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
        pose_tracker = AprilGridPoseTracker(detector, grid_cfg, K, D)
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
        mode_hint = "手持标定板多角度晃动, 覆盖画面全区域"
    else:
        phases = PHASES  # use the imucam phases defined above
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

    while phase_idx < len(phases):
        name, min_secs, req = phases[phase_idx]
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

        # 阶段结束判断
        if elapsed_phase >= min_secs:
            ok, fails, oks = stage.check(req)
            if strict and not ok:
                print(f"\n❌ [{name}] 未达标, 请继续:")
                for f in fails:
                    print(f"   - {f}")
                t_phase_start = now  # 严格模式: 延长当前阶段
                continue

            print(f"\n✅ [{name}] 完成")
            for o in oks:
                print(f"   {o}")
            if not ok:
                print("   (质量指标未完全达标, 但已继续; 建议本阶段动作再大一些)")
            phase_idx += 1
            stage.reset()
            t_phase_start = now
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
                if "AprilGrid识别" in o or "PnP运动量" in o:
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
    )


if __name__ == "__main__":
    main()
