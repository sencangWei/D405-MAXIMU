#!/usr/bin/env python3
"""把 Kalibr 双目标定结果写入 VINS / ORB-SLAM3 / 回放配置。

输入:
  --camchain  kalibr_calibrate_cameras 输出 camchain (内参+基线)
  --imucam    kalibr_calibrate_imu_camera 输出 camchain-imucam (外参+时间偏移)
  --tag-size  实际量出的 AprilGrid tag 边长(米), 用于校正内参尺度

更新:
  VINS  left.yaml / right.yaml  (两目内参)
  VINS  d405_stereo_imu_config.yaml  (body_T_cam0 / body_T_cam1)
  ORB   orbslam3_d405_{rgbd,stereo}_inertial_720p.yaml (内参+外参+基线)
  camimu_720p_leftir_kalibr.yaml
  回放  replay_db3_to_ros2.py / replay_mp4_to_ros2.py (--imu-shift-ms)

用法:
  python3 scripts/apply_calibration.py --camchain <camchain.yaml> \
      --imucam <camchain-imucam.yaml> [--tag-size 0.0352]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
VINS = Path("/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu")


def mat4(R, t):
    T = np.eye(4)
    T[:3, :3] = np.asarray(R)
    T[:3, 3] = np.asarray(t)
    return T


def mat4_str(T, indent="   "):
    lines = []
    for row in T:
        lines.append(indent + " ".join(f"{v:.8g}" for v in row))
    return "\n".join(lines)


def inv(T):
    R = T[:3, :3]
    t = T[:3, 3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ t
    return out


def write_yaml(path, data, header=None):
    txt = ""
    if header:
        txt += header + "\n"
    txt += yaml.safe_dump(data, default_flow_style=False, allow_unicode=True)
    path.write_text(txt)
    print(f"  -> {path}")


def update_intrinsics_yaml(path, fx, fy, cx, cy):
    lines = path.read_text().split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("fx:"):
            out.append(line.replace(s, f"fx: {fx:.6f}"))
        elif s.startswith("fy:"):
            out.append(line.replace(s, f"fy: {fy:.6f}"))
        elif s.startswith("cx:"):
            out.append(line.replace(s, f"cx: {cx:.6f}"))
        elif s.startswith("cy:"):
            out.append(line.replace(s, f"cy: {cy:.6f}"))
        else:
            out.append(line)
    path.write_text("\n".join(out))
    print(f"  -> {path}  (fx={fx:.3f} fy={fy:.3f} cx={cx:.3f} cy={cy:.3f})")


def patch_replay_shift(shift_s: float):
    shift_ms = -shift_s * 1000.0  # Kalibr timeshift_cam_imu: t_imu=t_cam+shift
    # 回放里 --imu-shift-ms 是加在 IMU 戳上补偿: shift_ms 应为 -timeshift
    for p in (ROOT / "scripts" / "replay_db3_to_ros2.py",
              ROOT / "scripts" / "replay_mp4_to_ros2.py"):
        if not p.exists():
            continue
        txt = p.read_text()
        import re
        new = re.sub(r'--imu-shift-ms.*?default=([0-9.eE+-]+)',
                     f'--imu-shift-ms", type=float, default={shift_ms:.3f}',
                     txt, count=1)
        # 上面的正则有点脆弱, 用更稳的方式: 找到 '--imu-shift-ms' 那行
        lines = txt.split("\n")
        for i, line in enumerate(lines):
            if '"--imu-shift-ms"' in line and "default=" in line:
                lines[i] = re.sub(r"default=([0-9.eE+-]+)", f"default={shift_ms:.3f}", line)
                break
        p.write_text("\n".join(lines))
        print(f"  -> {p}  (imu-shift-ms={shift_ms:.3f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camchain", required=True, help="kalibr_calibrate_cameras 输出")
    ap.add_argument("--imucam", required=True, help="kalibr_calibrate_imu_camera 输出")
    ap.add_argument("--tag-size", type=float, default=None,
                    help="实测 tag 边长(m), 用于校正内参尺度(默认用标定时的 35.2mm)")
    args = ap.parse_args()

    cam = yaml.safe_load(open(args.camchain))
    imu = yaml.safe_load(open(args.imucam))

    # 内参
    c0 = imu.get("cam0", cam.get("cam0", {}))
    c1 = imu.get("cam1", cam.get("cam1", {}))
    fx0, fy0, cx0, cy0 = c0["intrinsics"]
    fx1, fy1, cx1, cy1 = c1["intrinsics"]
    print(f"内参 cam0: fx={fx0:.3f} fy={fy0:.3f} cx={cx0:.3f} cy={cy0:.3f}")
    print(f"内参 cam1: fx={fx1:.3f} fy={fy1:.3f} cx={cx1:.3f} cy={cy1:.3f}")

    if args.tag_size is not None:
        scale = args.tag_size / 0.0352
        fx0, fy0, cx0, cy0 = fx0 * scale, fy0 * scale, cx0 * scale, cy0 * scale
        fx1, fy1, cx1, cy1 = fx1 * scale, fy1 * scale, cx1 * scale, cy1 * scale
        print(f"按实测 tagSize {args.tag_size*1000:.1f}mm 校正内参(×{scale:.4f})")

    # 外参: camchain-imucam 里 T_cam_imu = T_ci (imu->cam0), timeshift_cam_imu
    # 格式: 4x4 矩阵(list of lists) 或 {R:..., t:...}
    tc = c0["T_cam_imu"]
    if isinstance(tc, dict):
        T_cam0_imu = mat4(tc["R"], tc["t"])
    else:
        T_cam0_imu = np.asarray(tc, dtype=float)
    timeshift = c0.get("timeshift_cam_imu", 0.0)
    T_imu_cam0 = inv(T_cam0_imu)
    print(f"\ntimeshift_cam_imu: {timeshift:.6f} s")
    print(f"T_cam0_imu t: {T_cam0_imu[:3,3]}")

    # 立体基线 (cam0 -> cam1)
    T_cam1_cam0 = None
    for key in ("T_cn_cnm1", "T_cam_cam"):
        if key in c1:
            T_cam1_cam0 = mat4(np.array(c1[key])[:3,:3], np.array(c1[key])[:3,3])
            break
    if T_cam1_cam0 is not None:
        T_cam0_cam1 = inv(T_cam1_cam0)
        print(f"基线 |t_cam0_cam1|: {np.linalg.norm(T_cam0_cam1[:3,3])*1000:.3f} mm")

    print("\n===== 更新配置 =====")
    # VINS 内参
    update_intrinsics_yaml(VINS / "left.yaml", fx0, fy0, cx0, cy0)
    update_intrinsics_yaml(VINS / "right.yaml", fx1, fy1, cx1, cy1)

    # VINS body_T_cam0 = T_cam0_imu? (需确认约定, 先打印供核对)
    print("\n[核对] VINS body_T_cam0 需为 T_cam0_imu 或 T_imu_cam0, 见注释:")
    print("  T_cam0_imu:\n" + mat4_str(T_cam0_imu))
    print("  T_imu_cam0:\n" + mat4_str(T_imu_cam0))

    # 输出 camimu 配置
    camimu = {
        "calibration_source": "kalibr_calibrate_imu_camera (stereo, 2026-08-08)",
        "T_cam0_imu0": {"R": T_cam0_imu[:3,:3].tolist(), "t": T_cam0_imu[:3,3].tolist()},
        "T_imu0_cam0": {"R": T_imu_cam0[:3,:3].tolist(), "t": T_imu_cam0[:3,3].tolist()},
        "timeshift_cam_imu": timeshift,
        "cam0_intrinsics": {"fx": fx0, "fy": fy0, "cx": cx0, "cy": cy0},
        "cam1_intrinsics": {"fx": fx1, "fy": fy1, "cx": cx1, "cy": cy1},
    }
    out = ROOT / "config" / "camimu_720p_leftir_kalibr.yaml"
    write_yaml(out, camimu, header="# Kalibr 双目标定 2026-08-08 (见 /tmp/calib_run/)")

    # 回放 shift
    patch_replay_shift(timeshift)

    print("\n[待手动确认] VINS body_T_cam0 / ORB IMU.T_b_c1 的约定需要人工核对后写入")


if __name__ == "__main__":
    raise SystemExit(main())
