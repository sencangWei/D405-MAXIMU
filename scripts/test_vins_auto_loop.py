#!/usr/bin/env python3
"""Run VINS plus automatic visual loop closure on one recorded session.

The runner never receives or applies an endpoint constraint. Ground-truth facts such
as "the rig returned to the origin" are deliberately kept outside the algorithm and
may only be used after the run for scoring.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

import numpy as np
import rclpy
import yaml
from nav_msgs.msg import Odometry, Path as NavPath
from rclpy.node import Node

from slam_benchmark_environment import (
    capture_environment,
    evaluate_environment,
    find_processes,
    validate_environment_report,
)
from slam_run_health import evaluate_slam_health
from slam_runtime_watchdog import SlamRuntimeWatchdog


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_BUILD = ROOT / ".product_live_build"
DEFAULT_CONFIG = ROOT / "config" / "product_live_stm32" / "vins_config.yaml"
VINS_EXECUTABLE = (
    PRODUCT_BUILD / "vins_ws" / "build" / "vins_fusion_ros2"
    / "vins_fusion_ros2_node"
)
LOOP_EXECUTABLE = (
    PRODUCT_BUILD / "loop_ws" / "build" / "vins_fusion_ros2"
    / "loop_fusion" / "loop_fusion_node"
)
REPLAY_EXECUTABLE = (
    PRODUCT_BUILD / "vins_ws" / "build" / "vins_fusion_ros2"
    / "db3_replay_cpp"
)


def load_accel_calibration(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    accelerometer = data.get("accelerometer") if isinstance(data, dict) else None
    if isinstance(accelerometer, dict):
        matrix = np.asarray(accelerometer.get("matrix"), dtype=float)
        offset_g = np.asarray(accelerometer.get("offset_g"), dtype=float)
        source_format = "legacy_accelerometer_section"
    elif isinstance(data, dict) and data.get("method") == (
        "arbitrary_pose_accelerometer_ellipsoid_with_heldout_validation"
    ):
        if data.get("result") != "PASS":
            raise ValueError("IMU ellipsoid calibration is not PASS")
        matrix = np.asarray(data.get("correction_matrix"), dtype=float)
        bias_m_s2 = np.asarray(data.get("bias_m_s2"), dtype=float)
        if bias_m_s2.shape != (3,):
            raise ValueError("IMU ellipsoid bias must contain 3 values")
        gravity_m_s2 = float(data.get("gravity_m_s2", 9.80665))
        if not np.isfinite(gravity_m_s2) or gravity_m_s2 <= 0.0:
            raise ValueError("IMU ellipsoid gravity must be positive")
        # Ellipsoid report: a_cal = M @ (a_raw*m/s2 - bias) / g.
        # Replay contract: a_cal = M @ a_raw_g + offset_g.
        offset_g = -(matrix @ bias_m_s2) / gravity_m_s2
        source_format = "arbitrary_pose_ellipsoid_report"
    else:
        raise ValueError("IMU calibration lacks a supported accelerometer model")
    if matrix.shape != (3, 3) or offset_g.shape != (3,):
        raise ValueError("accelerometer calibration must be a 3x3 matrix plus 3 offsets")
    if not np.isfinite(matrix).all() or not np.isfinite(offset_g).all():
        raise ValueError("accelerometer calibration contains non-finite values")
    return {
        "path": str(path.resolve()),
        "matrix": matrix.reshape(-1).tolist(),
        "offset_g": offset_g.tolist(),
        "source_format": source_format,
    }


def accel_calibration_replay_arguments(calibration: dict[str, object]) -> list[str]:
    def values(items: object) -> list[str]:
        return [str(float(value)) for value in items]

    return [
        "--imu-accel-matrix",
        *values(calibration["matrix"]),
        "--imu-accel-offset-g",
        *values(calibration["offset_g"]),
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision(repository: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else None


def executable_runtime_library_directories(executable: Path) -> list[Path]:
    """Return library directories belonging to the executable's own build."""
    resolved = executable.resolve()
    for ancestor in (resolved.parent, *resolved.parents):
        if ancestor.name != "vins_fusion_ros2":
            continue
        if ancestor.parent.name == "build":
            return [path for path in (ancestor / "vins", ancestor) if path.is_dir()]
        if ancestor.parent.name == "install":
            library_root = ancestor / "lib"
            return [
                path for path in (library_root / "vins", library_root) if path.is_dir()
            ]
    return []


def executable_runtime_environment(executable: Path) -> dict[str, str]:
    environment = dict(os.environ)
    owned = [str(path) for path in executable_runtime_library_directories(executable)]
    inherited = [
        value
        for value in environment.get("LD_LIBRARY_PATH", "").split(":")
        if value and value not in owned
    ]
    environment["LD_LIBRARY_PATH"] = ":".join([*owned, *inherited])
    return environment


def executable_runtime_libraries(executable: Path) -> dict[str, dict[str, str]]:
    result = {}
    for name in (
        "libvins_lib.so",
        "libvins_fusion_ros2__rosidl_typesupport_fastrtps_cpp.so",
    ):
        for directory in executable_runtime_library_directories(executable):
            path = directory / name
            if path.is_file():
                result[name] = {
                    "path": str(path.resolve()),
                    "sha256": sha256_file(path),
                }
                break
    return result


def run_provenance(
    session: Path,
    run_config: Path,
    left_calibration: Path,
    right_calibration: Path,
    replay_backend: str,
    vins_executable: Path = VINS_EXECUTABLE,
    loop_executable: Path = LOOP_EXECUTABLE,
    replay_executable: Path | None = None,
    imu_accel_calibration: Path | None = None,
) -> dict[str, object]:
    files = {
        "runner": Path(__file__).resolve(),
        "run_config": run_config.resolve(),
        "left_calibration": left_calibration.resolve(),
        "right_calibration": right_calibration.resolve(),
        "vins_executable": vins_executable.resolve(),
        "loop_executable": loop_executable.resolve(),
    }
    if replay_backend == "cpp":
        files["replay_executable"] = (
            REPLAY_EXECUTABLE if replay_executable is None else replay_executable
        ).resolve()
    if imu_accel_calibration is not None:
        files["imu_accel_calibration"] = imu_accel_calibration.resolve()
    acceptance = session.resolve() / "acceptance.json"
    if acceptance.is_file():
        files["capture_acceptance"] = acceptance
    frame_timestamps = session.resolve() / "d405_frames.csv"
    if frame_timestamps.is_file():
        files["camera_timestamps"] = frame_timestamps
    imu_samples = session.resolve() / "external_imu" / "imu.bin"
    if imu_samples.is_file():
        files["imu_samples"] = imu_samples
    runtime_libraries = {
        "vins": executable_runtime_libraries(vins_executable),
        "loop": executable_runtime_libraries(loop_executable),
    }
    if replay_backend == "cpp":
        runtime_libraries["replay"] = executable_runtime_libraries(
            REPLAY_EXECUTABLE if replay_executable is None else replay_executable
        )
    return {
        "files": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in files.items()
        },
        "git_revisions": {
            "ego_vio_humble": git_revision(ROOT),
            "vins_fusion_ros2": git_revision(ROOT / "components" / "vins_fusion_ros2"),
        },
        "source_db3_hashed": False,
        "source_db3_identity": "capture acceptance hash plus immutable manifest",
        "runtime_libraries": runtime_libraries,
    }


def runtime_config_text(source_text: str, loop_output: Path) -> str:
    """Redirect all mutable VINS/loop outputs into this run directory."""
    replacements = (
        (
            r'(?m)^output_path:\s*"[^"]*"\s*$',
            f'output_path: "{loop_output}/"',
            "output_path",
        ),
        (
            r'(?m)^pose_graph_save_path:\s*"[^"]*"\s*$',
            f'pose_graph_save_path: "{loop_output}/pose_graph/"',
            "pose_graph_save_path",
        ),
        (r"(?m)^save_image:\s*[01]\s*$", "save_image: 1", "save_image"),
    )
    result = source_text
    for pattern, replacement, name in replacements:
        result, count = re.subn(pattern, replacement, result)
        if count != 1:
            raise ValueError(f"expected exactly one {name}, found {count}")
    return result


def classify_run_scope(
    runtime_error: str | None,
    camera_frames: int,
    corrected_poses: int,
) -> str:
    if runtime_error is not None and ("DDS" in runtime_error or corrected_poses == 0):
        return "INFRASTRUCTURE"
    if camera_frames > 0 and corrected_poses == 0:
        return "INFRASTRUCTURE"
    if runtime_error is not None:
        return "SLAM_RUNTIME"
    return "SLAM"


def parse_feature_tracking(
    vins_log: str,
    *,
    low_feature_count: int = 20,
    maximum_consecutive_low_samples: int = 2,
) -> dict[str, object]:
    """Summarize the one-Hz backend feature diagnostics emitted by VINS."""
    feature_counts = [
        int(match.group(1))
        for match in re.finditer(r"\bfeat:\s*(\d+)\b", vins_log)
    ]
    consecutive_low = 0
    maximum_consecutive_low = 0
    low_samples = 0
    for count in feature_counts:
        if count < low_feature_count:
            low_samples += 1
            consecutive_low += 1
            maximum_consecutive_low = max(
                maximum_consecutive_low, consecutive_low
            )
        else:
            consecutive_low = 0
    if not feature_counts:
        result = "BLOCKED"
    elif maximum_consecutive_low > maximum_consecutive_low_samples:
        result = "FAIL"
    else:
        result = "PASS"
    return {
        "result": result,
        "samples": len(feature_counts),
        "minimum_features": min(feature_counts) if feature_counts else None,
        "low_feature_samples": low_samples,
        "max_consecutive_low_samples": maximum_consecutive_low,
        "thresholds": {
            "low_feature_count": low_feature_count,
            "maximum_consecutive_low_samples": maximum_consecutive_low_samples,
        },
    }


def parse_pnp_quality(loop_log: str) -> dict:
    quality_pattern = re.compile(
        r"\[AUTO_LOOP_PNP_QUALITY\] current=(\d+) matched=(\d+) "
        r"inliers=(\d+) rmse_px=([0-9.inf]+) p95_px=([0-9.inf]+) "
        r"current_hull=([0-9.]+) old_hull=([0-9.]+)"
    )
    geometry_pattern = re.compile(
        r"\[AUTO_LOOP_GEOMETRY_PASS\] current=(\d+) matched=(\d+)"
    )
    accept_pattern = re.compile(
        r"\[AUTO_LOOP_ACCEPT\] current=(\d+) matched=(\d+)"
    )
    samples: dict[tuple[int, int], dict] = {}
    finite_samples = 0
    for match in quality_pattern.finditer(loop_log):
        current, matched, inliers = map(int, match.groups()[:3])
        rmse_px, p95_px, current_hull, old_hull = map(float, match.groups()[3:])
        finite_samples += int(np.isfinite(rmse_px) and np.isfinite(p95_px))
        samples[(current, matched)] = {
            "current": current,
            "matched": matched,
            "inliers": inliers,
            "rmse_px": rmse_px,
            "p95_px": p95_px,
            "current_hull_fraction": current_hull,
            "old_hull_fraction": old_hull,
        }
    geometry_keys = {
        (int(match.group(1)), int(match.group(2)))
        for match in geometry_pattern.finditer(loop_log)
    }
    accepted_edges = []
    for match in accept_pattern.finditer(loop_log):
        key = (int(match.group(1)), int(match.group(2)))
        if key in samples:
            accepted_edges.append(samples[key])
    return {
        "samples": len(samples),
        "finite_samples": finite_samples,
        "geometry_pass_samples": sum(key in samples for key in geometry_keys),
        "accepted_edges": accepted_edges,
    }


def parse_pose_graph_health(loop_log: str) -> dict:
    optimization_pattern = re.compile(
        r"\[POSE_GRAPH_OPTIMIZATION\] current=\d+ usable=(\d+)"
    )
    usability = [int(match.group(1)) for match in optimization_pattern.finditer(loop_log)]
    return {
        "optimizations": len(usability),
        "usable_optimizations": sum(usability),
        "rejected_optimizations": len(usability) - sum(usability),
    }


def parse_loop_configuration(loop_log: str) -> dict:
    spatial_support = re.search(
        r"min_loop_spatial_support:\s*([0-9.]+)\s*\((enabled|disabled)\)",
        loop_log,
    )
    max_candidates = re.search(r"max_loop_candidates:\s*(\d+)", loop_log)
    expanded_score = re.search(
        r"expanded_loop_min_retrieval_score:\s*([0-9.]+)", loop_log
    )
    return {
        "min_loop_spatial_support": (
            float(spatial_support.group(1)) if spatial_support is not None else None
        ),
        "max_loop_candidates": (
            int(max_candidates.group(1)) if max_candidates is not None else None
        ),
        "expanded_loop_min_retrieval_score": (
            float(expanded_score.group(1)) if expanded_score is not None else None
        ),
    }


def parse_loop_retrieval(loop_log: str) -> dict:
    pattern = re.compile(
        r"\[AUTO_LOOP_RETRIEVAL\] current=(\d+) returned=(\d+) eligible=(\d+)"
        r"(?P<ranks>(?: rank\d+=\d+:[0-9.]+)*)"
    )
    top_score_pattern = re.compile(r" rank1=\d+:([0-9.]+)")
    returned: list[int] = []
    eligible: list[int] = []
    top_scores: list[float] = []
    for match in pattern.finditer(loop_log):
        returned.append(int(match.group(2)))
        eligible.append(int(match.group(3)))
        top_score = top_score_pattern.search(match.group("ranks"))
        if top_score is not None:
            top_scores.append(float(top_score.group(1)))

    def summary(values: list[int] | list[float]) -> dict[str, float | int | None]:
        if not values:
            return {"min": None, "max": None, "mean": None}
        return {
            "min": min(values),
            "max": max(values),
            "mean": float(sum(values) / len(values)),
        }

    return {
        "frames": len(returned),
        "returned": summary(returned),
        "eligible": summary(eligible),
        "zero_eligible_frames": sum(value == 0 for value in eligible),
        "top_score": summary(top_scores),
    }


def parse_loop_stage_counts(loop_log: str) -> dict[str, int]:
    return {
        "pending": loop_log.count("[AUTO_LOOP_PENDING]"),
        "inconsistent": loop_log.count("[AUTO_LOOP_INCONSISTENT]"),
        "correction_rejected": loop_log.count("[AUTO_LOOP_CORRECTION_REJECT]"),
        "cooldown": loop_log.count("[AUTO_LOOP_COOLDOWN]"),
    }


def parse_loop_input_keyframes(loop_log: str) -> int:
    counts = [
        int(value)
        for value in re.findall(r"\[LOOP_INPUT\] atomic_keyframes=(\d+)", loop_log)
    ]
    return max(counts, default=0)


def pose_graph_rows_are_monotonic(rows: list[list[float]]) -> bool:
    return len(rows) >= 2 and all(
        current[0] > previous[0] for previous, current in zip(rows, rows[1:])
    )


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGINT)
        process.wait(timeout=5)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait(timeout=5)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"process group {process.pid} did not stop") from exc


def write_rows(path: Path, rows: list[list[float]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        writer.writerows(rows)


def trajectory_diagnostics(rows: list[list[float]]) -> dict[str, float | None]:
    if len(rows) < 2:
        return {
            "max_step_m": None,
            "z_span_m": None,
            "endpoint_delta_m": None,
            "endpoint_xy_m": None,
            "endpoint_z_abs_m": None,
        }
    points = np.asarray([row[1:4] for row in rows], dtype=float)
    endpoint_delta = points[-1] - points[0]
    return {
        "max_step_m": float(np.linalg.norm(np.diff(points, axis=0), axis=1).max()),
        "z_span_m": float(np.ptp(points[:, 2])),
        "endpoint_delta_m": float(np.linalg.norm(endpoint_delta)),
        "endpoint_xy_m": float(np.linalg.norm(endpoint_delta[:2])),
        "endpoint_z_abs_m": float(abs(endpoint_delta[2])),
    }


def loop_closure_error_m(
    diagnostics: dict[str, float | None], metric: str
) -> float | None:
    if metric == "3d":
        return diagnostics["endpoint_delta_m"]
    if metric == "xy":
        return diagnostics["endpoint_xy_m"]
    raise ValueError(f"unsupported loop closure metric: {metric}")


def evaluate_z_axis(
    raw: dict[str, float | None],
    corrected: dict[str, float | None],
    *,
    minimum_true_elevation_span_m: float = 0.10,
    minimum_retention_ratio: float = 0.90,
) -> dict[str, object]:
    raw_span = raw["z_span_m"]
    corrected_span = corrected["z_span_m"]
    evaluation: dict[str, object] = {
        "scope": "separate_from_loop_closure",
        "result": "MEASURED",
        "raw_span_m": raw_span,
        "raw_endpoint_abs_m": raw["endpoint_z_abs_m"],
        "corrected_span_m": corrected_span,
        "corrected_endpoint_abs_m": corrected["endpoint_z_abs_m"],
        "minimum_true_elevation_span_m": minimum_true_elevation_span_m,
        "minimum_retention_ratio": minimum_retention_ratio,
        "span_retention_ratio": None,
        "failure": None,
    }
    if raw_span is None or corrected_span is None:
        evaluation["result"] = "NOT_SCORED"
        return evaluation
    if raw_span < minimum_true_elevation_span_m:
        return evaluation
    retention_ratio = corrected_span / raw_span
    evaluation["span_retention_ratio"] = retention_ratio
    if retention_ratio < minimum_retention_ratio:
        evaluation["result"] = "FAIL"
        evaluation["failure"] = (
            f"true-elevation retention {retention_ratio:.3f} < "
            f"{minimum_retention_ratio:.3f}"
        )
    else:
        evaluation["result"] = "PASS"
    return evaluation


def camera_frame_count(session: Path, image_db3: Path | None) -> tuple[int, str | None]:
    if image_db3 is not None:
        path = image_db3.resolve()
        database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = database.execute(
                "SELECT count(*) FROM messages JOIN topics "
                "ON topics.id = messages.topic_id WHERE topics.name = ?",
                ("/device_0/sensor_0/Infrared_1/image/data",),
            ).fetchone()
        finally:
            database.close()
        count = int(row[0]) if row is not None else 0
        if count == 0:
            raise RuntimeError(f"image cache has no left IR frames: {path}")
        return count, str(path)

    for relative in (Path("d405_frames.csv"), Path("left_hand/camera_ts.csv")):
        frame_csv = session / relative
        if frame_csv.is_file():
            with frame_csv.open(newline="") as stream:
                return sum(1 for _ in csv.DictReader(stream)), str(frame_csv.resolve())
    return 0, None


def expected_pose_samples(
    camera_frames: int,
    skip_s: float,
    camera_rate_hz: float = 30.0,
) -> int:
    return max(1, camera_frames - int(round(skip_s * camera_rate_hz)))


def drain_is_complete(
    *,
    raw_poses: int,
    corrected_poses: int,
    expected_poses: int,
    min_pose_coverage: float,
    quiet_duration_s: float,
    require_coverage: bool,
    quiet_threshold_s: float = 6.0,
) -> bool:
    if quiet_duration_s < quiet_threshold_s:
        return False
    if not require_coverage:
        return True
    pose_coverage = min(raw_poses, corrected_poses) / max(1, expected_poses)
    return pose_coverage >= min_pose_coverage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--vins-executable",
        type=Path,
        default=VINS_EXECUTABLE,
        help="显式指定VINS节点二进制，并将该精确文件写入运行证据",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--skip-s", type=float, default=1.5)
    parser.add_argument("--imu-shift-ms", type=float, default=0.0)
    parser.add_argument(
        "--image-db3",
        type=Path,
        help="可选轻量双IR派生DB3；不改变原始会话和IMU来源",
    )
    parser.add_argument(
        "--replay-executable",
        type=Path,
        default=REPLAY_EXECUTABLE,
        help="显式指定C++回放二进制，支持不覆盖稳定安装的隔离A/B验证",
    )
    parser.add_argument(
        "--imu-accel-calibration",
        type=Path,
        help="可选：只应用六面静态标定的加速度计3x3矩阵与零偏",
    )
    parser.add_argument(
        "--replay-backend",
        choices=("cpp", "python"),
        default="cpp",
        help="DB3回放后端；产品验收默认使用C++以维持30fps吞吐",
    )
    parser.add_argument(
        "--loop-executable",
        type=Path,
        default=LOOP_EXECUTABLE,
        help="显式指定回环节点二进制，支持不覆盖稳定安装的隔离A/B验证",
    )
    parser.add_argument("--timeout-s", type=float, default=420.0)
    parser.add_argument("--drain-timeout-s", type=float, default=180.0)
    parser.add_argument("--duration-s", type=float, default=0.0)
    parser.add_argument(
        "--expect-loop", choices=("any", "yes", "no"), default="any"
    )
    parser.add_argument(
        "--max-loop-closure-m",
        type=float,
        default=0.01,
        help="--expect-loop yes时的后验闭环误差门槛；只评分，不输入SLAM",
    )
    parser.add_argument(
        "--loop-closure-metric",
        choices=("3d", "xy"),
        default="3d",
        help="闭环首尾误差评分维度；xy模式将Z单列报告但不计入回环结论",
    )
    parser.add_argument("--min-pose-coverage", type=float, default=0.98)
    parser.add_argument(
        "--ros-domain-id",
        type=int,
        default=77,
        help="隔离离线验收与同机其他ROS 2任务；默认77",
    )
    args = parser.parse_args()

    if args.imu_accel_calibration is not None and args.replay_backend != "cpp":
        parser.error("--imu-accel-calibration currently requires --replay-backend cpp")
    accel_calibration = (
        load_accel_calibration(args.imu_accel_calibration)
        if args.imu_accel_calibration is not None
        else None
    )

    os.environ["ROS_DOMAIN_ID"] = str(args.ros_domain_id)
    os.environ["ROS_LOCALHOST_ONLY"] = "1"

    args.out_dir.mkdir(parents=True, exist_ok=True)
    benchmark_environment = evaluate_environment(capture_environment())
    environment_failures = [
        *benchmark_environment["failures"],
        *validate_environment_report(benchmark_environment),
    ]
    if environment_failures:
        run_acceptance = {
            "result": "FAIL",
            "failure_scope": "INFRASTRUCTURE",
            "runtime_error": "benchmark environment preflight failed",
            "ros_domain_id": args.ros_domain_id,
            "ros_localhost_only": True,
            "session": str(args.session.resolve()),
            "benchmark_environment": benchmark_environment,
            "failures": environment_failures,
        }
        run_acceptance["health"] = evaluate_slam_health(run_acceptance)
        (args.out_dir / "run_acceptance.json").write_text(
            json.dumps(run_acceptance, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for failure in environment_failures:
            print(f"环境预检失败: {failure}")
        return 4
    loop_output = args.out_dir / "loop_output"
    loop_output.mkdir(exist_ok=True)
    (loop_output / "pose_graph").mkdir(exist_ok=True)

    if not args.vins_executable.is_file():
        raise FileNotFoundError(f"missing VINS executable: {args.vins_executable}")
    config_text = runtime_config_text(
        args.config.read_text(encoding="utf-8"), loop_output
    )
    run_config = args.out_dir / "vins_auto_loop_config.yaml"
    run_config.write_text(config_text, encoding="utf-8")
    calibration_paths: dict[str, Path] = {}
    for calibration_name in ("left.yaml", "right.yaml"):
        source = args.config.parent / calibration_name
        if not source.is_file():
            raise FileNotFoundError(f"missing camera calibration: {source}")
        destination = args.out_dir / calibration_name
        shutil.copy2(source, destination)
        calibration_paths[calibration_name] = destination
    provenance = run_provenance(
        args.session,
        run_config,
        calibration_paths["left.yaml"],
        calibration_paths["right.yaml"],
        args.replay_backend,
        vins_executable=args.vins_executable,
        loop_executable=args.loop_executable,
        replay_executable=args.replay_executable,
        imu_accel_calibration=args.imu_accel_calibration,
    )
    camera_frames, camera_frame_count_source = camera_frame_count(
        args.session, args.image_db3
    )
    expected_poses = expected_pose_samples(camera_frames, args.skip_s)

    vins_log_path = args.out_dir / "vins.log"
    loop_log_path = args.out_dir / "auto_loop.log"
    replay_log_path = args.out_dir / "replay.log"
    processes: list[subprocess.Popen[bytes]] = []

    loop_markers = tuple(
        dict.fromkeys(("loop_fusion_node", args.loop_executable.name))
    )
    stale = find_processes(loop_markers)
    if stale:
        stale_text = "\n".join(
            f"{entry['pid']} {entry['command']}" for entry in stale
        )
        raise RuntimeError(
            "stale loop_fusion_node exists; stop it before a deterministic run:\n"
            + stale_text
        )

    rclpy.init()
    node = Node("auto_loop_trajectory_sink")
    raw_rows: list[list[float]] = []
    corrected_rows: list[list[float]] = []
    pose_graph_rows: list[list[float]] = []
    runtime_error: str | None = None
    runtime_watchdog = SlamRuntimeWatchdog(start_monotonic_s=time.monotonic())

    def append_pose(
        stream: str, rows: list[list[float]], message: Odometry
    ) -> None:
        pose = message.pose.pose
        timestamp_s = message.header.stamp.sec + message.header.stamp.nanosec * 1e-9
        runtime_watchdog.ingest(
            stream,
            timestamp_s=timestamp_s,
            point=(pose.position.x, pose.position.y, pose.position.z),
            arrival_monotonic_s=time.monotonic(),
        )
        rows.append([
            timestamp_s,
            pose.position.x,
            pose.position.y,
            pose.position.z,
            pose.orientation.w,
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
        ])

    node.create_subscription(
        Odometry,
        "/odometry",
        lambda msg: append_pose("raw", raw_rows, msg),
        2000,
    )
    node.create_subscription(
        Odometry,
        "/odometry_rect",
        lambda msg: append_pose("corrected", corrected_rows, msg),
        2000,
    )

    # The real-time corrected stream cannot rewrite poses that were already
    # published.  VINS-Fusion's /pose_graph_path does republish the complete,
    # latest 4DoF-optimized keyframe history.  Preserve that historical product
    # output instead of reconstructing it from an abrupt live transform.
    def replace_pose_graph(message: NavPath) -> None:
        latest: list[list[float]] = []
        for pose_stamped in message.poses:
            pose = pose_stamped.pose
            stamp = pose_stamped.header.stamp
            timestamp_s = stamp.sec + stamp.nanosec * 1e-9
            latest.append([
                timestamp_s,
                pose.position.x,
                pose.position.y,
                pose.position.z,
                pose.orientation.w,
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
            ])
        if latest:
            pose_graph_rows[:] = latest

    node.create_subscription(NavPath, "/pose_graph_path", replace_pose_graph, 10)

    try:
        with vins_log_path.open("wb") as vins_log, loop_log_path.open("wb") as loop_log:
            vins = subprocess.Popen(
                [
                    str(args.vins_executable.resolve()),
                    "--ros-args", "-p", "use_sim_time:=false",
                    "-p", f"config_file:={run_config}",
                ],
                stdout=vins_log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
                env=executable_runtime_environment(args.vins_executable),
            )
            processes.append(vins)
            loop = subprocess.Popen(
                ["stdbuf", "-oL", "-eL", str(args.loop_executable.resolve()),
                 str(run_config)],
                stdout=loop_log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
                env=executable_runtime_environment(args.loop_executable),
            )
            processes.append(loop)
            time.sleep(6)
            if vins.poll() is not None or loop.poll() is not None:
                raise RuntimeError("VINS or loop-fusion node exited during startup")

            with replay_log_path.open("wb") as replay_log:
                if list(args.session.glob("*.db3")):
                    replay_args = [
                        "--session", str(args.session), "--mode", "stereo",
                        "--rate", str(args.rate), "--skip-s", str(args.skip_s),
                        "--imu-align-s", "0", "--imu-shift-ms", str(args.imu_shift_ms),
                    ]
                    if accel_calibration is not None:
                        replay_args.extend(
                            accel_calibration_replay_arguments(accel_calibration)
                        )
                    if args.replay_backend == "cpp":
                        replay_command = (
                            [
                                "python3",
                                str(ROOT / "scripts" / "replay_db3_cpp_to_ros2.py"),
                                *replay_args,
                            ]
                            if args.replay_executable.resolve()
                            == REPLAY_EXECUTABLE.resolve()
                            else [str(args.replay_executable.resolve()), *replay_args]
                        )
                    else:
                        replay_command = [
                            "python3",
                            str(ROOT / "scripts" / "replay_db3_to_ros2.py"),
                            *replay_args,
                        ]
                    if args.image_db3 is not None:
                        replay_command.extend(
                            ["--image-db3", str(args.image_db3.resolve())]
                        )
                    if args.replay_backend == "python":
                        replay_command.extend(["--duration-s", str(args.duration_s)])
                    elif args.duration_s:
                        raise ValueError(
                            "C++ DB3回放暂不支持--duration-s；改用--replay-backend python"
                        )
                elif (args.session / "left_hand/camera_ts.csv").is_file():
                    if args.skip_s or args.duration_s:
                        raise ValueError(
                            "calibration-session replay does not support skip/duration"
                        )
                    replay_command = [
                        "python3", str(ROOT / "scripts/replay_calib_to_ros2.py"),
                        "--session", str(args.session), "--rate", str(args.rate),
                        "--imu-shift-ms", str(args.imu_shift_ms),
                    ]
                else:
                    raise FileNotFoundError(
                        f"unsupported recording layout: {args.session}"
                    )
                replay = subprocess.Popen(
                    replay_command,
                    cwd=ROOT,
                    stdout=replay_log,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid,
                    env=(
                        executable_runtime_environment(args.replay_executable)
                        if args.replay_backend == "cpp"
                        else None
                    ),
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
            last_counts = (len(raw_rows), len(corrected_rows))
            drain_deadline = time.monotonic() + args.drain_timeout_s
            while time.monotonic() < drain_deadline:
                rclpy.spin_once(node, timeout_sec=0.05)
                current_counts = (len(raw_rows), len(corrected_rows))
                if current_counts != last_counts:
                    last_counts = current_counts
                    quiet_since = time.monotonic()
                elif drain_is_complete(
                    raw_poses=current_counts[0],
                    corrected_poses=current_counts[1],
                    expected_poses=expected_poses,
                    min_pose_coverage=args.min_pose_coverage,
                    quiet_duration_s=time.monotonic() - quiet_since,
                    require_coverage=camera_frames > 0,
                ):
                    break
    except Exception as exc:
        runtime_error = str(exc)
        print(f"FAIL: {exc}")
        return_code = 2
    else:
        return_code = 0
    finally:
        for process in reversed(processes):
            stop_process(process)
        write_rows(args.out_dir / "vio_raw.csv", raw_rows)
        write_rows(args.out_dir / "vio_corrected_stream.csv", corrected_rows)
        write_rows(args.out_dir / "vio_pose_graph_final.csv", pose_graph_rows)
        node.destroy_node()
        rclpy.shutdown()

    loop_log = loop_log_path.read_text(errors="replace") if loop_log_path.exists() else ""
    vins_log = vins_log_path.read_text(errors="replace") if vins_log_path.exists() else ""
    accepted = [line for line in loop_log.splitlines() if "[AUTO_LOOP_ACCEPT]" in line]
    rejected = [line for line in loop_log.splitlines() if "[AUTO_LOOP_REJECT]" in line]
    correction_rejected = [
        line
        for line in loop_log.splitlines()
        if "[AUTO_LOOP_CORRECTION_REJECT]" in line
    ]
    retrieval_score_rejected = [
        line
        for line in loop_log.splitlines()
        if "[AUTO_LOOP_RETRIEVAL_SCORE_REJECT]" in line
    ]
    spatial_rejected = [
        line for line in loop_log.splitlines() if "[AUTO_LOOP_SPATIAL_REJECT]" in line
    ]
    pose_graph_health = parse_pose_graph_health(loop_log)
    loop_configuration = parse_loop_configuration(loop_log)
    loop_stage_counts = parse_loop_stage_counts(loop_log)
    loop_atomic_keyframes = parse_loop_input_keyframes(loop_log)
    feature_tracking = parse_feature_tracking(vins_log)
    input_drops = [line for line in loop_log.splitlines() if "[LOOP_INPUT_DROP]" in line]
    estimator_queue_drops = [
        line
        for line in vins_log.splitlines()
        if "[LOOP_KEYFRAME_QUEUE_DROP]" in line
    ]
    pose_coverage = min(len(raw_rows), len(corrected_rows)) / expected_poses
    raw_diagnostics = trajectory_diagnostics(raw_rows)
    corrected_diagnostics = trajectory_diagnostics(corrected_rows)
    pose_graph_diagnostics = trajectory_diagnostics(pose_graph_rows)
    runtime_watchdog_report = runtime_watchdog.completion_snapshot()
    failures: list[str] = []
    if runtime_watchdog_report["state"] != "SLAM_HEALTHY":
        failures.append(
            "runtime watchdog is "
            f"{runtime_watchdog_report['state']}: "
            + ", ".join(runtime_watchdog_report["failures"])
        )
    if input_drops:
        failures.append(f"loop keyframe transport/backlog drops: {len(input_drops)}")
    if estimator_queue_drops:
        failures.append(
            f"estimator loop keyframe queue drops: {len(estimator_queue_drops)}"
        )
    if pose_graph_health["rejected_optimizations"]:
        failures.append(
            "pose graph unusable solutions: "
            f"{pose_graph_health['rejected_optimizations']}"
        )
    if loop_atomic_keyframes > 0 and not pose_graph_rows_are_monotonic(pose_graph_rows):
        failures.append(
            "final pose graph missing or timestamps are not strictly increasing "
            f"after {loop_atomic_keyframes} observed keyframes"
        )
    if feature_tracking["result"] != "PASS":
        failures.append(
            "feature tracking "
            f"{feature_tracking['result'].lower()}: minimum="
            f"{feature_tracking['minimum_features']}, consecutive_low="
            f"{feature_tracking['max_consecutive_low_samples']}"
        )
    if camera_frames and pose_coverage < args.min_pose_coverage:
        failures.append(
            f"pose coverage {pose_coverage:.4f} < {args.min_pose_coverage:.4f}"
        )
    if args.expect_loop == "yes" and not accepted:
        failures.append("expected an automatic loop, but none was accepted")
    if args.expect_loop == "no" and accepted:
        failures.append(f"expected no automatic loop, but accepted {len(accepted)}")
    corrected_endpoint_delta = loop_closure_error_m(
        corrected_diagnostics, args.loop_closure_metric
    )
    if (
        args.expect_loop == "yes"
        and corrected_endpoint_delta is not None
        and corrected_endpoint_delta >= args.max_loop_closure_m
    ):
        failures.append(
            f"automatic-loop {args.loop_closure_metric} endpoint error "
            f"{corrected_endpoint_delta:.4f}m >= "
            f"{args.max_loop_closure_m:.4f}m"
        )
    raw_max_step = raw_diagnostics["max_step_m"]
    corrected_max_step = corrected_diagnostics["max_step_m"]
    if raw_max_step is not None and corrected_max_step is not None:
        max_allowed_step = max(0.03, 3.5 * raw_max_step)
        if corrected_max_step > max_allowed_step:
            failures.append(
                f"corrected trajectory jump {corrected_max_step:.4f}m > "
                f"{max_allowed_step:.4f}m"
            )
    z_axis_evaluation = evaluate_z_axis(raw_diagnostics, corrected_diagnostics)
    z_retention_ratio = z_axis_evaluation["span_retention_ratio"]
    if args.loop_closure_metric == "3d" and z_axis_evaluation["failure"]:
        failures.append(str(z_axis_evaluation["failure"]))
    run_acceptance = {
        "result": "PASS" if return_code == 0 and not failures else "FAIL",
        "failure_scope": classify_run_scope(
            runtime_error, camera_frames, len(corrected_rows)
        ),
        "runtime_error": runtime_error,
        "runtime_watchdog": runtime_watchdog_report,
        "ros_domain_id": args.ros_domain_id,
        "ros_localhost_only": True,
        "benchmark_environment": benchmark_environment,
        "provenance": provenance,
        "session": str(args.session.resolve()),
        "replay_rate": args.rate,
        "replay_backend": args.replay_backend,
        "imu_accel_calibration": accel_calibration,
        "raw_odometry_samples": len(raw_rows),
        "corrected_odometry_samples": len(corrected_rows),
        "final_pose_graph_samples": len(pose_graph_rows),
        "loop_atomic_keyframes_observed": loop_atomic_keyframes,
        "camera_frames": camera_frames,
        "camera_frame_count_source": camera_frame_count_source,
        "expected_pose_samples_after_skip": expected_poses,
        "pose_coverage": pose_coverage,
        "automatic_loop_accepts": len(accepted),
        "automatic_loop_rejects": len(rejected),
        "automatic_correction_rejects": len(correction_rejected),
        "automatic_retrieval_score_rejects": len(retrieval_score_rejected),
        "automatic_spatial_rejects": len(spatial_rejected),
        **loop_configuration,
        "loop_retrieval": parse_loop_retrieval(loop_log),
        "loop_stage_counts": loop_stage_counts,
        "pnp_quality": parse_pnp_quality(loop_log),
        "pose_graph_health": pose_graph_health,
        "feature_tracking": feature_tracking,
        "raw_trajectory_diagnostics": raw_diagnostics,
        "corrected_trajectory_diagnostics": corrected_diagnostics,
        "final_pose_graph_diagnostics": pose_graph_diagnostics,
        "loop_closure_evaluation": {
            "metric": args.loop_closure_metric,
            "maximum_m": args.max_loop_closure_m,
            "corrected_endpoint_error_m": corrected_endpoint_delta,
        },
        "z_axis_evaluation": {
            **z_axis_evaluation,
            "included_in_run_result": args.loop_closure_metric == "3d",
        },
        "z_span_retention_ratio": z_retention_ratio,
        "loop_input_drop_events": len(input_drops),
        "estimator_keyframe_queue_drop_events": len(estimator_queue_drops),
        "failures": failures,
    }
    run_acceptance["health"] = evaluate_slam_health(run_acceptance)
    (args.out_dir / "run_acceptance.json").write_text(
        json.dumps(run_acceptance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"raw odometry: {len(raw_rows)} samples")
    print(f"corrected odometry: {len(corrected_rows)} samples")
    print(f"final pose graph: {len(pose_graph_rows)} keyframes")
    print(f"automatic loop accepts: {len(accepted)}")
    for line in accepted:
        print(line)
    print(f"automatic loop rejects after geometry: {len(rejected)}")
    print(f"automatic correction safety rejects: {len(correction_rejected)}")
    print(f"automatic expanded-retrieval rejects: {len(retrieval_score_rejected)}")
    print(f"automatic spatial-support rejects: {len(spatial_rejected)}")
    print(f"pose coverage: {pose_coverage:.4f}")
    print(f"loop input drop events: {len(input_drops)}")
    print(f"estimator keyframe queue drop events: {len(estimator_queue_drops)}")
    print(
        "feature tracking: "
        f"{feature_tracking['result']} "
        f"(minimum={feature_tracking['minimum_features']}, "
        f"consecutive_low={feature_tracking['max_consecutive_low_samples']})"
    )
    print(f"keyframe trajectory: {loop_output / 'vio_loop.csv'}")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 3 if return_code == 0 and failures else return_code


if __name__ == "__main__":
    raise SystemExit(main())
