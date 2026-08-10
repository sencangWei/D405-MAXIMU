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

注意事项 (2026-08-10 根因):
  - 早期 "VINS 收不到图像 + context is invalid 崩溃" 真因 = img_events() 生成器
    表达式按引用捕获循环变量 key, 两流帧全被标成 ir_right -> cam0 空 (stereo 同步
    0 位姿) + cam1 单流 2x 流量拥塞 FastRTPS。已用 make_gen(key, frames) 默认参数
    按值捕获修复 (sink 实测 cam0=945/cam1=944/imu=12157 全达)。
  - 回放用 --rate 1.0 (实时) 最稳; 图像 QoS 用 RELIABLE (与 replay_db3 一致,
    可匹配 VINS 的 BEST_EFFORT stereo 订阅)。

用法:
  source /opt/ros/humble/setup.bash
  python3 scripts/replay_mp4_to_ros2.py --session recordings/d405_720p_rgb_stereo_ir_xxx
"""
from __future__ import annotations

import argparse
import csv
import os
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
    p.add_argument("--imu-shift-ms", type=float, default=0.0,
                   help="IMU 发布戳平移(Kalibr t_imu=t_cam-7.36ms -> +7.36ms)")
    p.add_argument("--imu-align-s", type=float, default=0.0,
                   help="额外 IMU 平移(s): 补偿 warmup 帧导致图像/IMU 起始错位")
    p.add_argument("--skip-s", type=float, default=0.0, help="跳过开头秒数")
    p.add_argument("--mode", default="stereo",
                   help="兼容 _test_vins_dynamic.py; 本脚本固定双 IR (ir_left/ir_right)")
    p.add_argument("--raw-dir", type=Path, default=None,
                   help="预解码裸帧目录 (含 ir_left.raw/ir_right.raw): 不启动 ffmpeg 子进程, "
                        "用于隔离 ffmpeg 对 VINS 实时性的扰动")
    return p.parse_args()


def load_sidecar(mp4_dir: Path) -> dict:
    """返回 {stream: {frame_index: device_ts_ms}}."""
    out = {k: {} for k in ("ir_left", "ir_right", "color")}
    with (mp4_dir / "timestamps.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            out[row["stream"]][int(row["frame_index"])] = float(row["device_ts_ms"])
    return out


def mp4_frame_iter(mp4_path: Path, pix_fmt: str, frame_bytes: int, ts_list, raw_file=None):
    """流式解码 MP4 -> (device_ts_ms, frame_bytes) 序列.

    raw_file 指定时直接从预解码裸文件顺序读取 (无 ffmpeg 子进程)。
    """
    if raw_file is not None:
        f = open(raw_file, "rb")
        for ts in ts_list:
            buf = f.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield ts, buf
        f.close()
        return
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
    if args.raw_dir is None and os.environ.get("REPLAY_RAW_DIR"):
        args.raw_dir = Path(os.environ["REPLAY_RAW_DIR"])
    session = args.session.resolve()
    mp4_dir = session / "mp4"
    # IR 支持 .mkv (FFV1 无损) 或 .mp4 (HEVC cq18 有损); color 始终 .mp4
    def ir_file(key: str) -> Path:
        for ext in (".mkv", ".mp4"):
            p = mp4_dir / f"{key}{ext}"
            if p.exists():
                return p
        return mp4_dir / f"{key}.mkv"
    if not ir_file("ir_left").exists() or not ir_file("ir_right").exists():
        print(f"[ERROR] 会话里缺 mp4/ir_left(.mkv/.mp4) 或 ir_right: {session}")
        return 1

    ts = load_sidecar(mp4_dir)

    # IMU 对齐: 与 db3 回放语义完全一致 (镜像 replay_db3_to_ros2.py)。
    # 关键: 数值 0 必须原样使用, 不能触发自动对齐 —— 否则 `--imu-align-s 0`
    # 会让本路径 IMU 平移 compute_auto_align()(本会话 +0.357s), 而 db3 路径是 0,
    # 两路 IMU-图像相对时间错位 357ms, VINS 尺度估计爆炸(实测 6/6 闭环 1.5-3m)。
    # type=float 使 "auto" 不可达, 与 db3 一致 (实际恒为数值)。
    epoch_minus_mono = load_epoch_minus_mono(session)
    align_s = args.imu_align_s
    if str(args.imu_align_s).lower() == "auto":
        align_s = compute_auto_align(session)
        if align_s != 0.0:
            print(f"[replay] 自动对齐: IMU 平移 {align_s:+.3f}s (补偿 warmup)", flush=True)
    else:
        align_s = float(args.imu_align_s)
    print(f"[replay] epoch-monotonic 偏移: {epoch_minus_mono:.6f}s", flush=True)

    imu_it = iter(imu_event_iter(session, epoch_minus_mono,
                                 args.imu_shift_ms + align_s * 1000.0))

    # 图像迭代器: 取 sidecar 时间戳(秒)排序合并三流
    def img_events():
        raw_dir = args.raw_dir
        defs = [("ir_left", ir_file("ir_left"), "gray", GRAY_BYTES, ts["ir_left"]),
                ("ir_right", ir_file("ir_right"), "gray", GRAY_BYTES, ts["ir_right"])]
        # 生成器表达式按引用捕获外层循环变量 key: 循环结束后 key 恒为最后一值
        # ("ir_right"), 两流帧全被标成右流 -> cam0 一帧收不到 (VINS stereo 同步 0 位姿,
        # cam1 单流 2x 流量触发 FastRTPS "context is invalid" 崩溃)。用默认参数按值捕获。
        def make_gen(key, frames):
            return ((t / 1000.0, key, b) for t, b in frames)
        gens = []
        for key, path, pix, nbytes, tmap in defs:
            raw_file = (raw_dir / f"{key}.raw") if raw_dir else None
            frames = mp4_frame_iter(path, pix, nbytes, list(tmap.values()), raw_file)
            gens.append(make_gen(key, frames))
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
    from sensor_msgs.msg import Image as RosImage, Imu as RosImu
    from builtin_interfaces.msg import Time as RosTime

    # 图像默认 QoS (RELIABLE KeepLast10): 与 replay_db3_to_ros2.py 完全一致。
    # 实测 (2026-08-10): db3 回放 RELIABLE 发布本机 FastRTPS 下 VINS 正常收到
    # (181 位姿); 早期 BEST_EFFORT 反而让 C++ message_filters 收不到图像。
    rclpy.init()
    node = Node("mp4_replay")
    pub_cam0 = node.create_publisher(RosImage, "/cam0/image_raw", 10)
    pub_cam1 = node.create_publisher(RosImage, "/cam1/image_raw", 10)
    pub_imu = node.create_publisher(RosImu, "/imu0", 2000)

    def to_stamp(t_epoch: float) -> RosTime:
        sec = int(t_epoch)
        return RosTime(sec=sec, nanosec=int((t_epoch - sec) * 1e9))

    n_img = n_imu = 0
    t0_data = None
    t0_wall = None
    t_skip_end = None

    import array

    # 预分配消息 + 原地改写 .data: 避免每次 msg.data = X 触发 rclpy setter 的
    # O(N) 全元素校验 (32ms/帧 -> 整条回放 0.5x, 拖慢 VINS 送达节奏致发散)。
    # array slice 拷贝实测 ~4300fps, 60fps 实时无压力 (db3 回放反序列化走
    # C 快速路径天然规避; 这里显式等价)。
    msg0 = RosImage()
    msg0.header.frame_id = "cam0"
    msg0.height, msg0.width, msg0.step = H, W, W
    msg0.is_bigendian = False
    msg0.encoding = "mono8"
    msg0.data = array.array("B", [0]) * GRAY_BYTES

    msg1 = RosImage()
    msg1.header.frame_id = "cam1"
    msg1.height, msg1.width, msg1.step = H, W, W
    msg1.is_bigendian = False
    msg1.encoding = "mono8"
    msg1.data = array.array("B", [0]) * GRAY_BYTES

    def publish_img(t, key, buf):
        nonlocal n_img
        msg = msg0 if key == "ir_left" else msg1
        msg.header.stamp = to_stamp(t)
        msg.data[:] = array.array("B", buf)   # 原地拷贝, 不触发 setter
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
