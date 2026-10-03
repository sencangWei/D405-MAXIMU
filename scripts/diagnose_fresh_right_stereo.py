#!/usr/bin/env python3
"""Diagnostic-only RIGHT-primary D405 stereo scale check for a complete fresh run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import align_mast3r_scale_with_stereo as stereo  # noqa: E402
from evaluate_metric_joint_frontend import validate_frontend  # noqa: E402
from ego_vio.vio.right_stereo_motion import (  # noqa: E402
    MIRROR,
    _mirror_rotation,
    _restore_result,
    _validate_camera_dimensions,
    _virtual_calibration,
)


SCHEMA = "umi_fresh_right_primary_stereo_diagnostic_v1"
FRAME_STEP = 5
MAX_HOP = 5
MIN_DEPTH_M = 0.07
MAX_DEPTH_M = 0.6
NUM_DISPARITIES = 128
MIN_OBSERVATIONS = 4
MAX_TRAJECTORY_DELTA_S = 0.002
MAX_STEREO_SKEW_MS = 0.1


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(paths: dict[str, Path]) -> dict[str, dict[str, str]]:
    return {
        name: {"path": str(path.resolve()), "sha256": file_hash(path.resolve())}
        for name, path in sorted(paths.items())
    }


def assert_unchanged(before: dict[str, dict[str, str]], after: dict[str, dict[str, str]] | None = None) -> None:
    after = after or snapshot({name: Path(item["path"]) for name, item in before.items()})
    changed = [
        name
        for name, item in before.items()
        if after[name]["sha256"] != item["sha256"]
    ]
    if changed:
        raise ValueError(f"consumed source changed during RIGHT diagnostic: {changed}")


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.resolve())
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def write_json_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def refuse_output(output: Path) -> None:
    if os.path.lexists(output):
        raise FileExistsError(f"refusing existing or symlink output: {output}")


def load_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(newline="", encoding="utf-8")))


def row_by_int(rows: list[dict[str, str]], key: str) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        number = int(row[key])
        if number in result:
            raise ValueError(f"duplicate {key}: {number}")
        result[number] = row
    return result


def native_context(source_run: Path) -> tuple[dict[str, Any], Path, Path, Path]:
    manifest = read_json(source_run / "dataset" / "dataset_manifest.json")
    session = Path(manifest["source_session"]).resolve(strict=True)
    frontend = validate_frontend(source_run, "right", expected_session=session)
    context = read_json(source_run / "context.json")
    dataset = Path(context["dataset"]).resolve(strict=True)
    paired = Path(context["paired_left_dataset"]).resolve(strict=True)
    if dataset != (source_run / "dataset").resolve(strict=True):
        raise ValueError("RIGHT context dataset is not the source run dataset")
    if context.get("eye") != "right" or context.get("external_ground_truth_used") is not False:
        raise ValueError("source context is not onboard RIGHT-only")
    return frontend, session, dataset, paired


def match_right_timeline(
    trajectory_times: np.ndarray, d405_frames: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = load_csv(d405_frames)
    required = {
        "infrared_left_device_ms",
        "infrared_right_device_ms",
        "infrared_left_frame_number",
        "infrared_right_frame_number",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError("d405_frames.csv lacks synchronized IR columns")
    right_times = np.asarray([float(row["infrared_right_device_ms"]) / 1000.0 for row in rows])
    left_numbers = [int(row["infrared_left_frame_number"]) for row in rows]
    right_numbers = [int(row["infrared_right_frame_number"]) for row in rows]
    if not np.all(np.isfinite(right_times)) or not np.all(np.isfinite(trajectory_times)):
        raise ValueError("RIGHT timeline contains non-finite timestamps")
    if len(set(left_numbers)) != len(left_numbers) or len(set(right_numbers)) != len(right_numbers):
        raise ValueError("RIGHT timeline contains duplicate IR frame numbers")
    if np.any(np.diff(right_times) <= 0):
        raise ValueError("right IR timestamps are not strictly increasing")
    matched = []
    deltas = []
    skews = []
    for index, stamp in enumerate(trajectory_times):
        pos = int(np.searchsorted(right_times, stamp))
        candidates = [candidate for candidate in (pos - 1, pos) if 0 <= candidate < len(rows)]
        if not candidates:
            raise ValueError(f"RIGHT trajectory timestamp has no source frame at {index}: {stamp}")
        chosen = min(candidates, key=lambda candidate: abs(right_times[candidate] - stamp))
        row = rows[chosen]
        delta = abs(right_times[chosen] - float(stamp))
        skew = abs(float(row["infrared_left_device_ms"]) - float(row["infrared_right_device_ms"]))
        if delta > MAX_TRAJECTORY_DELTA_S:
            raise ValueError(f"RIGHT trajectory timestamp mismatch at {index}: {delta*1000:.3f}ms")
        if skew > MAX_STEREO_SKEW_MS:
            raise ValueError(f"LEFT/RIGHT stereo skew exceeds contract at {index}: {skew:.6f}ms")
        matched.append(
            {
                "input_index": index,
                "right_t_sec": float(right_times[chosen]),
                "left_frame_number": int(row["infrared_left_frame_number"]),
                "right_frame_number": int(row["infrared_right_frame_number"]),
                "trajectory_delta_ms": float(delta * 1000.0),
                "stereo_skew_ms": float(skew),
            }
        )
        deltas.append(delta * 1000.0)
        skews.append(skew)
    return matched, {
        "time_binding": "trajectory t_sec -> d405_frames.infrared_right_device_ms",
        "max_trajectory_delta_ms": float(max(deltas, default=0.0)),
        "max_stereo_skew_ms": float(max(skews, default=0.0)),
    }


def uniform_pairs(count: int, *, max_pairs: int) -> list[tuple[int, int, int]]:
    sample_indices = np.arange(0, count, FRAME_STEP, dtype=int)
    candidates = stereo.sample_pairs(sample_indices, MAX_HOP)
    if not candidates:
        return []
    if len(candidates) <= max_pairs:
        return candidates
    chosen = np.linspace(0, len(candidates) - 1, num=max_pairs, dtype=int)
    return [candidates[int(index)] for index in dict.fromkeys(chosen.tolist())]


def image_paths(
    dataset: Path, paired: Path, matched: list[dict[str, Any]]
) -> tuple[dict[int, dict[str, Path]], dict[str, Path]]:
    native_rows = load_csv(dataset / "frames.csv")
    paired_rows = load_csv(paired / "frames.csv")
    native_by_index = row_by_int(native_rows, "input_index")
    paired_by_left = row_by_int(paired_rows, "source_frame_number")
    paired_manifest = read_json(paired / "dataset_manifest.json")
    right_dir = paired_manifest["stereo_depth_source"]["right_directory"]
    paths: dict[int, dict[str, Path]] = {}
    consumed: dict[str, Path] = {
        "native_manifest": dataset / "dataset_manifest.json",
        "native_frames": dataset / "frames.csv",
        "native_calibration": dataset / "calibration.yaml",
        "paired_manifest": paired / "dataset_manifest.json",
        "paired_frames": paired / "frames.csv",
        "paired_calibration": paired / "calibration.yaml",
    }
    for item in matched:
        index = int(item["input_index"])
        native = native_by_index[index]
        if int(native["source_frame_number"]) != int(item["right_frame_number"]):
            raise ValueError("RIGHT native dataset is not bound to right frame numbers")
        left_row = paired_by_left[int(item["left_frame_number"])]
        if native["image"] != left_row["image"]:
            raise ValueError("native RIGHT and paired LEFT image names diverge")
        left = paired / left_row["image"]
        paired_right = paired / right_dir / left_row["image"]
        native_right = dataset / native["image"]
        if file_hash(native_right) != file_hash(paired_right):
            raise ValueError("native RIGHT image differs from paired stereo_right image")
        paths[index] = {"left": left, "right": native_right, "paired_right": paired_right}
        consumed[f"image_{index:04d}_left"] = left
        consumed[f"image_{index:04d}_right"] = native_right
        consumed[f"image_{index:04d}_paired_right"] = paired_right
    return paths, consumed


def read_gray(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"failed to read image: {path}")
    return image


def code_paths() -> dict[str, Path]:
    return {
        "diagnostic_script": Path(__file__),
        "right_helper": ROOT / "ego_vio/vio/right_stereo_motion.py",
        "stereo_module": Path(stereo.__file__).resolve(),
        "frontend_validation_module": ROOT / "scripts/evaluate_metric_joint_frontend.py",
        "context_validation_module": ROOT / "scripts/experimental_mast3r_metric_joint_adapter.py",
    }


def estimate_right_pair(
    first: int,
    second: int,
    images: dict[int, dict[str, Path]],
    positions: np.ndarray,
    rotations: Rotation,
    calibration: dict[str, Any],
) -> dict[str, Any]:
    right_i, left_i = read_gray(images[first]["right"]), read_gray(images[first]["left"])
    right_j, left_j = read_gray(images[second]["right"]), read_gray(images[second]["left"])
    shapes = {image.shape for image in (right_i, left_i, right_j, left_j)}
    if len(shapes) != 1:
        raise ValueError(f"stereo pair images must share one resolution, got {sorted(shapes)}")
    height, width = right_i.shape
    _validate_camera_dimensions(calibration, width, height)
    virtual = _virtual_calibration(calibration, width)
    result = stereo.estimate_pair_scale(
        np.fliplr(right_i),
        np.fliplr(left_i),
        np.fliplr(right_j),
        np.fliplr(left_j),
        MIRROR @ positions[first],
        MIRROR @ positions[second],
        _mirror_rotation(rotations[first]),
        _mirror_rotation(rotations[second]),
        virtual,
        NUM_DISPARITIES,
        MIN_DEPTH_M,
        MAX_DEPTH_M,
        trajectory_frame="infrared_left",
        correspondence_estimator="classical",
    )
    restored = _restore_result(result)
    restored.update(first_index=first, second_index=second, image_shape=[int(height), int(width)])
    return restored


def run(source_run: Path, output: Path, *, max_pairs: int) -> dict[str, Any]:
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    refuse_output(output)
    source_run = source_run.resolve(strict=True)
    frontend, session, dataset, paired = native_context(source_run)
    times, positions, quats, _rows = stereo.load_trajectory(source_run / "trajectory_frames.csv")
    matched, timing = match_right_timeline(times, session / "d405_frames.csv")
    pairs = uniform_pairs(len(times), max_pairs=max_pairs)
    needed = sorted({index for pair in pairs for index in pair[:2]})
    selected = [matched[index] for index in needed]
    paths, consumed = image_paths(dataset, paired, selected)
    calibration = stereo.load_stereo_calibration_from_prepared_dataset(paired)
    before = snapshot(
        {
            **consumed,
            **code_paths(),
            "run_manifest": source_run / "run_manifest.json",
            "context": source_run / "context.json",
            "trajectory": source_run / "trajectory_frames.csv",
            "source_d405_frames": session / "d405_frames.csv",
            "config": Path(frontend["config"]),
            "checkpoint": Path(frontend["checkpoint"]),
        }
    )
    rotations = Rotation.from_quat(quats)
    observations = []
    for first, second, hop in pairs:
        observation = estimate_right_pair(first, second, paths, positions, rotations, calibration)
        observation.update(
            hop=int(hop),
            first_t_sec=float(times[first]),
            second_t_sec=float(times[second]),
            first_right_frame_number=matched[first]["right_frame_number"],
            second_right_frame_number=matched[second]["right_frame_number"],
        )
        observations.append(observation)
    failures = []
    try:
        scale, quality = stereo.robust_scale(observations, MIN_OBSERVATIONS)
    except ValueError as error:
        scale, quality = None, {"error": str(error)}
        failures.append("right_stereo_scale_unobservable")
    continuity = stereo.trajectory_step_continuity(positions, scale, times) if scale else None
    if continuity and continuity.get("result") == "FAIL":
        failures.append(continuity.get("reason") or "trajectory_continuity_failed")
    after = snapshot({name: Path(item["path"]) for name, item in before.items()})
    assert_unchanged(before, after)
    report = {
        "schema": SCHEMA,
        "result": "FAIL" if failures else "PASS",
        "failures": failures,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "factors_emitted": 0,
        "backend_run": False,
        "scoring_run": False,
        "model_loaded": False,
        "accuracy_label": "geometry_diagnostic_only_not_slam_accuracy",
        "capped_diagnostic": True,
        "sampling": {"frame_step": FRAME_STEP, "max_hop": MAX_HOP, "max_pairs": max_pairs, "scheduled_pairs": len(pairs)},
        "fixed_parameters": {"min_depth_m": MIN_DEPTH_M, "max_depth_m": MAX_DEPTH_M, "num_disparities": NUM_DISPARITIES, "min_observations": MIN_OBSERVATIONS},
        "source_run": str(source_run),
        "source_session": str(session),
        "frontend": json_safe(frontend),
        "timing": timing,
        "scale_m_per_mast3r_unit": scale,
        "quality": quality,
        "trajectory_continuity": continuity,
        "observation_frame": "infrared_right_camera_i",
        "observations": observations,
        "consumed_source_sha256_before": before,
        "consumed_source_sha256_after": after,
    }
    write_json_new(output / "fresh_right_primary_stereo_diagnostic.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pairs", type=int, default=32)
    args = parser.parse_args()
    if args.max_pairs <= 0:
        raise ValueError("--max-pairs must be positive")
    report = run(args.source_run, args.output, max_pairs=args.max_pairs)
    print(json.dumps({"result": report["result"], "observations": len(report["observations"]), "scale": report["scale_m_per_mast3r_unit"]}, allow_nan=False))
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
