#!/usr/bin/env python3
"""把 ego_vio 录制数据转成 Kalibr 可用的 ROS1 bag。

录制目录结构(由 collect_calib_data.py 生成):
  recordings/calib_xxx/left_hand/
      frames/000001.jpg
      imu.bin
      camera_ts.csv
      imu_ts.csv

输出:
  calib.bag
    /cam0/image_raw   sensor_msgs/Image
    /cam0/camera_info sensor_msgs/CameraInfo
    /imu0             sensor_msgs/Imu

用法:
  python scripts/convert_to_kalibr_bag.py --input recordings/calib_20260725_xxxx --output calib.bag
"""
import argparse
import csv
import struct
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def read_imu_bin(path: Path):
    """读取 imu.bin, 返回 [(ts, counter, gx, gy, gz, ax, ay, az, temp), ...]。"""
    IMU_PACK_FMT = "<dI7f"
    IMU_PACK_SIZE = struct.calcsize(IMU_PACK_FMT)
    samples = []
    with open(path, "rb") as f:
        while True:
            chunk = f.read(IMU_PACK_SIZE)
            if len(chunk) < IMU_PACK_SIZE:
                break
            ts, counter, gx, gy, gz, ax, ay, az, temp = struct.unpack(IMU_PACK_FMT, chunk)
            samples.append({
                "ts": ts,
                "counter": counter,
                "gx": gx, "gy": gy, "gz": gz,
                "ax": ax, "ay": ay, "az": az,
                "temp": temp,
            })
    return samples


def read_camera_ts(path: Path):
    """读取 camera_ts.csv, 返回 [(idx, frame_number, ts_mono), ...]。

    兼容旧格式(无 frame_number 列): 旧格式返回 frame_number=idx(连续,
    无丢帧检测能力, 拟合去抖仍然可用)。
    """
    raw_rows = []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        has_fnum = "frame_number" in (reader.fieldnames or [])
        for r in reader:
            raw_rows.append((int(r["idx"]), float(r["ts_mono"]), r))

    if has_fnum:
        return [
            (idx, int(row["frame_number"]), stamp)
            for idx, stamp, row in raw_rows
        ]
    if len(raw_rows) < 2:
        return [(idx, idx, stamp) for idx, stamp, _ in raw_rows]

    # The legacy runtime recorder did not save the RealSense frame number.
    # Reconstruct it from timestamp gaps so a dropped camera frame remains a
    # gap instead of compressing the entire camera timeline.
    stamps = np.asarray([stamp for _, stamp, _ in raw_rows], dtype=np.float64)
    positive_dt = np.diff(stamps)
    positive_dt = positive_dt[positive_dt > 0.0]
    short_intervals = positive_dt[
        positive_dt <= np.percentile(positive_dt, 60.0)
    ]
    nominal_period = float(np.median(short_intervals))
    frame_numbers = [raw_rows[0][0]]
    for delta in np.diff(stamps):
        step = max(1, int(round(float(delta) / nominal_period)))
        frame_numbers.append(frame_numbers[-1] + step)
    return [
        (idx, frame_number, stamp)
        for (idx, stamp, _), frame_number in zip(raw_rows, frame_numbers)
    ]


def read_imu_arrival_timestamps(imu_csv: Path, camera_csv: Path, counters):
    """Load the raw host-arrival clock used to fit IMU sample time.

    New recordings contain ``rx_mono`` directly.  Older recordings contain
    only the writer's wall time; align that clock to monotonic time using the
    camera CSV, which records both clocks for every frame.
    """
    with open(imu_csv, "r", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != len(counters):
        raise ValueError(
            f"IMU CSV/bin count mismatch: csv={len(rows)}, bin={len(counters)}"
        )
    csv_counters = [int(row["counter"]) for row in rows]
    if csv_counters != list(counters):
        raise ValueError("IMU CSV/bin counters do not match")

    fields = rows[0].keys() if rows else ()
    if "rx_mono" in fields:
        return [float(row["rx_mono"]) for row in rows], "rx_mono"
    if "ts_wall" not in fields:
        return None, "stored_ts"

    with open(camera_csv, "r", newline="") as f:
        camera_rows = list(csv.DictReader(f))
    clock_offsets = [
        float(row["ts_wall"]) - float(row["ts_mono"])
        for row in camera_rows
        if row.get("ts_wall") and row.get("ts_mono")
    ]
    if not clock_offsets:
        return None, "stored_ts"
    wall_minus_mono = float(np.median(clock_offsets))
    return [float(row["ts_wall"]) - wall_minus_mono for row in rows], "legacy_wall"


def select_imu_timestamp_fit(imu_samples, arrival_ts, arrival_source):
    """Choose the least-jittery valid counter fit for this recording."""
    from ego_vio.imu.imu_reader import fit_counter_timestamps

    counters = [sample["counter"] for sample in imu_samples]
    candidates = [("stored_ts", [sample["ts"] for sample in imu_samples])]
    if arrival_ts is not None:
        candidates.append((arrival_source, arrival_ts))

    fits = []
    for source, timestamps in candidates:
        fitted, info = fit_counter_timestamps(timestamps, counters)
        rate = float(info["rate_hz"])
        if 350.0 <= rate <= 450.0:
            fits.append((float(info["sigma_ms"]), source, fitted, info))
    if not fits:
        raise ValueError("所有 IMU 时间戳候选都无法拟合到合理的采样率")
    _, source, fitted, info = min(fits, key=lambda item: item[0])
    return fitted, info, source


def build_header(stamp: float, frame_id: str, seq: int = 0):
    from rosbags.typesys import Stores, get_typestore
    ts = get_typestore(Stores.ROS1_NOETIC)
    Header = ts.types["std_msgs/msg/Header"]
    Time = ts.types["builtin_interfaces/msg/Time"]
    sec = int(stamp)
    nsec = int((stamp - sec) * 1e9)
    return Header(seq=seq, stamp=Time(sec=sec, nanosec=nsec), frame_id=frame_id)


def build_camera_info(width: int, height: int, fx: float, fy: float, cx: float, cy: float):
    """构造 CameraInfo 消息。Kalibr 会优化这些初值。"""
    from rosbags.typesys import Stores, get_typestore
    ts = get_typestore(Stores.ROS1_NOETIC)
    CameraInfo = ts.types["sensor_msgs/msg/CameraInfo"]
    RegionOfInterest = ts.types["sensor_msgs/msg/RegionOfInterest"]
    roi = RegionOfInterest(x_offset=0, y_offset=0, height=0, width=0, do_rectify=False)
    return CameraInfo(
        header=build_header(0.0, "cam0"),
        height=height,
        width=width,
        distortion_model="plumb_bob",
        D=np.array([0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64),
        K=np.array([fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0], dtype=np.float64),
        R=np.array([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0], dtype=np.float64),
        P=np.array([fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0], dtype=np.float64),
        binning_x=0,
        binning_y=0,
        roi=roi,
    )


def downsample_camera_rows(cam_rows, max_freq: float):
    """按拟合后的时间戳限制相机频率; 0 表示保留全部帧。"""
    if max_freq <= 0.0 or len(cam_rows) < 2:
        return cam_rows
    min_period = 1.0 / max_freq
    selected = [cam_rows[0]]
    last_ts = cam_rows[0][2]
    for row in cam_rows[1:]:
        if row[2] - last_ts >= min_period - 1e-9:
            selected.append(row)
            last_ts = row[2]
    return selected


def convert(input_dir: Path, output: Path, unit: str = "left_hand",
            fx: float = 600.0, fy: float = 600.0, cx: float = None, cy: float = None,
            cam_offset: float = 0.0, cam_freq: float = 0.0,
            mono: bool = False):
    unit_dir = input_dir / unit
    if not unit_dir.exists():
        raise FileNotFoundError(f"找不到单元目录: {unit_dir}")

    imu_samples = read_imu_bin(unit_dir / "imu.bin")
    cam_rows = read_camera_ts(unit_dir / "camera_ts.csv")

    if not imu_samples:
        raise ValueError("没有 IMU 数据")
    if not cam_rows:
        raise ValueError("没有相机数据")

    # IMU 时间戳去抖: 必须从原始主机到达时刻拟合。旧版曾对已经在线
    # 拟合过的 s.ts 再拟合，启动 USB 积压留下的相位误差会被永久保留。
    arrival_ts, arrival_source = read_imu_arrival_timestamps(
        unit_dir / "imu_ts.csv",
        unit_dir / "camera_ts.csv",
        [s["counter"] for s in imu_samples],
    )
    # 用 400Hz 硬件 counter 拟合, 消除串口接收抖动。旧 runtime 的
    # ts_wall 是写盘线程时刻，磁盘繁忙时会比 imu.bin 内的在线拟合时间
    # 差很多，因此比较候选残差后自动选择，而不是盲信 CSV。
    # 拟合残差 σ 是质量指标 —— σ>2ms 说明串口链路有问题, 不要用这份数据标定。
    fitted, info, selected_source = select_imu_timestamp_fit(
        imu_samples, arrival_ts, arrival_source
    )
    for s, fts in zip(imu_samples, fitted):
        s["ts"] = float(fts)
    print(f"IMU 时间基准: {selected_source}")
    print(f"IMU 去抖: 实测 {info['rate_hz']:.1f}Hz, 拟合残差 σ={info['sigma_ms']:.2f}ms, "
          f"离群剔除 {info['outliers']}")
    if info["sigma_ms"] > 2.0:
        print("!! IMU 接收抖动过大(>2ms), 数据质量不合格, 建议检查串口链路后重采")

    print(f"IMU samples: {len(imu_samples)}")
    print(f"Camera frames: {len(cam_rows)}")

    # 相机时间戳去抖: 用设备帧序号对到达时刻做鲁棒拟合(与 IMU counter 同一原理)。
    # Windows 上 RealSense 拿不到 metadata(系统级限制, global_time 无效),
    # 帧序号是唯一可靠的设备侧时钟; 丢帧会跳号, 拟合天然容忍。
    from ego_vio.imu.imu_reader import fit_counter_timestamps
    cam_fitted, cinfo = fit_counter_timestamps(
        [r[2] for r in cam_rows], [r[1] for r in cam_rows]
    )
    fnums = [r[1] for r in cam_rows]
    cam_drops = sum(max(0, b - a - 1) for a, b in zip(fnums, fnums[1:]))
    cam_rows = [(r[0], r[1], float(fts)) for r, fts in zip(cam_rows, cam_fitted)]
    print(f"相机去抖: 实测 {cinfo['rate_hz']:.1f}fps, 送达抖动 σ={cinfo['sigma_ms']:.2f}ms, "
          f"丢帧 {cam_drops}, 离群剔除 {cinfo['outliers']}")
    if cinfo["sigma_ms"] > 8.0:
        print("!! 相机送达抖动过大(>8ms), 建议换主板后置 USB 口/减少 USB 设备后重采")

    original_cam_count = len(cam_rows)
    cam_rows = downsample_camera_rows(cam_rows, cam_freq)
    if cam_freq > 0.0:
        print(f"相机降采样: {original_cam_count} -> {len(cam_rows)} 帧 (上限 {cam_freq:.1f}Hz)")

    # 读第一帧拿图像尺寸
    first_frame_path = unit_dir / "frames" / f"{cam_rows[0][0]:06d}.jpg"
    first_img = cv2.imread(str(first_frame_path))
    if first_img is None:
        raise FileNotFoundError(f"无法读取第一帧: {first_frame_path}")
    h, w = first_img.shape[:2]

    cx = cx if cx is not None else w / 2.0
    cy = cy if cy is not None else h / 2.0

    from rosbags.rosbag1 import Writer
    from rosbags.typesys import Stores, get_typestore

    ts = get_typestore(Stores.ROS1_NOETIC)
    Image = ts.types["sensor_msgs/msg/Image"]
    Imu = ts.types["sensor_msgs/msg/Imu"]
    CameraInfo = ts.types["sensor_msgs/msg/CameraInfo"]

    # 右 IR 帧目录 (双目标定; 不存在则只写 cam0)
    frames_right_dir = input_dir / unit / "frames_right"
    has_stereo = frames_right_dir.exists() and any(frames_right_dir.glob("*.jpg"))

    with Writer(output) as writer:
        conn_cam = writer.add_connection("/cam0/image_raw", "sensor_msgs/msg/Image", typestore=ts)
        conn_info = writer.add_connection("/cam0/camera_info", "sensor_msgs/msg/CameraInfo", typestore=ts)
        conn_imu = writer.add_connection("/imu0", "sensor_msgs/msg/Imu", typestore=ts)
        conn_cam1 = conn_info1 = None
        if has_stereo:
            conn_cam1 = writer.add_connection("/cam1/image_raw", "sensor_msgs/msg/Image", typestore=ts)
            conn_info1 = writer.add_connection("/cam1/camera_info", "sensor_msgs/msg/CameraInfo", typestore=ts)

        # 写 camera_info (只写一次)
        cam_info = build_camera_info(w, h, fx, fy, cx, cy)
        camera_info_stamp = cam_rows[0][2] + cam_offset
        cam_info.header = build_header(camera_info_stamp, "cam0")
        t_ns = int(camera_info_stamp * 1e9)
        writer.write(conn_info, t_ns, bytes(ts.serialize_ros1(cam_info, "sensor_msgs/msg/CameraInfo")))
        if has_stereo:
            cam_info1 = build_camera_info(w, h, fx, fy, cx, cy)
            cam_info1.header = build_header(camera_info_stamp, "cam1")
            writer.write(conn_info1, t_ns,
                         bytes(ts.serialize_ros1(cam_info1, "sensor_msgs/msg/CameraInfo")))

        # 写 IMU
        Quaternion = ts.types["geometry_msgs/msg/Quaternion"]
        Vector3 = ts.types["geometry_msgs/msg/Vector3"]
        for s in imu_samples:
            stamp = s["ts"]
            msg = Imu(
                header=build_header(stamp, "imu0"),
                orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
                orientation_covariance=np.array((-1.0,) + (0.0,) * 8, dtype=np.float64),
                angular_velocity=Vector3(
                    x=np.radians(s["gx"]),
                    y=np.radians(s["gy"]),
                    z=np.radians(s["gz"]),
                ),
                angular_velocity_covariance=np.zeros(9, dtype=np.float64),
                linear_acceleration=Vector3(
                    x=s["ax"] * 9.81,
                    y=s["ay"] * 9.81,
                    z=s["az"] * 9.81,
                ),
                linear_acceleration_covariance=np.zeros(9, dtype=np.float64),
            )
            t_ns = int(stamp * 1e9)
            writer.write(conn_imu, t_ns, bytes(ts.serialize_ros1(msg, "sensor_msgs/msg/Imu")))

        # 写图像 (cam_offset: test_sync 实测相机比 IMU 晚 ~74ms, 填 -0.074 预对齐,
        # 残余偏移由 Kalibr --time-calibration 估计)
        for idx, fnum, ts_mono in cam_rows:
            img_path = unit_dir / "frames" / f"{idx:06d}.jpg"
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            ts_shifted = ts_mono + cam_offset
            if mono:
                image_data = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                encoding = "mono8"
                step = w
            else:
                image_data = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                encoding = "rgb8"
                step = w * 3
            msg = Image(
                header=build_header(ts_shifted, "cam0", idx),
                height=h,
                width=w,
                encoding=encoding,
                is_bigendian=0,
                step=step,
                data=np.frombuffer(image_data.tobytes(), dtype=np.uint8),
            )
            t_ns = int(ts_shifted * 1e9)
            writer.write(conn_cam, t_ns, bytes(ts.serialize_ros1(msg, "sensor_msgs/msg/Image")))
            # 双目标定: 写 cam1 (右 IR, 帧号/时间戳与左目一致)
            if has_stereo:
                img_path1 = frames_right_dir / f"{idx:06d}.jpg"
                img1 = cv2.imread(str(img_path1))
                if img1 is not None:
                    if mono:
                        image_data1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
                        step1 = w
                    else:
                        image_data1 = cv2.cvtColor(img1, cv2.COLOR_BGR2RGB)
                        step1 = w * 3
                    msg1 = Image(
                        header=build_header(ts_shifted, "cam1", idx),
                        height=h,
                        width=w,
                        encoding=encoding,
                        is_bigendian=0,
                        step=step1,
                        data=np.frombuffer(image_data1.tobytes(), dtype=np.uint8),
                    )
                    writer.write(conn_cam1, t_ns,
                                 bytes(ts.serialize_ros1(msg1, "sensor_msgs/msg/Image")))

    print(f"Generated: {output}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="录制目录, 如 recordings/calib_xxx")
    ap.add_argument("--output", default="calib.bag", help="输出 rosbag 路径")
    ap.add_argument("--unit", default="left_hand", help="单元名")
    ap.add_argument("--fx", type=float, default=600.0, help="相机内参初值 fx")
    ap.add_argument("--fy", type=float, default=600.0, help="相机内参初值 fy")
    ap.add_argument("--cx", type=float, default=None, help="相机内参初值 cx(默认图像中心)")
    ap.add_argument("--cy", type=float, default=None, help="相机内参初值 cy(默认图像中心)")
    ap.add_argument("--cam-offset", type=float, default=0.0,
                    help="相机时间戳偏移(秒), 相机比 IMU 晚时填负值, 如 -0.074")
    ap.add_argument("--cam-freq", type=float, default=0.0,
                    help="输出相机帧率上限(Hz), 0=保留全部帧")
    ap.add_argument("--mono", action="store_true",
                    help="图像写为 mono8，适合标定并显著减小 bag")
    args = ap.parse_args()

    convert(
        input_dir=Path(args.input),
        output=Path(args.output),
        unit=args.unit,
        fx=args.fx, fy=args.fy, cx=args.cx, cy=args.cy,
        cam_offset=args.cam_offset,
        cam_freq=args.cam_freq,
        mono=args.mono,
    )


if __name__ == "__main__":
    main()
