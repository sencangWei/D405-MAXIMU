#!/usr/bin/env python3
"""Fit and cross-validate one rigid world-Z rotation across VINS trajectories."""

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np


def load_xyz(path: Path) -> np.ndarray:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [[float(row["x"]), float(row["y"]), float(row["z"])] for row in csv.DictReader(handle)]
    points = np.asarray(rows, dtype=float)
    if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError(f"invalid trajectory: {path}")
    return points


def session_covariance(points: np.ndarray, *, require_planar: bool) -> np.ndarray:
    centered = points - np.mean(points, axis=0)
    covariance = centered.T @ centered / len(points)
    eigenvalues = np.linalg.eigvalsh(covariance)
    trace = float(np.trace(covariance))
    if not np.isfinite(trace) or trace <= 1e-15:
        raise ValueError("degenerate trajectory: zero spatial variance")
    if eigenvalues[1] <= trace * 1e-8:
        raise ValueError("degenerate trajectory: insufficient in-plane rank")
    if require_planar and eigenvalues[0] / eigenvalues[1] > 0.1:
        raise ValueError("trajectory is not sufficiently planar for world-Z fitting")
    return covariance / trace


def fit_normal(sessions: list[np.ndarray], *, validate_geometry: bool = False) -> np.ndarray:
    if not sessions:
        raise ValueError("at least one trajectory is required")
    covariance = np.zeros((3, 3), dtype=float)
    for points in sessions:
        covariance += session_covariance(points, require_planar=validate_geometry)
    eigenvalues, vectors = np.linalg.eigh(covariance / len(sessions))
    if validate_geometry and eigenvalues[1] - eigenvalues[0] <= 0.01:
        raise ValueError("planar sessions do not identify a stable shared normal")
    normal = vectors[:, 0]
    return normal if normal[2] >= 0 else -normal


def rotation_to_world_z(normal: np.ndarray) -> np.ndarray:
    normal = np.asarray(normal, dtype=float)
    norm = np.linalg.norm(normal)
    if not np.isfinite(norm) or norm <= 1e-15:
        raise ValueError("normal must be finite and nonzero")
    normal = normal / norm
    target = np.array([0.0, 0.0, 1.0])
    cross = np.cross(normal, target)
    sine = np.linalg.norm(cross)
    cosine = float(np.dot(normal, target))
    if sine < 1e-12:
        return np.eye(3) if cosine >= 0 else np.diag([1.0, -1.0, -1.0])
    axis = cross / sine
    skew = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    return np.eye(3) + skew * sine + (skew @ skew) * (1.0 - cosine)


def metrics(points: np.ndarray, rotation: np.ndarray, *, include_plane: bool = True) -> dict:
    rotated = (rotation @ points.T).T
    z = rotated[:, 2]
    result = {
        "samples": len(rotated),
        "z_span_m": float(np.ptp(z)),
        "z_p5_p95_m": float(np.percentile(z, 95) - np.percentile(z, 5)),
    }
    if include_plane:
        normal = fit_normal([rotated])
        residual = (rotated - np.mean(rotated, axis=0)) @ normal
        result.update(
            plane_tilt_deg=float(math.degrees(math.acos(np.clip(normal[2], -1.0, 1.0)))),
            plane_residual_rms_m=float(np.sqrt(np.mean(residual**2))),
        )
    return result


def parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    name, path = value.split("=", 1)
    return name, Path(path).resolve()


def named_paths_to_dict(items: list[tuple[str, Path]]) -> dict[str, Path]:
    result = {}
    for name, path in items:
        if not name:
            raise ValueError("session name must not be empty")
        if name in result:
            raise ValueError(f"duplicate session name: {name}")
        result[name] = path
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_metadata(paths: dict[str, Path], role: str) -> list[dict]:
    return [
        {"name": name, "role": role, "path": str(path), "sha256": sha256_file(path)}
        for name, path in paths.items()
    ]


def elevation_retention_passes(retention: float) -> bool:
    return math.isfinite(retention) and retention >= 0.8


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--planar", action="append", required=True, type=parse_named_path)
    parser.add_argument("--elevation", action="append", required=True, type=parse_named_path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    try:
        planar_paths = named_paths_to_dict(args.planar)
        elevation_paths = named_paths_to_dict(args.elevation)
    except ValueError as error:
        parser.error(str(error))
    planar = {name: load_xyz(path) for name, path in planar_paths.items()}
    if len(planar) < 3:
        parser.error("at least three planar sessions are required for leave-one-out validation")
    elevation = {name: load_xyz(path) for name, path in elevation_paths.items()}

    identity = np.eye(3)
    normal = fit_normal(list(planar.values()), validate_geometry=True)
    rotation = rotation_to_world_z(normal)
    report = {
        "format_version": 2,
        "model": "one rigid output rotation; per-session translation removed only while fitting",
        "provenance": {
            "argv": [str(Path(sys.argv[0]).resolve()), *sys.argv[1:]],
            "inputs": input_metadata(planar_paths, "known_planar")
            + input_metadata(elevation_paths, "true_elevation"),
        },
        "acceptance_thresholds": {
            "held_out_planar_max_z_p5_p95_m": 0.010,
            "held_out_planar_must_not_worsen": True,
            "elevation_min_span_retention_fraction": 0.8,
        },
        "fit": {
            "normal_xyz": normal.tolist(),
            "R_world_z_from_vins_world": rotation.tolist(),
            "orthogonality_error": float(np.linalg.norm(rotation.T @ rotation - identity)),
            "determinant": float(np.linalg.det(rotation)),
        },
        "planar_full_fit": {},
        "planar_leave_one_out": {},
        "elevation_safety": {},
    }

    for name, points in planar.items():
        report["planar_full_fit"][name] = {
            "baseline": metrics(points, identity),
            "candidate": metrics(points, rotation),
        }

    held_out_passes = []
    for held_out, points in planar.items():
        training = [value for name, value in planar.items() if name != held_out]
        loo_rotation = rotation_to_world_z(fit_normal(training, validate_geometry=True))
        baseline = metrics(points, identity)
        candidate = metrics(points, loo_rotation)
        passed = candidate["z_p5_p95_m"] <= 0.010 and candidate["z_p5_p95_m"] <= baseline["z_p5_p95_m"]
        held_out_passes.append(passed)
        report["planar_leave_one_out"][held_out] = {
            "training_sessions": [name for name in planar if name != held_out],
            "R_world_z_from_vins_world": loo_rotation.tolist(),
            "baseline": baseline,
            "candidate": candidate,
            "result": "PASS" if passed else "FAIL",
        }

    elevation_passes = []
    for name, points in elevation.items():
        baseline = metrics(points, identity, include_plane=False)
        candidate = metrics(points, rotation, include_plane=False)
        retention = candidate["z_span_m"] / baseline["z_span_m"] if baseline["z_span_m"] > 0 else math.nan
        passed = elevation_retention_passes(retention)
        elevation_passes.append(passed)
        report["elevation_safety"][name] = {
            "baseline": baseline,
            "candidate": candidate,
            "z_span_retention_fraction": retention,
            "result": "PASS" if passed else "FAIL",
        }

    report["result"] = "PASS" if all(held_out_passes) and all(elevation_passes) else "FAIL"
    report["activation"] = (
        "FORBIDDEN_PENDING_END_TO_END_VALIDATION"
        if report["result"] == "PASS"
        else "FORBIDDEN_VALIDATION_FAILED"
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "out": str(args.out)}, ensure_ascii=False))
    return 0 if report["result"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
