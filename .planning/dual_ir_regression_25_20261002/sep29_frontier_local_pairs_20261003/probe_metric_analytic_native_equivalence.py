"""Native final-pose equivalence probe for the isolated analytic metric factor.

This diagnostic does not patch production code.  It loads a frozen metric-joint
native context, verifies its source bindings, replays the original finite-
difference metric solve, then monkeypatches only the imported Python
``factor_linearization`` callable inside this process to replay the same solve
with ``metric_factor_analytic_candidate``.  The callable is restored in a
``finally`` block.

No ATE, full trajectory, frontend, capture, or production-promotion claim is
made here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np
import torch
from scipy.spatial.transform import Rotation


BASE = Path(__file__).resolve().parent
ROOT = Path("/home/robot/ego_vio_humble")
FROZEN = BASE / "metric_relative_joint_native_falsifier_v2"
DEFAULT_OUTPUT = BASE / "metric_analytic_native_equivalence_v2"
SCHEMA = "metric_analytic_native_equivalence_v2"
GB = 1024 ** 3
MIN_FREE_BYTES = 8 * GB

for import_root in (BASE, Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from joint_metric_native_solver import array_hash, solve_joint  # noqa: E402
from probe_dense_native_graph import _validate_graph_args  # noqa: E402
from probe_stereo_depth_shape_native_graph import validate_job_bindings  # noqa: E402


def sha(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(path)
    spec.loader.exec_module(module)
    return module


def load_backend():
    build = _load_module(BASE / "native_derivative_audit_v1/build_backend.py", "metric_analytic_equiv_backend_build")
    try:
        return build.build_extension()
    except OSError as exc:
        cached = BASE / "native_derivative_audit_v1/_torch_extension_build/mast3r_native_derivative_audit_v1.so"
        if "CUDA_HOME" not in str(exc) or not cached.is_file():
            raise
        build.pinned.verify_source_hashes()
        spec = importlib.util.spec_from_file_location("mast3r_native_derivative_audit_v1", cached)
        module = importlib.util.module_from_spec(spec)
        if spec.loader is None:
            raise ImportError(cached)
        spec.loader.exec_module(module)
        return module


def snapshot(paths: list[Path]) -> dict[str, str]:
    return {str(path.resolve()): sha(path) for path in dict.fromkeys(paths)}


def assert_snapshot_unchanged(before: dict[str, str]) -> None:
    after = snapshot([Path(path) for path in before])
    if after != before:
        changed = sorted(path for path, digest in before.items() if after.get(path) != digest)
        raise RuntimeError(f"source files changed during native equivalence probe: {changed}")


def _parse_device0_free_bytes(text: str) -> int | None:
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 2 and parts[0] == "0" and parts[1]:
            return int(parts[1]) * 1024 * 1024
    return None


def gpu_free_bytes() -> int | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return _parse_device0_free_bytes(result.stdout)


def memory_guard(graph_path: Path, free_bytes: int | None = None) -> dict[str, Any]:
    graph_bytes = graph_path.stat().st_size
    estimated_required = max(MIN_FREE_BYTES, graph_bytes * 3 + 2 * GB)
    free = gpu_free_bytes() if free_bytes is None else free_bytes
    report = {
        "graph_bytes": graph_bytes,
        "free_bytes": free,
        "min_free_bytes": MIN_FREE_BYTES,
        "estimated_required_bytes": estimated_required,
        "accepted": free is not None and free >= MIN_FREE_BYTES and free >= estimated_required,
    }
    if not report["accepted"]:
        report["reason"] = "GPU_FREE_BELOW_GUARD_OR_UNKNOWN"
    return report


def validate_frozen_case(case: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[Path]]:
    summary_path = FROZEN / "summary.json"
    summary = read_json(summary_path)
    if summary.get("schema") != "metric_relative_joint_native_falsifier_v1":
        raise ValueError("frozen summary schema mismatch")
    if summary.get("diagnostic_only") is not True or summary.get("external_ground_truth_used") is not False:
        raise ValueError("frozen summary must be diagnostic/no-GT")
    if summary.get("production_promoted") is not False:
        raise ValueError("frozen summary unexpectedly promoted")
    rows = {row["id"]: row for row in summary.get("jobs", [])}
    if case not in rows:
        raise ValueError(f"unknown frozen case: {case}")
    row = rows[case]
    case_dir = FROZEN / case
    report_path = case_dir / "report.json"
    poses_path = case_dir / "poses.pt"
    if sha(report_path) != row["report_sha256"]:
        raise ValueError(f"{case}: summary/report hash mismatch")
    report = read_json(report_path)
    if report.get("id") != case or report.get("status") != "DIAGNOSTIC_COMPLETE":
        raise ValueError(f"{case}: frozen report not complete")
    if report.get("diagnostic_only") is not True or report.get("external_ground_truth_used") is not False:
        raise ValueError(f"{case}: frozen report must be diagnostic/no-GT")
    if report.get("production_promoted") is not False:
        raise ValueError(f"{case}: frozen report unexpectedly promoted")
    if len(report.get("metric_factors", [])) != int(row["metric_factor_count"]):
        raise ValueError(f"{case}: metric factor count mismatch")
    if sha(poses_path) != report["poses_sha256"]:
        raise ValueError(f"{case}: poses hash mismatch")
    poses = torch.load(poses_path, map_location="cpu", weights_only=True)
    graph_path = Path(report["job"]["graph"])
    if not poses.get("graph_sha256") or sha(graph_path) != poses["graph_sha256"]:
        raise ValueError(f"{case}: graph hash mismatch")
    validate_job_bindings(report["job"])
    source_paths = [
        summary_path,
        report_path,
        poses_path,
        graph_path,
        BASE / "metric_relative_pose_factor.py",
        BASE / "metric_factor_analytic_candidate.py",
        BASE / "joint_metric_native_solver.py",
        BASE / "native_derivative_audit_v1/inspect.cpp",
        BASE / "native_derivative_audit_v1/inspect.cu",
        BASE / "native_derivative_audit_v1/build_backend.py",
        Path(__file__).resolve(),
    ]
    return summary, report, poses, source_paths


def with_factor_linearization(factor_func: Callable[..., Any], action: Callable[[], Any]) -> Any:
    module = importlib.import_module("metric_relative_pose_factor")
    original = module.factor_linearization
    module.factor_linearization = factor_func
    try:
        return action()
    finally:
        module.factor_linearization = original


def pose_differences(reference: torch.Tensor, candidate: torch.Tensor) -> dict[str, Any]:
    if reference.shape != candidate.shape:
        raise ValueError("pose shapes differ")
    validate_pose_tensor(reference, "reference")
    validate_pose_tensor(candidate, "candidate")
    ref = reference.detach().cpu().double().numpy()
    cand = candidate.detach().cpu().double().numpy()
    translation = np.linalg.norm(cand[:, :3] - ref[:, :3], axis=1)
    logscale = np.abs(np.log(cand[:, 7]) - np.log(ref[:, 7]))
    rotation = []
    for a, b in zip(ref[:, 3:7], cand[:, 3:7]):
        rotation.append(float((Rotation.from_quat(a).inv() * Rotation.from_quat(b)).magnitude()))
    rotation = np.asarray(rotation, dtype=np.float64)
    return {
        "bitwise_exact": bool(torch.equal(reference, candidate)),
        "pose_count": int(reference.shape[0]),
        "translation_max": float(translation.max(initial=0.0)),
        "translation_mean": float(translation.mean() if translation.size else 0.0),
        "rotation_rad_max": float(rotation.max(initial=0.0)),
        "rotation_rad_mean": float(rotation.mean() if rotation.size else 0.0),
        "logscale_max": float(logscale.max(initial=0.0)),
        "logscale_mean": float(logscale.mean() if logscale.size else 0.0),
        "reference_sha256": array_hash(reference.cpu().numpy()),
        "candidate_sha256": array_hash(candidate.cpu().numpy()),
    }


def _pose0_pin_exact(initial: torch.Tensor, value: torch.Tensor) -> bool:
    return bool(torch.equal(initial[0], value[0]))


def validate_pose_tensor(poses: torch.Tensor, label: str) -> None:
    if poses.ndim != 2 or poses.shape[1] != 8:
        raise ValueError(f"{label} pose tensor must be Nx8")
    if not torch.isfinite(poses).all():
        raise ValueError(f"{label} pose tensor contains nonfinite values")
    if not bool((poses[:, 7] > 0).all()):
        raise ValueError(f"{label} pose tensor contains nonpositive scale")


def equal_positive_iteration_counts(*traces: list[dict[str, Any]]) -> bool:
    counts = [len(trace) for trace in traces]
    return bool(counts and all(count > 0 for count in counts) and len(set(counts)) == 1)


def timed_solve(action: Callable[[], tuple[torch.Tensor, list[dict[str, Any]]]]) -> tuple[torch.Tensor, list[dict[str, Any]], float]:
    started = time.monotonic()
    pose, trace = action()
    return pose, trace, time.monotonic() - started


def run_case(case: str, output_root: Path, *, artifact_name: str | None = None, min_free_bytes: int = MIN_FREE_BYTES) -> dict[str, Any]:
    started = time.monotonic()
    summary, frozen_report, frozen_poses, source_paths = validate_frozen_case(case)
    graph_path = Path(frozen_report["job"]["graph"])
    guard = memory_guard(graph_path)
    if guard["free_bytes"] is None or guard["free_bytes"] < min_free_bytes or guard["free_bytes"] < guard["estimated_required_bytes"]:
        return {
            "id": case,
            "status": "DIAGNOSTIC_SKIPPED",
            "skip_reason": guard.get("reason", "GPU_MEMORY_GUARD"),
            "memory_guard": guard,
            "diagnostic_only": True,
            "external_ground_truth_used": False,
            "production_promoted": False,
        }

    import mast3r_slam_backends  # noqa: PLC0415

    source_paths.append(Path(mast3r_slam_backends.__file__))
    before = snapshot(source_paths)
    out = output_root / (artifact_name or case)
    out.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {
        "id": case,
        "artifact_name": out.name,
        "status": "RUNNING",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "ate_or_fulltrajectory_claim": False,
        "memory_guard": guard,
        "source_snapshot_before": before,
        "frozen_report_sha256": before[str((FROZEN / case / "report.json").resolve())],
        "frozen_poses_sha256": before[str((FROZEN / case / "poses.pt").resolve())],
        "graph_sha256": before[str(graph_path.resolve())],
        "metric_factor_count": len(frozen_report["metric_factors"]),
    }
    write_json(out / "report.json", result)
    try:
        graph = torch.load(graph_path, map_location="cpu", weights_only=True)
        args = graph["args"]
        _validate_graph_args(torch, args)
        factors = frozen_report["metric_factors"]
        backend = load_backend()
        original_module = importlib.import_module("metric_relative_pose_factor")
        analytic_module = _load_module(BASE / "metric_factor_analytic_candidate.py", "metric_factor_analytic_candidate_for_native_equiv")

        saved_filtered = frozen_poses["poses"]["filtered"].cpu()
        saved_pre = frozen_poses["poses"]["pre"].cpu()
        if not torch.equal(args[0].cpu(), saved_pre):
            raise ValueError("graph pre pose differs from frozen poses.pt")

        original_pose, original_trace, original_solve_time = timed_solve(lambda: with_factor_linearization(
            original_module.factor_linearization,
            lambda: solve_joint(args, factors, backend),
        ))
        original_pose = original_pose.cpu()
        original_repeat, original_repeat_trace, original_repeat_solve_time = timed_solve(lambda: with_factor_linearization(
            original_module.factor_linearization,
            lambda: solve_joint(args, factors, backend),
        ))
        original_repeat = original_repeat.cpu()
        original_vs_saved = pose_differences(saved_filtered, original_pose)
        original_repeat_diff = pose_differences(original_pose, original_repeat)

        analytic_pose, analytic_trace, analytic_solve_time = timed_solve(lambda: with_factor_linearization(
            analytic_module.factor_linearization,
            lambda: solve_joint(args, factors, backend),
        ))
        analytic_pose = analytic_pose.cpu()
        analytic_repeat, analytic_repeat_trace, analytic_repeat_solve_time = timed_solve(lambda: with_factor_linearization(
            analytic_module.factor_linearization,
            lambda: solve_joint(args, factors, backend),
        ))
        analytic_repeat = analytic_repeat.cpu()

        original_vs_analytic = pose_differences(original_pose, analytic_pose)
        analytic_repeat_diff = pose_differences(analytic_pose, analytic_repeat)
        equal_iterations = equal_positive_iteration_counts(
            original_trace,
            original_repeat_trace,
            analytic_trace,
            analytic_repeat_trace,
        )
        torch.save(
            {
                "initial": args[0].cpu(),
                "saved_filtered": saved_filtered,
                "original": original_pose,
                "original_repeat": original_repeat,
                "analytic": analytic_pose,
                "analytic_repeat": analytic_repeat,
                "original_trace": original_trace,
                "original_repeat_trace": original_repeat_trace,
                "analytic_trace": analytic_trace,
                "analytic_repeat_trace": analytic_repeat_trace,
            },
            out / "poses.pt",
        )
        result.update(
            status="DIAGNOSTIC_COMPLETE" if (
                original_vs_saved["bitwise_exact"]
                and original_repeat_diff["bitwise_exact"]
                and original_vs_analytic["bitwise_exact"]
                and analytic_repeat_diff["bitwise_exact"]
                and _pose0_pin_exact(args[0].cpu(), original_pose)
                and _pose0_pin_exact(args[0].cpu(), original_repeat)
                and _pose0_pin_exact(args[0].cpu(), analytic_pose)
                and _pose0_pin_exact(args[0].cpu(), analytic_repeat)
                and equal_iterations
            ) else "DIAGNOSTIC_FAILED",
            original_replay_matches_frozen_filtered=original_vs_saved["bitwise_exact"],
            original_repeat_exact=original_repeat_diff["bitwise_exact"],
            analytic_matches_original_final_pose=original_vs_analytic["bitwise_exact"],
            analytic_repeat_exact=analytic_repeat_diff["bitwise_exact"],
            original_pin_exact=_pose0_pin_exact(args[0].cpu(), original_pose),
            original_repeat_pin_exact=_pose0_pin_exact(args[0].cpu(), original_repeat),
            analytic_pin_exact=_pose0_pin_exact(args[0].cpu(), analytic_pose),
            analytic_repeat_pin_exact=_pose0_pin_exact(args[0].cpu(), analytic_repeat),
            original_iteration_count=len(original_trace),
            original_repeat_iteration_count=len(original_repeat_trace),
            analytic_iteration_count=len(analytic_trace),
            analytic_repeat_iteration_count=len(analytic_repeat_trace),
            equal_positive_iteration_counts=equal_iterations,
            original_solve_time_s=original_solve_time,
            original_repeat_solve_time_s=original_repeat_solve_time,
            analytic_solve_time_s=analytic_solve_time,
            analytic_repeat_solve_time_s=analytic_repeat_solve_time,
            original_vs_saved_filtered=original_vs_saved,
            original_vs_original_repeat=original_repeat_diff,
            original_vs_analytic=original_vs_analytic,
            analytic_vs_repeat=analytic_repeat_diff,
            poses_sha256=sha(out / "poses.pt"),
        )
    except Exception as exc:
        result.update(
            status="DIAGNOSTIC_FAILED",
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(limit=12),
        )
    finally:
        assert_snapshot_unchanged(before)
        result["source_snapshot_after"] = snapshot(source_paths)
        result["elapsed_s"] = time.monotonic() - started
        write_json(out / "report.json", result)
    return result


def _next_artifact_name(output: Path, case: str) -> str:
    if not (output / case).exists():
        return case
    attempt = 2
    while (output / f"{case}_attempt{attempt}").exists():
        attempt += 1
    return f"{case}_attempt{attempt}"


def run_probe(cases: list[str], output: Path) -> dict[str, Any]:
    if not cases:
        raise ValueError("at least one case is required")
    if output.is_symlink():
        raise FileExistsError(output)
    torch.set_num_threads(1)
    if output.exists():
        summary_path = output / "summary.json"
        if not summary_path.is_file():
            raise FileExistsError(output)
        summary = read_json(summary_path)
        if summary.get("schema") != SCHEMA:
            raise ValueError("existing output summary schema mismatch")
        if summary.get("runner_sha256") != sha(Path(__file__).resolve()):
            raise ValueError("existing output probe source hash mismatch")
        summary.setdefault("jobs", [])
        summary.setdefault("cases_requested", [])
        summary["cases_requested"].extend(cases)
    else:
        output.mkdir(parents=False)
        summary = {
            "schema": SCHEMA,
            "diagnostic_only": True,
            "external_ground_truth_used": False,
            "production_promoted": False,
            "ate_or_fulltrajectory_claim": False,
            "runner_sha256": sha(Path(__file__).resolve()),
            "cases_requested": cases,
            "jobs": [],
        }
    write_json(output / "summary.json", summary)
    for case in cases:
        row = run_case(case, output, artifact_name=_next_artifact_name(output, case))
        summary["jobs"].append(row)
        write_json(output / "summary.json", summary)
        print(json.dumps({k: row.get(k) for k in ("id", "status", "skip_reason", "error") if k in row}, allow_nan=False), flush=True)
        if row["status"] == "DIAGNOSTIC_SKIPPED":
            break
    summary["status"] = "DIAGNOSTIC_COMPLETE" if all(row["status"] == "DIAGNOSTIC_COMPLETE" for row in summary["jobs"]) else "DIAGNOSTIC_INCOMPLETE_OR_FAILED"
    write_json(output / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case", action="append")
    args = parser.parse_args(argv)
    summary = run_probe(args.case or ["right587"], args.output)
    return 0 if summary["status"] == "DIAGNOSTIC_COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
