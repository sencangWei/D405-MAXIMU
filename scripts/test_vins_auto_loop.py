#!/usr/bin/env python3
"""Run VINS plus automatic visual loop closure on one recorded session.

The runner never receives or applies an endpoint constraint. Ground-truth facts such
as "the rig returned to the origin" are deliberately kept outside the algorithm and
may only be used after the run for scoring.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(
    "/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/"
    "d405_stereo_imu_config.yaml"
)


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGINT)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def write_rows(path: Path, rows: list[list[float]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--skip-s", type=float, default=1.5)
    parser.add_argument("--imu-shift-ms", type=float, default=0.0)
    parser.add_argument("--timeout-s", type=float, default=420.0)
    parser.add_argument("--duration-s", type=float, default=0.0)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    loop_output = args.out_dir / "loop_output"
    loop_output.mkdir(exist_ok=True)
    (loop_output / "pose_graph").mkdir(exist_ok=True)

    config_text = args.config.read_text(encoding="utf-8")
    config_text = config_text.replace(
        'output_path: "/home/robot/vins_output/"',
        f'output_path: "{loop_output}/"',
    ).replace(
        'pose_graph_save_path: "/home/robot/vins_output/pose_graph/"',
        f'pose_graph_save_path: "{loop_output}/pose_graph/"',
    ).replace(
        "save_image: 0",
        "save_image: 1",
    )
    run_config = args.out_dir / "vins_auto_loop_config.yaml"
    run_config.write_text(config_text, encoding="utf-8")
    for calibration_name in ("left.yaml", "right.yaml"):
        source = args.config.parent / calibration_name
        if not source.is_file():
            raise FileNotFoundError(f"missing camera calibration: {source}")
        shutil.copy2(source, args.out_dir / calibration_name)

    vins_log_path = args.out_dir / "vins.log"
    loop_log_path = args.out_dir / "auto_loop.log"
    replay_log_path = args.out_dir / "replay.log"
    processes: list[subprocess.Popen[bytes]] = []

    rclpy.init()
    node = Node("auto_loop_trajectory_sink")
    raw_rows: list[list[float]] = []
    corrected_rows: list[list[float]] = []

    def append_pose(rows: list[list[float]], message: Odometry) -> None:
        pose = message.pose.pose
        rows.append([
            message.header.stamp.sec + message.header.stamp.nanosec * 1e-9,
            pose.position.x,
            pose.position.y,
            pose.position.z,
            pose.orientation.w,
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
        ])

    node.create_subscription(Odometry, "/odometry", lambda msg: append_pose(raw_rows, msg), 2000)
    node.create_subscription(
        Odometry, "/odometry_rect", lambda msg: append_pose(corrected_rows, msg), 2000
    )

    try:
        with vins_log_path.open("wb") as vins_log, loop_log_path.open("wb") as loop_log:
            vins = subprocess.Popen(
                [
                    "ros2", "run", "vins_fusion_ros2", "vins_fusion_ros2_node",
                    "--ros-args", "-p", "use_sim_time:=false",
                    "-p", f"config_file:={run_config}",
                ],
                stdout=vins_log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            processes.append(vins)
            loop = subprocess.Popen(
                [
                    "stdbuf", "-oL", "-eL", "ros2", "run", "vins_fusion_ros2",
                    "loop_fusion_node", str(run_config),
                ],
                stdout=loop_log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            processes.append(loop)
            time.sleep(6)
            if vins.poll() is not None or loop.poll() is not None:
                raise RuntimeError("VINS or loop-fusion node exited during startup")

            with replay_log_path.open("wb") as replay_log:
                replay = subprocess.Popen(
                    [
                        "python3", str(ROOT / "scripts/replay_db3_to_ros2.py"),
                        "--session", str(args.session), "--mode", "stereo",
                        "--rate", str(args.rate), "--skip-s", str(args.skip_s),
                        "--imu-align-s", "0", "--imu-shift-ms", str(args.imu_shift_ms),
                        "--duration-s", str(args.duration_s),
                    ],
                    cwd=ROOT,
                    stdout=replay_log,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid,
                )
                processes.append(replay)
                deadline = time.monotonic() + args.timeout_s
                while replay.poll() is None and time.monotonic() < deadline:
                    if vins.poll() is not None or loop.poll() is not None:
                        raise RuntimeError("VINS or loop-fusion node exited during replay")
                    rclpy.spin_once(node, timeout_sec=0.05)
                if replay.poll() is None:
                    raise TimeoutError(f"replay exceeded {args.timeout_s:.0f}s")
                if replay.returncode != 0:
                    raise RuntimeError(f"replay failed with exit code {replay.returncode}")

            quiet_since = time.monotonic()
            last_count = len(corrected_rows)
            drain_deadline = time.monotonic() + 20
            while time.monotonic() < drain_deadline:
                rclpy.spin_once(node, timeout_sec=0.05)
                if len(corrected_rows) != last_count:
                    last_count = len(corrected_rows)
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since >= 6:
                    break
    except Exception as exc:
        print(f"FAIL: {exc}")
        return_code = 2
    else:
        return_code = 0
    finally:
        for process in reversed(processes):
            stop_process(process)
        write_rows(args.out_dir / "vio_raw.csv", raw_rows)
        write_rows(args.out_dir / "vio_corrected_stream.csv", corrected_rows)
        node.destroy_node()
        rclpy.shutdown()

    loop_log = loop_log_path.read_text(errors="replace") if loop_log_path.exists() else ""
    accepted = [line for line in loop_log.splitlines() if "[AUTO_LOOP_ACCEPT]" in line]
    rejected = [line for line in loop_log.splitlines() if "[AUTO_LOOP_REJECT]" in line]
    print(f"raw odometry: {len(raw_rows)} samples")
    print(f"corrected odometry: {len(corrected_rows)} samples")
    print(f"automatic loop accepts: {len(accepted)}")
    for line in accepted:
        print(line)
    print(f"automatic loop rejects after geometry: {len(rejected)}")
    print(f"keyframe trajectory: {loop_output / 'vio_loop.csv'}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
