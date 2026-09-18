#!/usr/bin/env python3
"""Build per-frame camera rotation priors from the UMI 400 Hz IMU."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from align_mast3r_scale_with_stereo import load_stereo_calibration
from fuse_mast3r_stereo_imu import (
    body_t_color_from_stereo_report,
    camera_epoch_to_monotonic,
    integrate_gyro,
    load_calibrated_imu,
    load_vins_config,
)
from prepare_mast3r_slam_dataset import select_db3


def camera_rotation_priors(
    visual_times_mono: np.ndarray,
    imu_times: np.ndarray,
    gyro_body: np.ndarray,
    body_from_camera: Rotation,
    td_s: float,
) -> list[Rotation]:
    priors = [Rotation.identity()]
    for start_s, end_s in zip(visual_times_mono[:-1], visual_times_mono[1:]):
        body_delta = integrate_gyro(
            imu_times, gyro_body, start_s + td_s, end_s + td_s
        )
        priors.append(body_from_camera.inv() * body_delta * body_from_camera)
    return priors


def body_t_camera_for_stream(
    body_t_left_ir: np.ndarray, factory: dict, stream: str
) -> np.ndarray:
    if stream == "infrared_left":
        return body_t_left_ir
    if stream != "color":
        raise ValueError(f"unsupported camera stream: {stream}")
    stereo_report = {
        "observation_frame": "color_camera_i",
        "factory_stereo_calibration": {
            "color_rotation_from_left": factory[
                "color_rotation_from_left"
            ].tolist(),
            "color_translation_from_left_m": factory[
                "color_translation_from_left_m"
            ].tolist(),
        },
    }
    return body_t_color_from_stereo_report(body_t_left_ir, stereo_report)


def generate(
    session: Path,
    dataset: Path,
    stream: str,
    vins_config: Path,
    imu_calibration: Path,
    expected_td_s: float,
) -> dict:
    frame_rows = list(
        csv.DictReader((dataset / "frames.csv").open(newline="", encoding="utf-8"))
    )
    if len(frame_rows) < 2:
        raise ValueError("MASt3R dataset has fewer than two frames")
    epoch_times = np.asarray([float(row["t_sec"]) for row in frame_rows])
    visual_times_mono = camera_epoch_to_monotonic(
        session / "d405_frames.csv", stream, epoch_times
    )
    imu_times, gyro, _, imu_info = load_calibrated_imu(
        session / "external_imu" / "imu.bin", imu_calibration
    )
    runtime = load_vins_config(vins_config, expected_td_s)
    factory = load_stereo_calibration(select_db3(session))
    body_t_camera = body_t_camera_for_stream(
        runtime["body_T_camera"], factory, stream
    )
    priors = camera_rotation_priors(
        visual_times_mono,
        imu_times,
        gyro,
        Rotation.from_matrix(body_t_camera[:3, :3]),
        runtime["td_s"],
    )
    output = dataset / "imu_rotation_priors.csv"
    with output.open("w", newline="", encoding="utf-8") as stream_file:
        writer = csv.DictWriter(
            stream_file,
            fieldnames=("input_index", "qx", "qy", "qz", "qw", "angle_deg"),
        )
        writer.writeheader()
        for index, prior in enumerate(priors):
            quaternion = prior.as_quat()
            writer.writerow(
                {
                    "input_index": index,
                    "qx": f"{quaternion[0]:.12f}",
                    "qy": f"{quaternion[1]:.12f}",
                    "qz": f"{quaternion[2]:.12f}",
                    "qw": f"{quaternion[3]:.12f}",
                    "angle_deg": f"{np.degrees(prior.magnitude()):.9f}",
                }
            )
    angles = np.degrees([prior.magnitude() for prior in priors])
    report = {
        "schema": "umi_mast3r_imu_rotation_priors_v1",
        "result": "PASS",
        "slam_supervision": False,
        "inputs": "D405 exposure timestamps + calibrated onboard 400Hz IMU only",
        "camera_stream": stream,
        "camera_extrinsic_policy": (
            "docker2_body_T_left_ir"
            if stream == "infrared_left"
            else "docker2_body_T_left_ir_composed_with_factory_left_ir_to_color"
        ),
        "frames": int(len(priors)),
        "td_s": runtime["td_s"],
        "estimate_td": runtime["estimate_td"],
        "median_rate_hz": imu_info["median_rate_hz"],
        "rotation_prior_p95_deg": float(np.percentile(angles[1:], 95)),
        "rotation_prior_max_deg": float(np.max(angles[1:])),
        "output": str(output.resolve()),
        "external_ground_truth_used": False,
    }
    (dataset / "imu_rotation_priors_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--stream", choices=("color", "infrared_left"), default="color")
    parser.add_argument("--vins-config", type=Path, required=True)
    parser.add_argument("--imu-calibration", type=Path, required=True)
    parser.add_argument("--expected-td-s", type=float, required=True)
    args = parser.parse_args()
    report = generate(
        args.session.resolve(),
        args.dataset.resolve(),
        args.stream,
        args.vins_config.resolve(),
        args.imu_calibration.resolve(),
        args.expected_td_s,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
