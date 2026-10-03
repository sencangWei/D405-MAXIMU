#!/usr/bin/env python3
"""Evaluate one fresh metric-joint MASt3R LEFT/RIGHT frontend pair.

This is an orchestration helper only.  It calls existing source-report,
dual-IR, constant-gauge, physical-stereo-lever, and frozen scoring entry
points.  It does not implement a new optimizer, tune parameters, or promote
the result to production.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
import traceback
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from experimental_mast3r_metric_joint_adapter import context_for_source  # noqa: E402

TOOL = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
DEFAULT_TOOLCHAIN_PYTHON = TOOL / ".venv/bin/python"
FORMAL_VINS_CONFIG = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/"
    "docker2_release/formal_runtime_calibration/vins_config.yaml"
)
IMU_CALIBRATION = ROOT / "config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"
TD_S = "-0.009109323"
SCHEMA = "umi_metric_joint_frontend_fresh_eval_v1"
INVENTORY_SCHEMA = "umi_metric_joint_frontend_fresh_inventory_v1"
FRONTEND_READY_STATUS = "FRONTEND_COMPLETE_NOT_SCORED"
SYMMETRIC_VARIANT = "both"
OPTIONAL_RETURN_CODES = (0, 2)

STEREO_WINDOWS = (
    {
        "left_report": "stereo_scale_bidirectional_report.json",
        "right_report": "stereo_scale_right_report.json",
        "output": "trajectory_stereo_bidirectional.csv",
        "args": ("--frame-step", "5", "--max-hop", "5"),
    },
    {
        "left_report": "stereo_scale_long_hops_report.json",
        "right_report": "stereo_scale_long_hops_right_report.json",
        "output": "trajectory_stereo_long_hops.csv",
        "args": ("--frame-step", "5", "--hop-values", "8,12,16"),
    },
    {
        "left_report": "stereo_scale_dense10hz_report.json",
        "right_report": "stereo_scale_dense10hz_right_report.json",
        "output": "trajectory_stereo_dense10hz.csv",
        "args": ("--frame-step", "3", "--max-hop", "3"),
    },
    {
        "left_report": "stereo_scale_multisecond_report.json",
        "right_report": "stereo_scale_multisecond_right_report.json",
        "output": "trajectory_stereo_multisecond.csv",
        "args": ("--frame-step", "5", "--hop-values", "24,32,48"),
    },
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(paths: Sequence[Path]) -> dict[str, str]:
    return {str(path.resolve()): file_hash(path) for path in dict.fromkeys(paths)}


def assert_unchanged(before: dict[str, str]) -> None:
    after = snapshot([Path(path) for path in before])
    if after != before:
        changed = sorted(path for path, digest in before.items() if after.get(path) != digest)
        raise RuntimeError(f"consumed input changed during evaluation: {changed[:3]}")


def _stage_log_name(stage: str) -> str:
    keep = [ch if ch.isalnum() or ch in "._-" else "_" for ch in stage]
    return "".join(keep).strip("._") + ".log"


def csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as stream:
        return max(sum(1 for _row in csv.DictReader(stream)), 0)


def csv_times(path: Path) -> list[float]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if "t_sec" not in (reader.fieldnames or ()):
            raise ValueError(f"CSV is missing t_sec: {path}")
        values = []
        for row in reader:
            text = row.get("t_sec", "")
            try:
                value = float(text)
            except ValueError as error:
                raise ValueError(f"non-numeric t_sec in {path}: {text!r}") from error
            if not math.isfinite(value):
                raise ValueError(f"non-finite t_sec in {path}: {text!r}")
            values.append(value)
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate t_sec values in {path}")
    if any(later <= earlier for earlier, later in zip(values, values[1:])):
        raise ValueError(f"t_sec values are not strictly increasing in {path}")
    return values


def _times_match(left: Sequence[float], right: Sequence[float], *, tolerance_s: float = 1e-6) -> bool:
    return len(left) == len(right) and all(abs(a - b) <= tolerance_s for a, b in zip(left, right))


def _sha_field(manifest: dict[str, Any]) -> str | None:
    for key in ("trajectory_frames_sha256", "trajectory_sha256", "raw_trajectory_sha256"):
        value = manifest.get(key)
        if isinstance(value, str):
            return value
    return None


def _path_from_record(value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    return Path(value).resolve()


def _same_declared_path(value: Any, expected: Path) -> bool:
    declared = _path_from_record(value)
    return declared is not None and declared == expected.resolve()


def _metric_joint_enabled(config_path: Path) -> bool:
    if not config_path.is_file():
        raise ValueError(f"frontend config is missing: {config_path}")
    try:
        import yaml  # type: ignore
    except ImportError as error:  # pragma: no cover - PyYAML is part of the project env.
        raise RuntimeError("PyYAML is required to read metric-joint frontend config") from error
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    tracking = config.get("tracking")
    if not isinstance(tracking, dict) or not isinstance(tracking.get("metric_relative_joint"), bool):
        raise ValueError(f"frontend config must declare tracking.metric_relative_joint: {config_path}")
    return bool(tracking["metric_relative_joint"])


def _required_string(container: dict[str, Any], key: str, label: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} missing {key}")
    return value


def _validate_actual_context(label: str, context_path: Path, eye: str, expected_session: Path) -> dict[str, Any]:
    context = read_json(context_path)
    required = {
        "schema",
        "external_ground_truth_used",
        "eye",
        "dataset",
        "paired_left_dataset",
        "input_sha256",
        "code_sha256",
    }
    if set(context) != required or context.get("schema") != "umi_metric_relative_joint_frontend_context_v1":
        raise ValueError(f"{label} frontend context fields/schema mismatch")
    if context.get("external_ground_truth_used") is not False:
        raise ValueError(f"{label} frontend context used external ground truth")
    if context.get("eye") != eye:
        raise ValueError(f"{label} frontend context eye mismatch: {context.get('eye')}")
    dataset = Path(_required_string(context, "dataset", f"{label} context dataset")).resolve(strict=True)
    paired = Path(_required_string(context, "paired_left_dataset", f"{label} context paired_left_dataset")).resolve(strict=True)
    expected_context = context_for_source(dataset, paired, eye)
    if context != expected_context:
        raise ValueError(f"{label} frontend context source/code binding mismatch")
    native_manifest = read_json(dataset / "dataset_manifest.json")
    paired_manifest = read_json(paired / "dataset_manifest.json")
    if native_manifest.get("source_session") != str(expected_session.resolve()):
        raise ValueError(f"{label} frontend native dataset source session mismatch")
    if paired_manifest.get("source_session") != str(expected_session.resolve()):
        raise ValueError(f"{label} frontend paired dataset source session mismatch")
    return context


def validate_frontend(directory: Path, eye: str, *, expected_session: Path) -> dict[str, Any]:
    run_manifest = directory / "run_manifest.json"
    context = directory / "context.json"
    frames = directory / "dataset" / "frames.csv"
    trajectory = directory / "trajectory_frames.csv"
    required = [run_manifest, context, frames, trajectory]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"{eye} frontend missing required outputs: {missing}")
    manifest = read_json(run_manifest)
    context_data = read_json(context)
    if manifest.get("schema") != "umi_mast3r_run_v1":
        raise ValueError(f"{eye} frontend run_manifest schema mismatch")
    if manifest.get("status") != FRONTEND_READY_STATUS:
        raise ValueError(f"{eye} frontend status is not {FRONTEND_READY_STATUS}: {manifest.get('status')}")
    if manifest.get("eye") not in (None, eye):
        raise ValueError(f"{eye} frontend eye mismatch: {manifest.get('eye')}")
    if manifest.get("slam_supervision") is not False:
        raise ValueError(f"{eye} frontend used SLAM supervision")
    if manifest.get("external_ground_truth_used") not in (None, False):
        raise ValueError(f"{eye} frontend used external ground truth")
    _validate_actual_context(eye, context, eye, expected_session)
    declared_context = _path_from_record(manifest.get("context"))
    if declared_context is not None and declared_context != context.resolve():
        raise ValueError(f"{eye} frontend run_manifest context path mismatch")
    declared_context_sha = manifest.get("context_sha256")
    if declared_context_sha is not None and declared_context_sha != file_hash(context):
        raise ValueError(f"{eye} frontend run_manifest context sha mismatch")
    if not any(
        _same_declared_path(container.get(key), expected_session)
        for container in (manifest, context_data)
        for key in ("source_session", "session", "d405_session")
    ):
        raise ValueError(f"{eye} frontend source session does not match selected record")
    frame_count = csv_row_count(frames)
    trajectory_count = csv_row_count(trajectory)
    if frame_count <= 0 or trajectory_count != frame_count:
        raise ValueError(f"{eye} frontend trajectory coverage incomplete: {trajectory_count}/{frame_count}")
    if not _times_match(csv_times(frames), csv_times(trajectory)):
        raise ValueError(f"{eye} frontend trajectory timestamps do not match dataset frames")
    for key in ("input_frame_count", "trajectory_frame_count", "native_pose_count"):
        value = manifest.get(key)
        if value is not None and int(value) != frame_count:
            raise ValueError(f"{eye} frontend {key} does not match full coverage: {value} != {frame_count}")
    trajectory_sha = file_hash(trajectory)
    declared = _sha_field(manifest)
    if declared is None:
        raise ValueError(f"{eye} frontend run_manifest missing trajectory sha256")
    if declared != trajectory_sha:
        raise ValueError(f"{eye} frontend trajectory sha mismatch")
    config_path = _path_from_record(manifest.get("config"))
    if config_path is None:
        raise ValueError(f"{eye} frontend run_manifest missing config")
    if file_hash(config_path) != manifest["config_sha256"]:
        raise ValueError(f"{eye} frontend config sha mismatch")
    checkpoint_path = _path_from_record(manifest.get("checkpoint"))
    if checkpoint_path is None:
        raise ValueError(f"{eye} frontend run_manifest missing checkpoint")
    if file_hash(checkpoint_path) != manifest["checkpoint_sha256"]:
        raise ValueError(f"{eye} frontend checkpoint sha mismatch")
    metric_joint = _metric_joint_enabled(config_path)
    paths = [run_manifest, context, frames, trajectory, config_path, checkpoint_path]
    return {
        "eye": eye,
        "run_manifest": str(run_manifest.resolve()),
        "run_manifest_sha256": file_hash(run_manifest),
        "context": str(context.resolve()),
        "context_sha256": file_hash(context),
        "frames_csv": str(frames.resolve()),
        "frames_csv_sha256": file_hash(frames),
        "trajectory_frames": str(trajectory.resolve()),
        "trajectory_frames_sha256": trajectory_sha,
        "frame_count": frame_count,
        "source_session": str(expected_session.resolve()),
        "consumed_paths": paths,
        "config": str(config_path),
        "config_sha256": manifest.get("config_sha256"),
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": manifest.get("checkpoint_sha256"),
        "metric_relative_joint_enabled": metric_joint,
    }


def find_record(manifest: dict[str, Any], record_id: str) -> dict[str, Any]:
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("source manifest records missing")
    matches = [record for record in records if record.get("id") == record_id]
    if len(matches) != 1:
        raise ValueError(f"record id not unique in manifest: {record_id}")
    return dict(matches[0])


def _run(
    command: list[str],
    runner: Callable[..., Any],
    *,
    stage: str,
    log_dir: Path | None = None,
    allowed: tuple[int, ...] = (0,),
) -> int:
    log_path = log_dir / _stage_log_name(stage) if log_dir is not None else None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as stream:
            stream.write("$ " + shlex.join(command) + "\n")
            stream.flush()
            try:
                result = runner(command, cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT, text=True)
            except TypeError:
                result = runner(command, cwd=str(ROOT))
    else:
        result = runner(command, cwd=str(ROOT))
    rc = int(getattr(result, "returncode", 0))
    if rc not in allowed:
        raise subprocess.CalledProcessError(rc, command, output=stage)
    return rc


def _completed_record_status(summary_path: Path, record_id: str, stage: str) -> None:
    summary = read_json(summary_path)
    results = summary.get("results")
    if not isinstance(results, list):
        raise ValueError(f"{stage} summary missing results: {summary_path}")
    matches = [row for row in results if row.get("id") == record_id]
    if len(matches) != 1 or matches[0].get("status") != "COMPLETED":
        raise ValueError(f"{stage} did not complete selected record {record_id}: {summary_path}")


def _reference_calibration_path(reference_manifest: Path) -> Path:
    reference = read_json(reference_manifest)
    artifacts = reference.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get("calibration"), dict):
        raise ValueError(f"reference manifest missing calibration artifact: {reference_manifest}")
    calibration = _required_string(artifacts["calibration"], "path", "reference calibration artifact")
    return (ROOT / calibration).resolve()


def scoring_input_paths(record: dict[str, Any]) -> list[Path]:
    capture_dir = Path(record["capture_dir"]).resolve()
    capture_manifest = capture_dir / "capture_manifest.json"
    capture = read_json(capture_manifest)
    session = Path(capture["d405_session"]).resolve()
    reference_manifest = Path(record["reference_manifest"]).resolve()
    return [
        capture_manifest,
        capture_dir / "tracker.csv",
        session / "d405_frames.csv",
        reference_manifest,
        _reference_calibration_path(reference_manifest),
        FORMAL_VINS_CONFIG,
    ]


def _verify_score(score_dir: Path, *, estimate: Path, estimate_sha256: str, reference_manifest: Path) -> dict[str, Any]:
    workflow = read_json(score_dir / "workflow_manifest.json")
    if workflow.get("result") != "SCORING_COMPLETED" or workflow.get("estimate_unchanged") is not True:
        raise ValueError(f"scorer did not complete with unchanged estimate: {score_dir}")
    if Path(workflow.get("estimate", "")).resolve() != estimate.resolve():
        raise ValueError(f"scorer estimate path mismatch: {score_dir}")
    if workflow.get("estimate_sha256") != estimate_sha256:
        raise ValueError(f"scorer estimate sha mismatch: {score_dir}")
    if file_hash(estimate) != estimate_sha256:
        raise ValueError(f"body estimate changed during scoring: {estimate}")
    if Path(workflow.get("reference_manifest", "")).resolve() != reference_manifest.resolve():
        raise ValueError(f"scorer reference manifest path mismatch: {score_dir}")
    if workflow.get("reference_manifest_sha256") != file_hash(reference_manifest):
        raise ValueError(f"scorer reference manifest sha mismatch: {score_dir}")
    return read_json(score_dir / "precision.json")


def _mark_failed(output: Path, error: BaseException) -> None:
    summary_path = output / "summary.json"
    if not summary_path.is_file():
        return
    try:
        report = read_json(summary_path)
        report.update(
            status="EVALUATION_FAILED",
            error_type=type(error).__name__,
            error=str(error),
            traceback=traceback.format_exc(limit=8),
        )
        write_json(summary_path, report)
    except Exception:
        return


def _ros_stereo_command(toolchain_python: Path, args: list[str]) -> list[str]:
    quoted = " ".join(shlex.quote(part) for part in [str(toolchain_python), *args])
    script = (
        "set +u; "
        "source /opt/ros/humble/setup.bash; "
        "test -f /home/robot/ros2_ws/install/setup.bash && "
        "source /home/robot/ros2_ws/install/setup.bash || true; "
        "set -u; "
        f"{quoted}"
    )
    return ["bash", "-lc", script]


def run_pipeline(
    *,
    manifest_path: Path,
    record_id: str,
    left_frontend: Path,
    right_frontend: Path,
    output: Path,
    toolchain_python: Path = DEFAULT_TOOLCHAIN_PYTHON,
    command_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output must be new: {output}")
    source_manifest = read_json(manifest_path)
    record = find_record(source_manifest, record_id)
    session = Path(record["session"]).resolve()
    vins_dir = Path(record["vins_dir"]).resolve()
    left = validate_frontend(left_frontend, "left", expected_session=session)
    right = validate_frontend(right_frontend, "right", expected_session=session)
    if left["metric_relative_joint_enabled"] != right["metric_relative_joint_enabled"]:
        raise ValueError("left/right frontend metric_relative_joint mode mismatch")
    script_paths = [
        ROOT / "scripts/align_mast3r_scale_with_stereo.py",
        ROOT / "scripts/derive_right_ir_stereo_scale.py",
        ROOT / "scripts/align_mast3r_scale_with_imu.py",
        ROOT / "scripts/fuse_mast3r_dual_ir_symmetric.py",
        ROOT / "scripts/score_steamvr_slam.py",
        ROOT / "scripts/run_constant_ir_gauge_probe.py",
        ROOT / "scripts/run_physical_stereo_lever_probe.py",
    ]
    consumed_paths = [
        manifest_path,
        FORMAL_VINS_CONFIG,
        IMU_CALIBRATION,
        vins_dir / "vio_corrected_stream.csv",
        *scoring_input_paths(record),
        *script_paths,
        *[Path(path) for path in left["consumed_paths"]],
        *[Path(path) for path in right["consumed_paths"]],
    ]
    consumed_before = snapshot(consumed_paths)
    output.mkdir(parents=True)

    left_cache = output / "left_cache"
    right_cache = output / "right_cache"
    baseline = output / "fresh_symmetric_baseline"
    constant = output / "constant_gauge"
    physical = output / "physical_stereo_lever"
    left_cache.mkdir()
    right_cache.mkdir()
    summary_path = output / "summary.json"
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "RUNNING",
        "development_only": True,
        "blind_test": False,
        "production_promoted": False,
        "accepted": False,
        "external_ground_truth_used_by_solver": False,
        "external_ground_truth_used_for_scoring_only": True,
        "slam_supervision": False,
        "not_full10": True,
        "not_full25": True,
        "record_id": record_id,
        "fresh_frontends": {"left": {k: v for k, v in left.items() if k != "consumed_paths"},
                             "right": {k: v for k, v in right.items() if k != "consumed_paths"}},
        "frontend_mode": "metric_relative_joint" if left["metric_relative_joint_enabled"] else "default_metric_joint_off",
        "right_geometry_source": "left_derived_right_geometry_from_derive_right_ir_stereo_scale",
        "source_manifest": str(manifest_path.resolve()),
        "source_manifest_sha256": file_hash(manifest_path),
        "workflow_parameters": {
            "stereo_max_depth_m": 0.6,
            "imu_td_s": TD_S,
            "imu_node_stride": 10,
            "imu_max_hop": 1,
            "symmetric_max_correction_mm": "none",
            "optional_stereo_policy": "reject_window",
        },
        "output": str(output.resolve()),
        "stages": [],
    }
    write_json(summary_path, report)
    log_dir = output / "logs"

    left_traj = Path(left["trajectory_frames"])
    right_traj = Path(right["trajectory_frames"])

    for index, window in enumerate(STEREO_WINDOWS):
        is_primary = index == 0
        stereo_args = [
            str(ROOT / "scripts/align_mast3r_scale_with_stereo.py"),
            "--session", str(session),
            "--trajectory", str(left_traj),
            "--trajectory-frame", "infrared_left",
            "--motion-estimator", "pnp",
            *window["args"],
            "--max-depth-m", "0.6",
            "--output", str(left_cache / window["output"]),
            "--report", str(left_cache / window["left_report"]),
        ]
        left_rc = _run(
            _ros_stereo_command(toolchain_python, stereo_args),
            command_runner,
            stage=f"left_stereo_{window['left_report']}",
            log_dir=log_dir,
            allowed=(0,) if is_primary else OPTIONAL_RETURN_CODES,
        )
        left_report_path = left_cache / window["left_report"]
        if not left_report_path.is_file():
            raise FileNotFoundError(f"left stereo report was not written: {left_report_path}")
        right_rc = _run([
            sys.executable, str(ROOT / "scripts/derive_right_ir_stereo_scale.py"),
            "--left-stereo-report", str(left_cache / window["left_report"]),
            "--right-trajectory", str(right_traj),
            "--output", str(right_cache / window["right_report"]),
        ], command_runner, stage=f"right_derive_{window['right_report']}", log_dir=log_dir, allowed=(0,) if is_primary else OPTIONAL_RETURN_CODES)
        right_report_path = right_cache / window["right_report"]
        if not right_report_path.is_file():
            raise FileNotFoundError(f"right stereo report was not written: {right_report_path}")
        right_report = read_json(right_report_path)
        if Path(right_report.get("derived_from_left_stereo_report", "")).resolve() != left_report_path.resolve():
            raise ValueError(f"right stereo report is not explicitly left-derived: {right_report_path}")
        report["stages"].append({
            "stage": f"stereo_window_{window['left_report']}",
            "primary": is_primary,
            "left_returncode": left_rc,
            "right_returncode": right_rc,
            "left_log": str((log_dir / _stage_log_name(f"left_stereo_{window['left_report']}")).resolve()),
            "right_log": str((log_dir / _stage_log_name(f"right_derive_{window['right_report']}")).resolve()),
            "retained_for_reject_window_policy": (not is_primary and (left_rc == 2 or right_rc == 2)),
            "left_report": str(left_report_path.resolve()),
            "right_report": str(right_report_path.resolve()),
        })
        write_json(summary_path, report)

    _run([
        sys.executable, str(ROOT / "scripts/align_mast3r_scale_with_imu.py"),
        "--trajectory", str(left_traj),
        "--session", str(session),
        "--stream", "infrared_left",
        "--body-t-camera-yaml", str(FORMAL_VINS_CONFIG),
        "--imu-calibration", str(IMU_CALIBRATION),
        "--orientation-trajectory", str(vins_dir / "vio_corrected_stream.csv"),
        "--stereo-scale-report", str(left_cache / "stereo_scale_bidirectional_report.json"),
        "--td-s", TD_S,
        "--node-stride", "10",
        "--max-hop", "1",
        "--output", str(left_cache / "trajectory_imu_metric.csv"),
        "--report", str(left_cache / "imu_scale_report.json"),
    ], command_runner, stage="left_imu_scale", log_dir=log_dir)
    report["stages"].append({"stage": "left_imu_scale", "returncode": 0, "log": str((log_dir / _stage_log_name("left_imu_scale")).resolve())})
    write_json(summary_path, report)
    _run([
        sys.executable, str(ROOT / "scripts/align_mast3r_scale_with_imu.py"),
        "--trajectory", str(right_traj),
        "--session", str(session),
        "--stream", "infrared_right",
        "--body-t-camera-yaml", str(FORMAL_VINS_CONFIG),
        "--imu-calibration", str(IMU_CALIBRATION),
        "--orientation-trajectory", str(vins_dir / "vio_corrected_stream.csv"),
        "--stereo-scale-report", str(right_cache / "stereo_scale_right_report.json"),
        "--td-s", TD_S,
        "--node-stride", "10",
        "--max-hop", "1",
        "--output", str(right_cache / "imu_metric_trajectory.csv"),
        "--report", str(right_cache / "imu_scale_report.json"),
    ], command_runner, stage="right_imu_scale", log_dir=log_dir)
    report["stages"].append({"stage": "right_imu_scale", "returncode": 0, "log": str((log_dir / _stage_log_name("right_imu_scale")).resolve())})
    write_json(summary_path, report)

    baseline_artifact = baseline / record_id / SYMMETRIC_VARIANT
    _run([
        sys.executable, str(ROOT / "scripts/fuse_mast3r_dual_ir_symmetric.py"),
        "--session", str(session),
        "--left-dir", str(left_cache),
        "--right-dir", str(right_cache),
        "--vins-dir", str(vins_dir),
        "--output-dir", str(baseline_artifact),
        "--max-correction-mm", "none",
        "--optional-stereo-policy", "reject_window",
    ], command_runner, stage="fresh_symmetric", log_dir=log_dir)
    report["stages"].append({"stage": "fresh_symmetric", "returncode": 0, "log": str((log_dir / _stage_log_name("fresh_symmetric")).resolve())})
    write_json(summary_path, report)

    assert_unchanged(consumed_before)
    score_dir = baseline_artifact / "score"
    estimate_path = baseline_artifact / "body_trajectory_fused.csv"
    estimate_sha256 = file_hash(estimate_path)
    reference_manifest = Path(record["reference_manifest"]).resolve()
    score_rc = _run([
        sys.executable, str(ROOT / "scripts/score_steamvr_slam.py"),
        "--capture", str(Path(record["capture_dir"]).resolve()),
        "--estimate", str(estimate_path),
        "--output", str(score_dir),
        "--reference-manifest", str(reference_manifest),
    ], command_runner, stage="fresh_symmetric_score", log_dir=log_dir, allowed=(0, 3))
    report["stages"].append({"stage": "fresh_symmetric_score", "returncode": score_rc, "log": str((log_dir / _stage_log_name("fresh_symmetric_score")).resolve())})
    write_json(summary_path, report)

    precision = _verify_score(score_dir, estimate=estimate_path, estimate_sha256=estimate_sha256, reference_manifest=reference_manifest)
    baseline_summary = {
        "schema": "umi_dual_ir_development_regression_v1",
        "status": "COMPLETED",
        "blind_test": False,
        "production_promoted": False,
        "development_only": True,
        "dataset_count": 1,
        "completed_count": 1,
        "manifest_sha256": file_hash(manifest_path),
        "optional_stereo_policy": "reject_window",
        "results": [{
            "id": record_id,
            "status": "COMPLETED",
            "variants": {SYMMETRIC_VARIANT: {"score": precision}},
            "left_cache": str(left_cache.resolve()),
            "right_cache": str(right_cache.resolve()),
        }],
    }
    write_json(baseline / "summary.json", baseline_summary)

    inventory = dict(source_manifest)
    inventory["schema"] = INVENTORY_SCHEMA
    inventory["source_manifest_path"] = str(manifest_path.resolve())
    inventory["source_manifest_sha256"] = file_hash(manifest_path)
    inventory["development_only"] = True
    inventory["production_promoted"] = False
    inventory["fresh_eval_record_id"] = record_id
    inventory["records"] = [dict(row) for row in source_manifest.get("records", [])]
    for row in inventory["records"]:
        if row.get("id") == record_id:
            row["left_dir"] = str(left_cache.resolve())
            row["right_dir"] = str(right_cache.resolve())
            row["fresh_metric_joint_frontend_eval"] = {
                "schema": "fresh_metric_joint_frontend_source_binding_v1",
                "left_frontend": report["fresh_frontends"]["left"],
                "right_frontend": report["fresh_frontends"]["right"],
                "right_geometry_source": report["right_geometry_source"],
                "baseline_artifact": str(baseline_artifact.resolve()),
                "baseline_artifact_body_trajectory_sha256": file_hash(baseline_artifact / "body_trajectory_fused.csv"),
            }
    inventory_path = output / "fresh_full25_inventory.json"
    write_json(inventory_path, inventory)

    assert_unchanged(consumed_before)
    constant_rc = _run([
        sys.executable, str(ROOT / "scripts/run_constant_ir_gauge_probe.py"),
        "--manifest", str(inventory_path),
        "--baseline", str(baseline),
        "--output", str(constant),
        "--dataset", record_id,
    ], command_runner, stage="constant_gauge", log_dir=log_dir, allowed=(0, 3))
    _completed_record_status(constant / "summary.json", record_id, "constant_gauge")
    report["stages"].append({"stage": "constant_gauge", "returncode": constant_rc, "log": str((log_dir / _stage_log_name("constant_gauge")).resolve())})
    write_json(summary_path, report)
    assert_unchanged(consumed_before)
    physical_rc = _run([
        sys.executable, str(ROOT / "scripts/run_physical_stereo_lever_probe.py"),
        "--manifest", str(inventory_path),
        "--baseline", str(baseline),
        "--constant-gauge", str(constant),
        "--output", str(physical),
        "--dataset", record_id,
    ], command_runner, stage="physical_stereo_lever", log_dir=log_dir, allowed=(0, 3))
    _completed_record_status(physical / "summary.json", record_id, "physical_stereo_lever")
    report["stages"].append({"stage": "physical_stereo_lever", "returncode": physical_rc, "log": str((log_dir / _stage_log_name("physical_stereo_lever")).resolve())})
    assert_unchanged(consumed_before)

    report.update(
        status="COMPLETED",
        left_cache=str(left_cache.resolve()),
        right_cache=str(right_cache.resolve()),
        fresh_baseline=str(baseline.resolve()),
        fresh_inventory=str(inventory_path.resolve()),
        constant_gauge=str(constant.resolve()),
        physical_stereo_lever=str(physical.resolve()),
        consumed_source_guard={
            "guarded_before_sha256": consumed_before,
            "guarded_after_verified": True,
        },
    )
    write_json(summary_path, report)
    return report


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--left-frontend-dir", type=Path, required=True)
    parser.add_argument("--right-frontend-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--toolchain-python", type=Path, default=DEFAULT_TOOLCHAIN_PYTHON)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    try:
        run_pipeline(
            manifest_path=args.manifest,
            record_id=args.record_id,
            left_frontend=args.left_frontend_dir,
            right_frontend=args.right_frontend_dir,
            output=args.output,
            toolchain_python=args.toolchain_python,
        )
    except Exception as error:
        _mark_failed(args.output, error)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
