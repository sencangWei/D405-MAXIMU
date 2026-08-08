#!/usr/bin/env python3
"""回放 MP4 (NVENC 后处理产物) + imu.bin 到 ROS2 topic, 供后端 SLAM 离线跑。

数据源:
  - session/mp4/ir_left.mp4, ir_right.mp4  (bag_to_mp4_nvenc.py 生成)
  - session/mp4/timestamps.csv             (每帧真实设备全局时间戳, ms)
  - external_imu/imu.bin                   (KT-EX9, ts 为 monotonic)

发布 (与 replay_db3_to_ros2.py 相同的 topic/时间线):
  /cam0/image_raw       左IR (mono8)     <- ir_left.mp4
  /cam1/image_raw       右IR (mono8)     <- ir_right.mp4
  /imu0                 sensor_msgs/Imu  <- imu.bin + 重力 Y->Z 变换

时间戳: 图像用 sidecar 里的设备全局时间; IMU 用 monotonic+epoch 偏移对齐。

注意事项 (本机实测):
  - msg.data 必须用 bytearray (不能用 bytes), 否则 FastRTPS 间歇抛
    "context is invalid" 崩溃。回放用 --rate 1.0 (实时) 最稳。
  - 本机 FastRTPS 环境下, VINS (C++ message_filters) 收不到本脚本的
    图像 (Python 订阅者可收到)。SLAM 回放建议先用 replay_db3_to_ros2.py
    (bag 路径, 已验证 181 位姿), MP4 作为存储/传输格式。

用法:
  source /opt/ros/humble/setup.bash
  python3 scripts/replay_mp4_to_ros2.py --session recordings/d405_720p_rgb_stereo_ir_xxx
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from replay_db3_to_ros2 import compute_auto_align, imu_event_iter, load_epoch_minus_mono

W, H, FPS = 1280, 720, 30
GRAY_BYTES = W * H
BGR_BYTES = W * H * 3


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="回放 MP4 + imu.bin 到 ROS2 topic(流式)")
    p.add_argument("--session", type=Path, required=True, help="采集会话目录(含 mp4/ 与 external_imu/)")
    p.add_argument("--rate", type=float, default=1.0, help="回放倍速(1=实时)")
    p.add_argument("--imu-shift-ms", type=float, default=7.36,
                   help="IMU 发布戳平移(Kalibr t_imu=t_cam-7.36ms -> +7.36ms)")
    p.add_argument("--imu-align-s", type=float, default=0.0,
                   help="额外 IMU 平移(s): 补偿 warmup 帧导致图像/IMU 起始错位")
    p.add_argument("--skip-s", type=float, default=0.0, help="跳过开头秒数")
    p.add_argument("--with-color", action="store_true",
                   help="同时发布彩色流(默认关; SLAM 不需要, 且 2.7MB/帧大消息易触发 FastRTPS 崩溃)")
    return p.parse_args()


def load_sidecar(mp4_dir: Path) -> dict:
    """返回 {stream: {frame_index: device_ts_ms}}."""
    out = {k: {} for k in ("ir_left", "ir_right", "color")}
    with (mp4_dir / "timestamps.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            out[row["stream"]][int(row["frame_index"])] = float(row["device_ts_ms"])
    return out


def mp4_frame_iter(mp4_path: Path, pix_fmt: str, frame_bytes: int, ts_list):
    """流式解码 MP4 -> (device_ts_ms, frame_bytes) 序列. ffmpeg 子进程读 stdout."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(mp4_path),
           "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    for ts in ts_list:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        yield ts, buf
    proc.terminate()
    proc.wait()


def main() -> int:
    args = parse_args()
    session = args.session.resolve()
    mp4_dir = session / "mp4"
    if not (mp4_dir / "ir_left.mp4").exists() or not (mp4_dir / "ir_right.mp4").exists():
        print(f"[ERROR] 会话里缺 mp4/ir_left.mp4 或 ir_right.mp4: {session}")
        print("        先跑: python3 scripts/bag_to_mp4_nvenc.py --session <dir>")
        return 1

    ts = load_sidecar(mp4_dir)

    # IMU 对齐 (与 db3 回放一致)
    epoch_minus_mono = load_epoch_minus_mono(session)
    align_s = args.imu_align_s
    if align_s == 0.0:
        align_s = compute_auto_align(session)
        if align_s != 0.0:
            print(f"[replay] 自动对齐: IMU 平移 {align_s:+.3f}s (补偿 warmup)", flush=True)
    print(f"[replay] epoch-monotonic 偏移: {epoch_minus_mono:.6f}s", flush=True)

    imu_it = iter(imu_event_iter(session, epoch_minus_mono,
                                 args.imu_shift_ms + align_s * 1000.0))

    # 图像迭代器: 取 sidecar 时间戳(秒)排序合并三流
    def img_events():
        defs = [("ir_left", mp4_dir / "ir_left.mp4", "gray", GRAY_BYTES, ts["ir_left"]),
                ("ir_right", mp4_dir / "ir_right.mp4", "gray", GRAY_BYTES, ts["ir_right"])]
        gens = []
        for key, path, pix, nbytes, tmap in defs:
            frames = mp4_frame_iter(path, pix, nbytes, list(tmap.values()))
            gens.append(((t / 1000.0, key, b) for t, b in frames))
        # 逐流 peek 一个, 每次取时间最早的一个
        peeks = []
        for g in list(gens):
            try:
                peeks.append(next(g))
            except StopIteration:
                gens.remove(g)
        while peeks:
            i = min(range(len(peeks)), key=lambda j: peeks[j][0])
            ev = peeks[i]
            try:
                peeks[i] = next(gens[i])
            except StopIteration:
                peeks.pop(i)
                gens.pop(i)
            yield ev

    img_it = iter(img_events())

    img_ev = next(img_it, None)
    imu_ev = next(imu_it, None)
    if img_ev is None or imu_ev is None:
        print("[ERROR] 图像或 IMU 事件为空")
        return 1

    import rclpy
    from rclpy.node import Node
    from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                           ReliabilityPolicy)
    from sensor_msgs.msg import Image as RosImage, Imu as RosImu
    from builtin_interfaces.msg import Time as RosTime

    # 图像 BEST_EFFORT: 与 VINS 的 rmw_qos_profile_sensor_data 完全匹配,
    # 且避免 FastRTPS 可靠大图写者被慢消费者阻塞(max_blocking_time)导致的
    # "context is invalid" 崩溃。IMU 保持可靠(与 VINS IMU 订阅匹配)。
    img_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST,
                         durability=DurabilityPolicy.VOLATILE)

    rclpy.init()
    node = Node("mp4_replay")
    pub_cam0 = node.create_publisher(RosImage, "/cam0/image_raw", img_qos)
    pub_cam1 = node.create_publisher(RosImage, "/cam1/image_raw", img_qos)
    pub_imu = node.create_publisher(RosImu, "/imu0", 2000)

    def to_stamp(t_epoch: float) -> RosTime:
        sec = int(t_epoch)
        return RosTime(sec=sec, nanosec=int((t_epoch - sec) * 1e9))

    n_img = n_imu = 0
    t0_data = None
    t0_wall = None
    t_skip_end = None

    def publish_img(t, key, buf):
        nonlocal n_img
        msg = RosImage()
        msg.header.stamp = to_stamp(t)
        msg.header.frame_id = "cam0" if key == "ir_left" else "cam1"
        msg.height, msg.width = H, W
        msg.step = W
        msg.is_bigendian = False
        msg.encoding = "mono8"
        # 用 bytearray (与 db3 回放的 deserialized 数据一致), 避免 bytes 触发 FastRTPS 崩溃
        msg.data = bytearray(buf)
        if key == "ir_left":
            pub_cam0.publish(msg)
        else:
            pub_cam1.publish(msg)
        n_img += 1

    def publish_imu(ev):
        nonlocal n_imu
        t, gx, gy, gz, ax, ay, az = ev
        # IMU 坐标系变换: 重力 y -> z 向下 (R 见 replay_db3_to_ros2.py)
        gx_n = 0.99980212 * gx - 0.01423891 * gy - 0.01389161 * gz
        gy_n = -0.01423891 * gx - 0.02458715 * gy - 0.99959628 * gz
        gz_n = 0.01389161 * gx + 0.99959628 * gy - 0.02478503 * gz
        ax_n = 0.99980212 * ax - 0.01423891 * ay - 0.01389161 * az
        ay_n = -0.01423891 * ax - 0.02458715 * ay - 0.99959628 * az
        az_n = 0.01389161 * ax + 0.99959628 * ay - 0.02478503 * az
        im = RosImu()
        im.header.stamp = to_stamp(t)
        im.header.frame_id = "imu0"
        im.angular_velocity.x = gx_n
        im.angular_velocity.y = gy_n
        im.angular_velocity.z = gz_n
        im.linear_acceleration.x = ax_n
        im.linear_acceleration.y = ay_n
        im.linear_acceleration.z = az_n
        pub_imu.publish(im)
        n_imu += 1

    print("[replay] 开始回放 (MP4 解码)...", flush=True)
    try:
        while img_ev is not None or imu_ev is not None:
            _diag = (n_img, n_imu)
            if imu_ev is None or (img_ev is not None and img_ev[0] <= imu_ev[0]):
                t = img_ev[0]
                ev = img_ev
                img_ev = next(img_it, None)
                kind = "img"
            else:
                t = imu_ev[0]
                ev = imu_ev
                imu_ev = next(imu_it, None)
                kind = "imu"

            if t0_data is None:
                t0_data = t
                t_skip_end = t + args.skip_s
            if t < t_skip_end:
                continue
            if t0_wall is None:
                t0_wall = time.monotonic()
                t0_data = t

            target = t0_wall + (t - t0_data) / max(args.rate, 1e-6)
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, 0.5))

            try:
                if kind == "img":
                    publish_img(ev[0], ev[1], ev[2])
                else:
                    publish_imu(ev)
            except Exception as e:
                print(f"[replay] FAIL kind={kind} t={t - t0_data:.1f}s "
                      f"img={n_img} imu={n_imu} "
                      f"ev0={ev[0]} ev1={ev[1] if kind=='img' else ''} "
                      f"buflen={len(ev[2]) if kind=='img' else ''}", flush=True)
                raise
            if kind == "imu" and n_imu % 4000 == 0:
                print(f"[replay] t={t - t0_data:7.1f}s img={n_img} imu={n_imu}", flush=True)
    except KeyboardInterrupt:
        print("\n[replay] 中断")
    finally:
        node.destroy_node()
        rclpy.shutdown()

    print(f"[replay] 完成: img={n_img} imu={n_imu}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
