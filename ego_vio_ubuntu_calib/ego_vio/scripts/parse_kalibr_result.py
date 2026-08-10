#!/usr/bin/env python3
"""解析 Kalibr 标定结果(camchain.yaml + imu.yaml), 输出 OpenVINS 可用的配置。

用法:
  python scripts/parse_kalibr_result.py --camchain results/calib-camchain.yaml \
                                         --imu results/imu.yaml \
                                         --out config/ov_left_hand.yaml
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
import yaml


def _read_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_camchain(camchain_path: Path) -> dict:
    """解析 Kalibr camchain 文件。"""
    data = _read_yaml(camchain_path)
    cam0 = data.get("cam0", {})

    model = cam0.get("camera_model", "pinhole")
    intrinsics = cam0.get("intrinsics", [0.0] * 4)
    distortion_model = cam0.get("distortion_model", "radtan")
    distortion_coeffs = cam0.get("distortion_coeffs", [0.0] * 4)
    resolution = cam0.get("resolution", [0, 0])

    # intrinsics 顺序因模型而异
    if model == "pinhole":
        fx, fy, cx, cy = intrinsics
    elif model == "omni":
        xi, fx, fy, cx, cy = intrinsics
        model = "omni"
    else:
        fx, fy, cx, cy = intrinsics[:4]

    # T_cam_imu: 4x4 matrix in camchain
    T_cam_imu = cam0.get("T_cam_imu", None)
    # timeshift_cam_imu: seconds (camera = imu + timeshift)
    timeshift = cam0.get("timeshift_cam_imu", 0.0)

    return {
        "camera_model": model,
        "distortion_model": distortion_model,
        "fx": fx, "fy": fy, "cx": cx, "cy": cy,
        "distortion_coeffs": distortion_coeffs,
        "resolution": resolution,
        "T_cam_imu": T_cam_imu,
        "timeshift_cam_imu": timeshift,
    }


def _parse_imu(imu_path: Path) -> dict:
    """解析 Kalibr imu 结果文件。"""
    data = _read_yaml(imu_path)
    imu0 = data.get("imu0", {})
    return {
        "update_rate": imu0.get("update_rate", 400.0),
        "gyroscope_noise_density": imu0.get("gyroscope_noise_density", 1e-4),
        "gyroscope_random_walk": imu0.get("gyroscope_random_walk", 1e-6),
        "accelerometer_noise_density": imu0.get("accelerometer_noise_density", 1e-3),
        "accelerometer_random_walk": imu0.get("accelerometer_random_walk", 1e-4),
        "T_i_b": imu0.get("T_i_b", None),  # IMU 到 body, 通常单位阵
    }


def _to_openvins(cam: dict, imu: dict) -> dict:
    """转成 OpenVINS 配置格式。"""
    T_cam_imu = np.array(cam["T_cam_imu"]) if cam["T_cam_imu"] else np.eye(4)
    # OpenVINS 要的是 T_imu_cam (IMU 到相机, 或相机在 IMU 坐标系下的位姿)
    # 注意不同 OpenVINS 版本约定不同; 这里按常见约定: T_Cl_Cam = T_cam_imu 的逆
    T_imu_cam = np.linalg.inv(T_cam_imu)

    return {
        "imu": {
            "accelerometer_noise_density": imu["accelerometer_noise_density"],
            "accelerometer_random_walk": imu["accelerometer_random_walk"],
            "gyroscope_noise_density": imu["gyroscope_noise_density"],
            "gyroscope_random_walk": imu["gyroscope_random_walk"],
            "rate_hz": imu["update_rate"],
            "time_offset": -cam["timeshift_cam_imu"],  # OpenVINS: t_imu = t_cam + offset
        },
        "camera": {
            "camera_model": cam["camera_model"],
            "distortion_model": cam["distortion_model"],
            "resolution": cam["resolution"],
            "intrinsics": [cam["fx"], cam["fy"], cam["cx"], cam["cy"]],
            "distortion_coeffs": cam["distortion_coeffs"],
            "T_cam_imu": T_cam_imu.tolist(),
            "T_imu_cam": T_imu_cam.tolist(),
        },
    }


def print_summary(cam: dict, imu: dict):
    print("=" * 56)
    print("Kalibr 标定结果摘要")
    print("=" * 56)
    print(f"相机模型: {cam['camera_model']}")
    print(f"畸变模型: {cam['distortion_model']}")
    print(f"分辨率:   {cam['resolution']}")
    print(f"内参:     fx={cam['fx']:.3f} fy={cam['fy']:.3f} cx={cam['cx']:.3f} cy={cam['cy']:.3f}")
    print(f"畸变:     {cam['distortion_coeffs']}")
    print(f"相机-IMU 时间偏移: {cam['timeshift_cam_imu']*1000:.2f} ms (camera = imu + offset)")
    if cam['T_cam_imu']:
        T = np.array(cam['T_cam_imu'])
        # 分解平移和旋转欧拉角 (xyz 顺序)
        t = T[:3, 3]
        R = T[:3, :3]
        sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])
        if sy < 1e-6:
            rx = math.atan2(-R[1, 2], R[1, 1])
            ry = math.atan2(-R[2, 0], sy)
            rz = 0
        else:
            rx = math.atan2(R[2, 1], R[2, 2])
            ry = math.atan2(-R[2, 0], sy)
            rz = math.atan2(R[1, 0], R[0, 0])
        print(f"T_cam_imu 平移:    [{t[0]*1000:.2f}, {t[1]*1000:.2f}, {t[2]*1000:.2f}] mm")
        print(f"T_cam_imu 欧拉角:  [{math.degrees(rx):.2f}, {math.degrees(ry):.2f}, {math.degrees(rz):.2f}] deg")
    print(f"IMU 更新率: {imu['update_rate']:.1f} Hz")
    print(f"IMU 陀螺噪声密度:  {imu['gyroscope_noise_density']:.2e} rad/s/sqrt(Hz)")
    print(f"IMU 加计噪声密度:  {imu['accelerometer_noise_density']:.2e} m/s^2/sqrt(Hz)")
    print("=" * 56)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camchain", required=True, help="Kalibr camchain yaml")
    ap.add_argument("--imu", required=True, help="Kalibr imu yaml")
    ap.add_argument("--out", default="config/kalibr_parsed.yaml", help="输出 yaml")
    args = ap.parse_args()

    cam = _parse_camchain(Path(args.camchain))
    imu = _parse_imu(Path(args.imu))
    print_summary(cam, imu)

    ov_cfg = _to_openvins(cam, imu)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(ov_cfg, f, allow_unicode=True, sort_keys=False)
    print(f"已保存 OpenVINS 配置: {out_path.resolve()}")


if __name__ == "__main__":
    main()
