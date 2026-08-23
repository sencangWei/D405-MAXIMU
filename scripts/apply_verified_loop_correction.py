#!/usr/bin/env python3
"""Smoothly apply an automatically verified loop transform to a VIO CSV.

The correction comes only from AUTO_LOOP_ACCEPT evidence emitted by loop_fusion.
It never reads an expected path shape, endpoint, or operator-provided dimensions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np


ACCEPT_RE = re.compile(
    r"\[AUTO_LOOP_ACCEPT\] current=(?P<current>\d+) matched=(?P<matched>\d+) "
    r"confirmations=(?P<confirmations>\d+).*?"
    r"fused_correction_t_m=(?P<norm>[-+0-9.eE]+).*?"
    r"fused_correction_xyz_m=\((?P<x>[-+0-9.eE]+),"
    r"(?P<y>[-+0-9.eE]+),(?P<z>[-+0-9.eE]+)\).*?"
    r"fused_correction_yaw_deg=(?P<yaw>[-+0-9.eE]+)"
)


@dataclass(frozen=True)
class VerifiedLoopCorrection:
    current: int
    matched: int
    confirmations: int
    translation_m: np.ndarray
    yaw_deg: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reject_path_collisions(inputs: list[Path], outputs: list[Path]) -> None:
    resolved_inputs = {path.resolve() for path in inputs}
    resolved_outputs = [path.resolve() for path in outputs]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ValueError("output and report paths must be distinct")
    collisions = resolved_inputs.intersection(resolved_outputs)
    if collisions:
        raise ValueError(f"output path aliases an input: {sorted(map(str, collisions))}")


def parse_verified_corrections(log_path: Path) -> list[VerifiedLoopCorrection]:
    corrections = []
    for match in ACCEPT_RE.finditer(log_path.read_text(encoding="utf-8", errors="replace")):
        translation = np.array(
            [float(match.group(axis)) for axis in ("x", "y", "z")],
            dtype=float,
        )
        reported_norm = float(match.group("norm"))
        yaw_deg = float(match.group("yaw"))
        if not translation.shape == (3,) or not np.isfinite(translation).all():
            raise ValueError("AUTO_LOOP_ACCEPT translation is non-finite")
        if not math.isfinite(reported_norm) or not math.isfinite(yaw_deg):
            raise ValueError("AUTO_LOOP_ACCEPT correction is non-finite")
        if abs(float(np.linalg.norm(translation)) - reported_norm) > 2e-4:
            raise ValueError("AUTO_LOOP_ACCEPT translation norm is inconsistent")
        corrections.append(
            VerifiedLoopCorrection(
                current=int(match.group("current")),
                matched=int(match.group("matched")),
                confirmations=int(match.group("confirmations")),
                translation_m=translation,
                yaw_deg=yaw_deg,
            )
        )
    return corrections


def correction_consistency(corrections: list[VerifiedLoopCorrection]) -> dict[str, float]:
    if not corrections:
        raise ValueError("no verified loop corrections")
    translations = np.array([item.translation_m for item in corrections])
    yaws = np.array([item.yaw_deg for item in corrections])
    if not np.isfinite(translations).all() or not np.isfinite(yaws).all():
        raise ValueError("verified loop correction contains non-finite values")
    pairwise_translation = [
        float(np.linalg.norm(translations[i] - translations[j]))
        for i in range(len(translations))
        for j in range(i)
    ]
    pairwise_yaw = [
        abs(float(((yaws[i] - yaws[j] + 180.0) % 360.0) - 180.0))
        for i in range(len(yaws))
        for j in range(i)
    ]
    return {
        "max_pairwise_translation_m": max(pairwise_translation, default=0.0),
        "max_pairwise_yaw_deg": max(pairwise_yaw, default=0.0),
    }


def normalized_arc_length(points: np.ndarray) -> np.ndarray:
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 2:
        raise ValueError("trajectory must contain at least two 3D points")
    if not np.isfinite(points).all():
        raise ValueError("trajectory contains non-finite positions")
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(lengths)]
    if cumulative[-1] <= 0.0:
        raise ValueError("trajectory contains no measurable motion")
    return cumulative / cumulative[-1]


def yaw_quaternion(yaw_rad: float) -> np.ndarray:
    return np.array([0.0, 0.0, math.sin(yaw_rad / 2.0), math.cos(yaw_rad / 2.0)])


def quaternion_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return np.array(
        [
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
            lw * rw - lx * rx - ly * ry - lz * rz,
        ]
    )


def apply_smooth_correction(
    points: np.ndarray,
    quaternions_xyzw: np.ndarray,
    correction: VerifiedLoopCorrection,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if quaternions_xyzw.shape != (len(points), 4):
        raise ValueError("trajectory quaternions must be Nx4")
    if not np.isfinite(quaternions_xyzw).all():
        raise ValueError("trajectory contains non-finite quaternions")
    quaternion_norms = np.linalg.norm(quaternions_xyzw, axis=1)
    if np.any(quaternion_norms <= 1e-12):
        raise ValueError("trajectory contains a zero quaternion")
    quaternions_xyzw = quaternions_xyzw / quaternion_norms[:, None]
    progress = normalized_arc_length(points)
    corrected_points = np.empty_like(points)
    corrected_quaternions = np.empty_like(quaternions_xyzw)
    full_yaw_rad = math.radians(correction.yaw_deg)
    for index, fraction in enumerate(progress):
        yaw = fraction * full_yaw_rad
        cosine, sine = math.cos(yaw), math.sin(yaw)
        rotation = np.array(
            [[cosine, -sine, 0.0], [sine, cosine, 0.0], [0.0, 0.0, 1.0]]
        )
        corrected_points[index] = (
            rotation @ points[index]
            + fraction * correction.translation_m
        )
        quaternion = quaternion_multiply(
            yaw_quaternion(yaw), quaternions_xyzw[index]
        )
        corrected_quaternions[index] = quaternion / np.linalg.norm(quaternion)
    if not np.isfinite(corrected_points).all() or not np.isfinite(
        corrected_quaternions
    ).all():
        raise ValueError("correction produced non-finite output")
    return corrected_points, corrected_quaternions, progress


def trajectory_metrics(points: np.ndarray) -> dict[str, object]:
    if not np.isfinite(points).all():
        raise ValueError("trajectory metrics received non-finite points")
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    return {
        "samples": len(points),
        "endpoint_distance_m": float(np.linalg.norm(points[-1] - points[0])),
        "xyz_span_m": np.ptp(points, axis=0).tolist(),
        "maximum_step_m": float(np.max(steps)),
        "cumulative_path_m": float(np.sum(steps)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--loop-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--minimum-confirmations", type=int, default=3)
    parser.add_argument("--minimum-accepts", type=int, default=1)
    parser.add_argument("--consistency-window", type=int, default=3)
    parser.add_argument("--max-translation-disagreement-m", type=float, default=0.03)
    parser.add_argument("--max-yaw-disagreement-deg", type=float, default=3.0)
    args = parser.parse_args()

    reject_path_collisions(
        [args.trajectory, args.loop_log], [args.output, args.report]
    )
    source_hashes = {
        "trajectory_sha256": sha256_file(args.trajectory),
        "loop_log_sha256": sha256_file(args.loop_log),
    }

    rows = list(csv.DictReader(args.trajectory.open(encoding="utf-8")))
    required = {"x", "y", "z", "qx", "qy", "qz", "qw"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("trajectory CSV is empty or lacks pose columns")
    corrections = parse_verified_corrections(args.loop_log)
    qualified = [
        item for item in corrections
        if item.confirmations >= args.minimum_confirmations
    ]
    if len(qualified) < args.minimum_accepts:
        raise ValueError("insufficient automatically verified loop accepts")
    evidence_window = qualified[-args.consistency_window :]
    consistency = correction_consistency(evidence_window)
    if consistency["max_pairwise_translation_m"] > args.max_translation_disagreement_m:
        raise ValueError("verified loop translations disagree")
    if consistency["max_pairwise_yaw_deg"] > args.max_yaw_disagreement_deg:
        raise ValueError("verified loop yaws disagree")

    selected = qualified[-1]
    points = np.array(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.array(
        [[float(row[key]) for key in ("qx", "qy", "qz", "qw")] for row in rows]
    )
    corrected_points, corrected_quaternions, progress = apply_smooth_correction(
        points, quaternions, selected
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row, point, quaternion in zip(rows, corrected_points, corrected_quaternions):
            output_row = dict(row)
            for key, value in zip(("x", "y", "z"), point):
                output_row[key] = f"{value:.12g}"
            for key, value in zip(("qx", "qy", "qz", "qw"), quaternion):
                output_row[key] = f"{value:.12g}"
            writer.writerow(output_row)

    report = {
        "schema_version": 1,
        "result": "PASS",
        "acceptance_scope": "postprocess_integrity_only",
        "accuracy_note": (
            "PASS means verified evidence was parsed and applied safely; "
            "trajectory accuracy requires a separate scored acceptance gate"
        ),
        "method": "verified_loop_se2_correction_distributed_by_arc_length",
        "does_not_use": ["expected_dimensions", "endpoint_position", "ground_truth"],
        "source": {
            "trajectory": str(args.trajectory.resolve()),
            "trajectory_sha256": source_hashes["trajectory_sha256"],
            "loop_log": str(args.loop_log.resolve()),
            "loop_log_sha256": source_hashes["loop_log_sha256"],
        },
        "verification": {
            "qualified_accepts": len(qualified),
            "minimum_confirmations": args.minimum_confirmations,
            **consistency,
        },
        "selected_correction": {
            "current_keyframe": selected.current,
            "matched_keyframe": selected.matched,
            "confirmations": selected.confirmations,
            "translation_m": selected.translation_m.tolist(),
            "yaw_deg": selected.yaw_deg,
        },
        "progress": {
            "first": float(progress[0]),
            "last": float(progress[-1]),
            "monotonic": bool(np.all(np.diff(progress) >= 0.0)),
        },
        "before": trajectory_metrics(points),
        "after": trajectory_metrics(corrected_points),
        "output": str(args.output.resolve()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
