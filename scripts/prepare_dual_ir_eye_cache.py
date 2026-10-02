#!/usr/bin/env python3
"""Prepare an onboard-only independent right-IR MASt3R cache.

This helper intentionally does not reuse an existing right cache: callers that
want reuse should detect and select those caches before calling here.  The
output directory must be new, and all downstream products are bound to the raw
right-eye ``trajectory_frames.csv`` rather than the interpolated dense track.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
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
PRIMARY_STEREO_REPORTS = (STEREO_REPORTS[0],)
OPTIONAL_STEREO_REPORTS = STEREO_REPORTS[1:]
STRICT_DERIVE_STEREO_RETURNCODES = (0,)
OPTIONAL_DERIVE_STEREO_RETURNCODES = (0, 2)
OPTIONAL_STEREO_POLICIES = ("strict", "reject_window")
ONBOARD_ATTITUDE_SOURCES = {None, "onboard_orientation_trajectory", "mast3r"}


class PreparationError(RuntimeError):
    """Raised when the cache cannot be prepared safely."""


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise PreparationError(f"{label} does not exist: {path}")


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
) -> int:
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
    return rc


def _validate_right_stereo_identity(report: dict[str, Any], path: Path, session: Path, trajectory: Path) -> None:
    if report.get("schema") != "umi_mast3r_stereo_scale_v2":
        raise PreparationError(f"unexpected right stereo report schema: {path}")
    if report.get("slam_supervision") is not False:
        raise PreparationError(f"right stereo report does not disable supervision: {path}")
    if report.get("external_ground_truth_used") is not False:
        raise PreparationError(f"right stereo report is not onboard-only: {path}")
    if not _same_path(report.get("session", ""), session):
        raise PreparationError(f"right stereo report session does not match input: {path}")
    if not _same_path(report.get("trajectory", ""), trajectory):
        raise PreparationError(f"right stereo report is not bound to raw right trajectory: {path}")
    if report.get("observation_frame") != "infrared_right_camera_i":
        raise PreparationError(f"right stereo report is not right-IR framed: {path}")
    if _contains_tracker_gt(report):
        raise PreparationError(f"right stereo report is not onboard-only: {path}")


def _validate_right_stereo_pass(report: dict[str, Any], path: Path, session: Path, trajectory: Path) -> None:
    _validate_right_stereo_identity(report, path, session, trajectory)
    if report.get("result") != "PASS":
        raise PreparationError(f"right stereo report did not pass: {path}")


def _validate_right_stereo_optional_rejection(
    report: dict[str, Any], path: Path, session: Path, trajectory: Path
) -> dict[str, Any]:
    _validate_right_stereo_identity(report, path, session, trajectory)
    if report.get("result") != "FAIL":
        raise PreparationError(f"optional right stereo rc2 did not write a FAIL report: {path}")
    return {
        "report": str(path.resolve()),
        "result": report.get("result"),
        "failures": report.get("failures", []),
    }


def _validate_frontend_cache(cache: Path, session: Path) -> dict[str, Any]:
    run_path = cache / "run_manifest.json"
    dataset_path = cache / "dataset" / "dataset_manifest.json"
    trajectory_path = cache / "trajectory_frames.csv"
    trajectory_manifest_path = cache / "trajectory_frames.manifest.json"
    interpolated_path = cache / "trajectory_all_frames_interpolated.csv"
    for label, path in (
        ("frontend cache run manifest", run_path),
        ("frontend cache dataset manifest", dataset_path),
        ("frontend cache raw trajectory", trajectory_path),
        ("frontend cache trajectory manifest", trajectory_manifest_path),
        ("frontend cache interpolated trajectory", interpolated_path),
    ):
        _require_file(path, label)
    run = _json(run_path)
    dataset = _json(dataset_path)
    if run.get("schema") != "umi_mast3r_run_v1" or run.get("slam_supervision") is not False:
        raise PreparationError("frontend cache is not an unsupervised MASt3R run")
    if dataset.get("schema") != "umi_mast3r_dataset_v1" or dataset.get("slam_supervision") is not False:
        raise PreparationError("frontend cache dataset is not unsupervised")
    if dataset.get("stream") != "infrared_right":
        raise PreparationError("frontend cache dataset is not infrared_right")
    if not _same_path(dataset.get("source_session", ""), session):
        raise PreparationError("frontend cache source session mismatch")
    frames = session / "d405_frames.csv"
    _require_file(frames, "session d405_frames.csv")
    if dataset.get("source_frames_csv_sha256") != _file_sha256(frames):
        raise PreparationError("frontend cache source frame hash mismatch")
    if Path(run.get("config", "")).resolve() != OFFLINE_CONFIG.resolve():
        raise PreparationError("frontend cache config path mismatch")
    if run.get("config_sha256") != _file_sha256(OFFLINE_CONFIG):
        raise PreparationError("frontend cache config hash mismatch")
    if Path(run.get("checkpoint", "")).resolve() != CHECKPOINT.resolve():
        raise PreparationError("frontend cache checkpoint path mismatch")
    if run.get("checkpoint_sha256") != _file_sha256(CHECKPOINT):
        raise PreparationError("frontend cache checkpoint hash mismatch")
    if bool(run.get("stereo_descriptor_recovery")) or bool(run.get("spatial_pointmap_recovery")):
        raise PreparationError("frontend cache recovery flags are enabled")
    preprocessing = dataset.get("image_preprocessing", {})
    if preprocessing.get("crop_bottom_px") != 0 or preprocessing.get("mask_fixed_self_occlusion") is not False:
        raise PreparationError("frontend cache preprocessing mismatch")
    if run.get("trajectory") and not _same_path(run["trajectory"], trajectory_path):
        raise PreparationError("frontend cache run trajectory path mismatch")
    if run.get("all_frames_interpolated_trajectory") and not _same_path(
        run["all_frames_interpolated_trajectory"], interpolated_path
    ):
        raise PreparationError("frontend cache run interpolated trajectory path mismatch")
    return {
        "source_cache": str(cache.resolve()),
        "source_sha256": {
            "run_manifest.json": _file_sha256(run_path),
            "dataset/dataset_manifest.json": _file_sha256(dataset_path),
            "trajectory_frames.csv": _file_sha256(trajectory_path),
            "trajectory_frames.manifest.json": _file_sha256(trajectory_manifest_path),
            "trajectory_all_frames_interpolated.csv": _file_sha256(interpolated_path),
        },
    }


def _copy_frontend_cache(cache: Path, output: Path, provenance: dict[str, Any]) -> dict[str, Any]:
    output.mkdir(parents=True)
    (output / "dataset").mkdir()
    shutil.copy2(cache / "trajectory_frames.csv", output / "trajectory_frames.csv")
    shutil.copy2(cache / "trajectory_frames.manifest.json", output / "trajectory_frames.manifest.json")
    shutil.copy2(
        cache / "trajectory_all_frames_interpolated.csv",
        output / "trajectory_all_frames_interpolated.csv",
    )
    shutil.copy2(cache / "dataset" / "dataset_manifest.json", output / "dataset" / "dataset_manifest.json")
    run = _json(cache / "run_manifest.json")
    run["trajectory"] = str((output / "trajectory_frames.csv").resolve())
    run["all_frames_interpolated_trajectory"] = str(
        (output / "trajectory_all_frames_interpolated.csv").resolve()
    )
    _write_json(output / "run_manifest.json", run)
    provenance = {
        **provenance,
        "frontend_reused_without_gpu": True,
        "copied_sha256": {
            "run_manifest.json": _file_sha256(output / "run_manifest.json"),
            "dataset/dataset_manifest.json": _file_sha256(output / "dataset" / "dataset_manifest.json"),
            "trajectory_frames.csv": _file_sha256(output / "trajectory_frames.csv"),
            "trajectory_frames.manifest.json": _file_sha256(output / "trajectory_frames.manifest.json"),
            "trajectory_all_frames_interpolated.csv": _file_sha256(
                output / "trajectory_all_frames_interpolated.csv"
            ),
        },
    }
    _write_json(output / "frontend_cache_reuse_provenance.json", provenance)
    return provenance


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
    if tracked_poses <= 0:
        raise PreparationError("frontend raw trajectory has zero poses")
    if tracked_poses > dense_frames:
        raise PreparationError("frontend raw trajectory pose count exceeds dense frames")
    if dense_frames > compatible_frames:
        raise PreparationError("frontend dense frames exceed compatible dataset frames")
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
    optional_stereo_policy: str = "strict",
    frontend_cache: str | Path | None = None,
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
    if optional_stereo_policy not in OPTIONAL_STEREO_POLICIES:
        raise PreparationError(f"unknown optional_stereo_policy: {optional_stereo_policy}")

    for left_name, _ in STEREO_REPORTS:
        _validate_left_stereo_report(left_path / left_name, session_path)
    orientation_trajectory = _orientation_trajectory_from_left_cache(left_path, session_path)

    frontend_reuse: dict[str, Any] | None = None
    if frontend_cache is not None:
        cache_path = Path(frontend_cache).resolve()
        frontend_reuse = _copy_frontend_cache(
            cache_path,
            output_path,
            _validate_frontend_cache(cache_path, session_path),
        )
    else:
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
    rejected_optional_stereo_reports: list[dict[str, Any]] = []
    for left_name, right_name in STEREO_REPORTS:
        is_primary = (left_name, right_name) in PRIMARY_STEREO_REPORTS
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
        allowed = STRICT_DERIVE_STEREO_RETURNCODES
        if optional_stereo_policy == "reject_window" and not is_primary:
            allowed = OPTIONAL_DERIVE_STEREO_RETURNCODES
        rc = _run_command(
            command,
            stage=right_report.stem,
            output=output_path,
            timeout_s=stage_timeout_s,
            allowed_returncodes=allowed,
        )
        report = _json(right_report)
        if is_primary or rc == 0:
            _validate_right_stereo_pass(
                report, right_report, session_path, output_path / "trajectory_frames.csv"
            )
            stereo_outputs.append(right_report)
        else:
            rejected = _validate_right_stereo_optional_rejection(
                report, right_report, session_path, output_path / "trajectory_frames.csv"
            )
            rejected.update(
                source_left_report=str((left_path / left_name).resolve()),
                returncode=rc,
            )
            rejected_optional_stereo_reports.append(rejected)

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
            "rejected_optional_stereo_reports": rejected_optional_stereo_reports,
            "imu_metric_trajectory": str(imu_output.resolve()),
            "imu_scale_report": str(imu_report.resolve()),
            "imu_alignment_args": {"node_stride": 10, "max_hop": 1, "stream": "infrared_right"},
            "orientation_source": "onboard_orientation_trajectory_from_left_imu_scale_report",
            "orientation_trajectory": str(orientation_trajectory),
            "external_ground_truth_used": False,
            "optional_stereo_policy": optional_stereo_policy,
            "frontend_cache_reuse": frontend_reuse,
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
    parser.add_argument("--optional-stereo-policy", choices=OPTIONAL_STEREO_POLICIES, default="strict")
    parser.add_argument("--frontend-cache", type=Path)
    args = parser.parse_args(argv)
    prepare(
        args.session,
        args.left_dir,
        args.output,
        frontend_timeout_s=args.frontend_timeout_s,
        stage_timeout_s=args.stage_timeout_s,
        optional_stereo_policy=args.optional_stereo_policy,
        frontend_cache=args.frontend_cache,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
