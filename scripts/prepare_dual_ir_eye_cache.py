#!/usr/bin/env python3
"""Prepare an onboard-only independent right-IR MASt3R cache.

This helper intentionally does not reuse an existing right cache: callers that
want reuse should detect and select those caches before calling here.  The
output directory must be new, and all downstream products are bound to the raw
right-eye ``trajectory_frames.csv`` rather than the interpolated dense track.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from fuse_mast3r_stereo_imu import validate_onboard_report

PYTHON = sys.executable
WORKFLOW = ROOT / "scripts" / "mast3r_slam_precision_workflow.sh"
DERIVE_RIGHT_STEREO = ROOT / "scripts" / "derive_right_ir_stereo_scale.py"
ALIGN_IMU = ROOT / "scripts" / "align_mast3r_scale_with_imu.py"
OFFLINE_CONFIG = ROOT / "config" / "mast3r_slam_d405_offline.yaml"
VINS_CONFIG = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/"
    "docker2_release/formal_runtime_calibration/vins_config.yaml"
)
IMU_CALIBRATION = ROOT / "config" / "imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"
CHECKPOINT = Path(
    "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/checkpoints/"
    "MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"
)

STEREO_REPORTS = (
    ("stereo_scale_bidirectional_report.json", "stereo_scale_right_report.json"),
    ("stereo_scale_long_hops_report.json", "stereo_scale_long_hops_right_report.json"),
    ("stereo_scale_dense10hz_report.json", "stereo_scale_dense10hz_right_report.json"),
    ("stereo_scale_multisecond_report.json", "stereo_scale_multisecond_right_report.json"),
)
ONBOARD_ATTITUDE_SOURCES = {None, "onboard_orientation_trajectory", "mast3r"}


class PreparationError(RuntimeError):
    """Raised when the cache cannot be prepared safely."""


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _contains_tracker_gt(value: Any) -> bool:
    if isinstance(value, str):
        return "TrackerGT" in value
    if isinstance(value, dict):
        return any(_contains_tracker_gt(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tracker_gt(item) for item in value)
    return False


def _same_path(left: str | Path, right: str | Path) -> bool:
    return Path(left).resolve() == Path(right).resolve()


def _validate_left_stereo_report(path: Path, session: Path) -> None:
    report = _json(path)
    try:
        validate_onboard_report(report, path, "umi_mast3r_stereo_scale_v2")
    except ValueError as error:
        raise PreparationError(str(error)) from error
    if not _same_path(report.get("session", ""), session):
        raise PreparationError(f"left stereo report session does not match input: {path}")
    trajectory = report.get("trajectory")
    if not trajectory or not Path(trajectory).is_file():
        raise PreparationError(f"left stereo report trajectory is missing: {path}")
    if report.get("observation_frame") != "infrared_left_camera_i":
        raise PreparationError(f"left stereo report is not left-IR framed: {path}")
    if _contains_tracker_gt(report):
        raise PreparationError(f"refusing TrackerGT-derived stereo report: {path}")


def _orientation_trajectory_from_left_cache(left_dir: Path, session: Path) -> Path:
    report_path = left_dir / "imu_scale_report.json"
    report = _json(report_path)
    try:
        validate_onboard_report(report, report_path, "umi_mast3r_imu_scale_v1")
    except ValueError as error:
        raise PreparationError(str(error)) from error
    if report.get("session") is not None and not _same_path(report["session"], session):
        raise PreparationError(f"left IMU scale report session does not match input: {report_path}")
    if report.get("attitude", {}).get("source") not in ONBOARD_ATTITUDE_SOURCES:
        raise PreparationError(f"left IMU scale report did not use onboard body orientation: {report_path}")
    if _contains_tracker_gt(report):
        raise PreparationError(f"refusing TrackerGT-derived IMU scale report: {report_path}")
    source = report.get("orientation_trajectory")
    if not source:
        raise PreparationError(f"missing onboard orientation_trajectory in {report_path}")
    path = Path(source)
    if not path.is_file():
        raise PreparationError(f"orientation trajectory does not exist: {path}")
    return path.resolve()


def _frontend_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MAST3R_")}
    env.update(
        {
            "MAST3R_SLAM_CONFIG": str(OFFLINE_CONFIG),
            "MAST3R_SLAM_CHECKPOINT": str(CHECKPOINT),
            "MAST3R_CROP_BOTTOM_PX": "0",
            "MAST3R_MASK_FIXED_SELF": "0",
            "MAST3R_STEREO_DESCRIPTOR_RECOVERY": "0",
            "MAST3R_SPATIAL_POINTMAP_RECOVERY": "0",
        }
    )
    return env


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_command_log(output: Path, record: dict[str, Any]) -> None:
    with (output / "command_log.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _fail_marker(output: Path, stage: str, command: list[str], reason: str, rc: int | None = None) -> None:
    _write_json(
        output / f"{stage}.failed.json",
        {
            "schema": "umi_dual_ir_eye_cache_stage_failure_v1",
            "stage": stage,
            "reason": reason,
            "returncode": rc,
            "command": command,
            "timestamp_unix_s": time.time(),
        },
    )


def _run_command(
    command: list[str],
    *,
    stage: str,
    output: Path,
    timeout_s: int,
    env: dict[str, str] | None = None,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> None:
    started = time.time()
    _append_command_log(
        output,
        {
            "stage": stage,
            "event": "start",
            "command": command,
            "timeout_s": timeout_s,
            "timestamp_unix_s": started,
        },
    )
    log_path = output / f"{stage}.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            _fail_marker(output, stage, command, f"timeout after {timeout_s}s")
            _append_command_log(
                output,
                {
                    "stage": stage,
                    "event": "timeout",
                    "elapsed_s": time.time() - started,
                    "command": command,
                },
            )
            raise TimeoutError(f"{stage} timed out after {timeout_s}s") from exc
    _append_command_log(
        output,
        {
            "stage": stage,
            "event": "finish",
            "returncode": rc,
            "elapsed_s": time.time() - started,
            "command": command,
        },
    )
    if rc not in allowed_returncodes:
        _fail_marker(output, stage, command, "disallowed return code", rc)
        raise subprocess.CalledProcessError(rc, command)


def _coverage_report(output: Path) -> dict[str, Any]:
    dataset_manifest = _json(output / "dataset" / "dataset_manifest.json")
    trajectory_manifest = _json(output / "trajectory_frames.manifest.json")
    if dataset_manifest.get("schema") != "umi_mast3r_dataset_v1":
        raise PreparationError("unexpected dataset manifest schema")
    if trajectory_manifest.get("schema") != "umi_mast3r_trajectory_v1":
        raise PreparationError("unexpected trajectory manifest schema")
    if dataset_manifest.get("stream") != "infrared_right":
        raise PreparationError("frontend dataset is not infrared_right")
    compatible_frames = int(dataset_manifest["frames"])
    tracked_poses = int(trajectory_manifest["source_poses"])
    dense_frames = int(trajectory_manifest["dense_frames"])
    if dense_frames != compatible_frames:
        raise PreparationError("trajectory manifest dense frame count does not match dataset")
    report = {
        "schema": "umi_dual_ir_eye_cache_coverage_v1",
        "stream": dataset_manifest.get("stream"),
        "source_session": dataset_manifest.get("source_session"),
        "compatible_original_camera_frames": compatible_frames,
        "raw_tracked_poses": tracked_poses,
        "raw_coverage_ratio": tracked_poses / compatible_frames if compatible_frames else 0.0,
        "trajectory_used_for_scale": str((output / "trajectory_frames.csv").resolve()),
        "interpolated_dense_trajectory": str(
            (output / "trajectory_all_frames_interpolated.csv").resolve()
        ),
        "interpolated_dense_used_for_scale": False,
        "dense_frames": dense_frames,
        "partial_track_allowed": tracked_poses < compatible_frames,
    }
    _write_json(output / "frontend_coverage_report.json", report)
    return report


def prepare(
    session: str | Path,
    left_dir: str | Path,
    output: str | Path,
    *,
    frontend_timeout_s: int = 7200,
    stage_timeout_s: int = 1800,
) -> Path:
    """Prepare a new independent right-IR cache from a validated left cache."""

    session_path = Path(session).resolve()
    left_path = Path(left_dir).resolve()
    output_path = Path(output).resolve()
    if not session_path.is_dir():
        raise PreparationError(f"session does not exist: {session_path}")
    if not left_path.is_dir():
        raise PreparationError(f"left cache does not exist: {left_path}")
    if output_path.exists() or output_path.is_symlink():
        raise PreparationError(f"refusing to overwrite existing output: {output_path}")

    for left_name, _ in STEREO_REPORTS:
        _validate_left_stereo_report(left_path / left_name, session_path)
    orientation_trajectory = _orientation_trajectory_from_left_cache(left_path, session_path)

    output_path.mkdir(parents=True)
    env = _frontend_environment()

    workflow_command = [
        "bash",
        str(WORKFLOW),
        "run",
        str(session_path),
        str(output_path),
        "infrared_right",
        "0",
        "0",
    ]
    _run_command(
        workflow_command,
        stage="frontend",
        output=output_path,
        timeout_s=frontend_timeout_s,
        env=env,
    )
    coverage = _coverage_report(output_path)
    run_manifest = _json(output_path / "run_manifest.json")
    if Path(run_manifest["config"]).resolve() != OFFLINE_CONFIG.resolve():
        raise PreparationError("frontend did not use frozen offline config")
    if Path(run_manifest["checkpoint"]).resolve() != CHECKPOINT.resolve():
        raise PreparationError("frontend did not use official checkpoint")
    if _json(output_path / "dataset" / "dataset_manifest.json").get("stream") != "infrared_right":
        raise PreparationError("frontend dataset is not infrared_right")

    stereo_outputs: list[Path] = []
    for left_name, right_name in STEREO_REPORTS:
        right_report = output_path / right_name
        if right_report.exists():
            raise PreparationError(f"refusing to overwrite: {right_report}")
        command = [
            PYTHON,
            str(DERIVE_RIGHT_STEREO),
            "--left-stereo-report",
            str(left_path / left_name),
            "--right-trajectory",
            str(output_path / "trajectory_frames.csv"),
            "--output",
            str(right_report),
        ]
        _run_command(command, stage=right_report.stem, output=output_path, timeout_s=stage_timeout_s)
        report = _json(right_report)
        try:
            validate_onboard_report(report, right_report, "umi_mast3r_stereo_scale_v2")
        except ValueError as error:
            raise PreparationError(str(error)) from error
        if not _same_path(report.get("session", ""), session_path):
            raise PreparationError(f"right stereo report session does not match input: {right_report}")
        if not _same_path(report.get("trajectory", ""), output_path / "trajectory_frames.csv"):
            raise PreparationError(f"right stereo report is not bound to raw right trajectory: {right_report}")
        if _contains_tracker_gt(report):
            raise PreparationError(f"right stereo report is not onboard-only: {right_report}")
        stereo_outputs.append(right_report)

    imu_output = output_path / "imu_metric_trajectory.csv"
    imu_report = output_path / "imu_scale_report.json"
    if imu_output.exists() or imu_report.exists():
        raise PreparationError("refusing to overwrite existing IMU scale outputs")
    imu_command = [
        PYTHON,
        str(ALIGN_IMU),
        "--session",
        str(session_path),
        "--trajectory",
        str(output_path / "trajectory_frames.csv"),
        "--orientation-trajectory",
        str(orientation_trajectory),
        "--stereo-scale-report",
        str(stereo_outputs[0]),
        "--stream",
        "infrared_right",
        "--body-t-camera-yaml",
        str(VINS_CONFIG),
        "--imu-calibration",
        str(IMU_CALIBRATION),
        "--td-s",
        "-0.009109323",
        "--node-stride",
        "10",
        "--max-hop",
        "1",
        "--output",
        str(imu_output),
        "--report",
        str(imu_report),
    ]
    _run_command(imu_command, stage="imu_scale", output=output_path, timeout_s=stage_timeout_s)
    imu = _json(imu_report)
    try:
        validate_onboard_report(imu, imu_report, "umi_mast3r_imu_scale_v1")
    except ValueError as error:
        raise PreparationError(str(error)) from error
    if _contains_tracker_gt(imu):
        raise PreparationError("right IMU scale report is not onboard-only")
    if imu.get("camera_stream") != "infrared_right":
        raise PreparationError("right IMU scale report is not bound to infrared_right")

    _write_json(
        output_path / "dual_ir_eye_cache_manifest.json",
        {
            "schema": "umi_dual_ir_eye_cache_v1",
            "result": "PASS",
            "session": str(session_path),
            "left_cache": str(left_path),
            "right_cache": str(output_path),
            "stream": "infrared_right",
            "frontend_config": str(OFFLINE_CONFIG),
            "checkpoint": str(CHECKPOINT),
            "trajectory_used_for_downstream": str((output_path / "trajectory_frames.csv").resolve()),
            "interpolated_dense_used_for_downstream": False,
            "coverage": coverage,
            "stereo_reports": [str(path.resolve()) for path in stereo_outputs],
            "imu_metric_trajectory": str(imu_output.resolve()),
            "imu_scale_report": str(imu_report.resolve()),
            "imu_alignment_args": {"node_stride": 10, "max_hop": 1, "stream": "infrared_right"},
            "orientation_source": "onboard_orientation_trajectory_from_left_imu_scale_report",
            "orientation_trajectory": str(orientation_trajectory),
            "external_ground_truth_used": False,
        },
    )
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("left_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--frontend-timeout-s", type=int, default=7200)
    parser.add_argument("--stage-timeout-s", type=int, default=1800)
    args = parser.parse_args(argv)
    prepare(
        args.session,
        args.left_dir,
        args.output,
        frontend_timeout_s=args.frontend_timeout_s,
        stage_timeout_s=args.stage_timeout_s,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
