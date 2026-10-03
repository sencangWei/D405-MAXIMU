"""CPU-only benchmark for the isolated metric analytic-Jacobian candidate.

Compares the original central-difference metric factor linearization against
metric_factor_analytic_candidate on the seven frozen retained contexts.  This is
not an ATE/evaluator run and does not import or monkeypatch the production
solver.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any
import hashlib

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np
import torch


BASE = Path(__file__).resolve().parent
FROZEN = BASE / "metric_relative_joint_native_falsifier_v2"
CASES = (
    "left877",
    "left1044",
    "right587",
    "right592",
    "right613",
    "passing_held2_752",
    "passing_take01_738",
)
POSE_KEYS = ("pre", "control", "filtered")
SOURCE_SUMMARY = FROZEN / "summary.json"
SOURCE_SUMMARY_SCHEMA = "metric_relative_joint_native_falsifier_v1"
SOURCE_REPORT_REQUIRED_KEYS = {
    "id",
    "status",
    "diagnostic_only",
    "external_ground_truth_used",
    "production_promoted",
    "poses_sha256",
    "metric_factors",
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(paths: list[Path]) -> dict[str, str]:
    return {str(path): _sha256_file(path) for path in paths}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(path)
    spec.loader.exec_module(module)
    return module


def _validate_source_summary() -> dict[str, Any]:
    summary = json.loads(SOURCE_SUMMARY.read_text())
    if summary.get("schema") != SOURCE_SUMMARY_SCHEMA:
        raise ValueError(f"unexpected source summary schema: {summary.get('schema')!r}")
    if summary.get("external_ground_truth_used") is not False:
        raise ValueError("source summary unexpectedly used external ground truth")
    if summary.get("production_promoted") is not False:
        raise ValueError("source summary unexpectedly marked production promoted")
    return {
        "path": str(SOURCE_SUMMARY),
        "schema": summary["schema"],
        "sha256": _sha256_file(SOURCE_SUMMARY),
    }


def _validate_source_report(case: str, report: dict[str, Any], poses_sha256: str) -> None:
    missing = sorted(SOURCE_REPORT_REQUIRED_KEYS - set(report))
    if missing:
        raise ValueError(f"{case}: source report missing keys {missing}")
    if report["id"] != case:
        raise ValueError(f"{case}: source id mismatch {report['id']!r}")
    if report["status"] != "DIAGNOSTIC_COMPLETE":
        raise ValueError(f"{case}: source status mismatch {report['status']!r}")
    if report["diagnostic_only"] is not True:
        raise ValueError(f"{case}: source report is not diagnostic-only")
    if report["external_ground_truth_used"] is not False:
        raise ValueError(f"{case}: source report unexpectedly used external ground truth")
    if report["production_promoted"] is not False:
        raise ValueError(f"{case}: source report unexpectedly marked production promoted")
    if report["poses_sha256"] != poses_sha256:
        raise ValueError(f"{case}: poses_sha256 mismatch")
    if not isinstance(report["metric_factors"], list) or not report["metric_factors"]:
        raise ValueError(f"{case}: source report has no metric factors")


def _rel_abs(diff: np.ndarray, ref: np.ndarray) -> tuple[float, float]:
    abs_max = float(np.max(np.abs(diff))) if diff.size else 0.0
    ref_max = float(np.max(np.abs(ref))) if ref.size else 0.0
    rel_max = abs_max / max(ref_max, 1e-30)
    return abs_max, rel_max


def _factor_stats(reference: tuple[np.ndarray, np.ndarray, np.ndarray], candidate: tuple[np.ndarray, np.ndarray, np.ndarray]) -> dict[str, float]:
    residual_ref, jac_ref, info_ref = reference
    residual_new, jac_new, info_new = candidate
    h_ref = jac_ref.T @ info_ref @ jac_ref
    h_new = jac_new.T @ info_new @ jac_new
    g_ref = jac_ref.T @ info_ref @ residual_ref
    g_new = jac_new.T @ info_new @ residual_new
    r_abs, r_rel = _rel_abs(residual_new - residual_ref, residual_ref)
    j_abs, j_rel = _rel_abs(jac_new - jac_ref, jac_ref)
    h_abs, h_rel = _rel_abs(h_new - h_ref, h_ref)
    g_abs, g_rel = _rel_abs(g_new - g_ref, g_ref)
    info_abs, info_rel = _rel_abs(info_new - info_ref, info_ref)
    return {
        "residual_abs_max": r_abs,
        "residual_rel_max": r_rel,
        "jacobian_abs_max": j_abs,
        "jacobian_rel_max": j_rel,
        "hessian_abs_max": h_abs,
        "hessian_rel_max": h_rel,
        "gradient_abs_max": g_abs,
        "gradient_rel_max": g_rel,
        "information_abs_max": info_abs,
        "information_rel_max": info_rel,
    }


def _merge_max(dst: dict[str, float], src: dict[str, float]) -> None:
    for key, value in src.items():
        dst[key] = max(float(dst.get(key, 0.0)), float(value))


def run_benchmark() -> dict[str, Any]:
    original_path = BASE / "metric_relative_pose_factor.py"
    candidate_path = BASE / "metric_factor_analytic_candidate.py"
    benchmark_path = Path(__file__).resolve()
    source_paths = [original_path, candidate_path, benchmark_path, SOURCE_SUMMARY]
    for case in CASES:
        source_paths.extend([FROZEN / case / "report.json", FROZEN / case / "poses.pt"])
    snapshot_before = _snapshot(source_paths)
    source_summary = _validate_source_summary()

    original = _load_module(original_path, "metric_relative_pose_factor_original_for_benchmark")
    candidate = _load_module(candidate_path, "metric_factor_analytic_candidate_for_benchmark")
    report: dict[str, Any] = {
        "schema": "metric_factor_analytic_candidate_benchmark_v2",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "ate_or_precision_evaluation": False,
        "production_promoted": False,
        "final_pose_equivalence_tested": False,
        "fulltrajectory_equivalence_tested": False,
        "candidate_file": str(candidate_path),
        "original_file": str(original_path),
        "benchmark_file": str(benchmark_path),
        "frozen_root": str(FROZEN),
        "source_summary": source_summary,
        "code_sha256": {
            "original": snapshot_before[str(original_path)],
            "candidate": snapshot_before[str(candidate_path)],
            "benchmark": snapshot_before[str(benchmark_path)],
        },
        "source_sha256_snapshot_before": snapshot_before,
        "cases": [],
        "overall": {
            "case_count": 0,
            "pose_key_count": 0,
            "factor_evaluations": 0,
            "original_time_s": 0.0,
            "candidate_time_s": 0.0,
            "speedup_original_over_candidate": None,
        },
    }
    overall_max: dict[str, float] = {}
    for case in CASES:
        report_path = FROZEN / case / "report.json"
        poses_path = FROZEN / case / "poses.pt"
        source = json.loads(report_path.read_text())
        report_sha256 = snapshot_before[str(report_path)]
        poses_sha256 = snapshot_before[str(poses_path)]
        _validate_source_report(case, source, poses_sha256)
        factors = source["metric_factors"]
        poses_by_key = torch.load(poses_path, map_location="cpu", weights_only=True)["poses"]
        case_entry: dict[str, Any] = {
            "case": case,
            "factor_count": len(factors),
            "source_report_sha256": report_sha256,
            "poses_sha256": poses_sha256,
            "declared_poses_sha256": source["poses_sha256"],
            "source_status": source["status"],
            "source_id": source["id"],
            "pose_keys": [],
            "max": {},
        }
        case_max: dict[str, float] = {}
        for pose_key in POSE_KEYS:
            poses = poses_by_key[pose_key].detach().cpu().double().numpy()
            original_results = []
            t0 = time.perf_counter()
            for factor in factors:
                original_results.append(original.factor_linearization(poses, factor))
            original_time = time.perf_counter() - t0

            candidate_results = []
            t0 = time.perf_counter()
            for factor in factors:
                candidate_results.append(candidate.factor_linearization(poses, factor))
            candidate_time = time.perf_counter() - t0

            pose_max: dict[str, float] = {}
            for ref, new in zip(original_results, candidate_results):
                _merge_max(pose_max, _factor_stats(ref, new))
            _merge_max(case_max, pose_max)
            _merge_max(overall_max, pose_max)
            factor_count = len(factors)
            case_entry["pose_keys"].append({
                "pose_key": pose_key,
                "factor_count": factor_count,
                "original_time_s": original_time,
                "candidate_time_s": candidate_time,
                "speedup_original_over_candidate": original_time / candidate_time if candidate_time > 0.0 else None,
                "max": pose_max,
            })
            report["overall"]["pose_key_count"] += 1
            report["overall"]["factor_evaluations"] += factor_count
            report["overall"]["original_time_s"] += original_time
            report["overall"]["candidate_time_s"] += candidate_time
        case_entry["max"] = case_max
        report["cases"].append(case_entry)
        report["overall"]["case_count"] += 1
    report["overall"]["max"] = overall_max
    candidate_time = float(report["overall"]["candidate_time_s"])
    report["overall"]["speedup_original_over_candidate"] = (
        float(report["overall"]["original_time_s"]) / candidate_time if candidate_time > 0.0 else None
    )
    snapshot_after = _snapshot(source_paths)
    if snapshot_after != snapshot_before:
        changed = sorted(path for path in snapshot_before if snapshot_before[path] != snapshot_after.get(path))
        raise RuntimeError(f"source files changed during benchmark: {changed}")
    report["source_sha256_snapshot_after"] = snapshot_after
    return report


def _write_new_output(path: Path, report: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing benchmark output: {path}")
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=BASE / "metric_factor_analytic_candidate_benchmark_v2.json")
    args = parser.parse_args()
    report = run_benchmark()
    _write_new_output(args.output, report)
    print(json.dumps(report["overall"], indent=2, sort_keys=True))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
