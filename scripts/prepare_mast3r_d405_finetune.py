#!/usr/bin/env python3
"""Build a Lighthouse-free D405 IR stereo fine-tuning dataset for MASt3R.

The generated supervision comes only from hardware-synchronized D405 IR pairs,
their factory intrinsics/baseline, and the onboard IMU rotation prior used for
sampling.  Lighthouse and robot trajectories are never read.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "config/mast3r_d405_ir_training_corpus_v1.json"
DEFAULT_EXPORT_ROOT = ROOT / "reports"
DEPTH_UNIT_M = 0.0001


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def compute_bidirectional_depth(
    left: np.ndarray,
    right: np.ndarray,
    focal_length_px: float,
    baseline_m: float,
    minimum_depth_m: float,
    maximum_depth_m: float,
    num_disparities: int = 128,
    consistency_tolerance_px: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return left/right metric depth with a left-right consistency gate."""
    if left.ndim != 2 or right.ndim != 2 or left.shape != right.shape:
        raise ValueError("stereo images must be matching grayscale arrays")
    num_disparities = max(16, int(np.ceil(num_disparities / 16.0)) * 16)
    common = dict(
        numDisparities=num_disparities,
        blockSize=5,
        P1=8 * 5 * 5,
        P2=32 * 5 * 5,
        disp12MaxDiff=1,
        uniquenessRatio=8,
        speckleWindowSize=80,
        speckleRange=2,
        preFilterCap=31,
        mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY,
    )
    disparity_left = cv2.StereoSGBM_create(minDisparity=0, **common).compute(
        left, right
    ).astype(np.float32) / 16.0
    disparity_right = cv2.StereoSGBM_create(
        minDisparity=-num_disparities, **common
    ).compute(right, left).astype(np.float32) / 16.0

    height, width = left.shape
    y, x = np.indices((height, width))

    right_x = np.rint(x - disparity_left).astype(np.int32)
    left_valid = (right_x >= 0) & (right_x < width) & (disparity_left > 0.5)
    sampled_right = np.full(left.shape, np.nan, dtype=np.float32)
    sampled_right[left_valid] = disparity_right[y[left_valid], right_x[left_valid]]
    left_valid &= np.abs(disparity_left + sampled_right) <= consistency_tolerance_px

    left_x = np.rint(x - disparity_right).astype(np.int32)
    right_valid = (left_x >= 0) & (left_x < width) & (disparity_right < -0.5)
    sampled_left = np.full(right.shape, np.nan, dtype=np.float32)
    sampled_left[right_valid] = disparity_left[y[right_valid], left_x[right_valid]]
    right_valid &= np.abs(disparity_right + sampled_left) <= consistency_tolerance_px

    left_depth = np.zeros(left.shape, dtype=np.float32)
    right_depth = np.zeros(right.shape, dtype=np.float32)
    left_depth[left_valid] = focal_length_px * baseline_m / disparity_left[left_valid]
    right_depth[right_valid] = -focal_length_px * baseline_m / disparity_right[right_valid]
    left_valid &= (left_depth >= minimum_depth_m) & (left_depth <= maximum_depth_m)
    right_valid &= (right_depth >= minimum_depth_m) & (right_depth <= maximum_depth_m)
    left_depth[~left_valid] = 0.0
    right_depth[~right_valid] = 0.0
    return left_depth, right_depth


def discover_exports(export_root: Path, extras: list[Path]) -> dict[Path, Path]:
    candidates = list(export_root.rglob("dataset_manifest.json"))
    candidates.extend(path / "dataset_manifest.json" for path in extras)
    by_session: dict[Path, Path] = {}
    for manifest_path in candidates:
        if not manifest_path.is_file():
            continue
        manifest = load_json(manifest_path)
        if manifest.get("slam_supervision") is not False:
            continue
        if manifest.get("stream") != "infrared_left":
            continue
        if not manifest.get("stereo_depth_source"):
            continue
        session = Path(manifest["source_session"]).resolve()
        by_session.setdefault(session, manifest_path.parent.resolve())
    return by_session


def motion_bin(angle_deg: float) -> str:
    if angle_deg >= 2.0:
        return "very_fast"
    if angle_deg >= 1.0:
        return "fast"
    if angle_deg >= 0.5:
        return "moderate"
    return "slow"


def depth_to_u16(depth_m: np.ndarray) -> np.ndarray:
    scaled = np.rint(np.asarray(depth_m) / DEPTH_UNIT_M)
    return np.clip(scaled, 0, np.iinfo(np.uint16).max).astype(np.uint16)


def select_sample_indices(
    priors: list[dict[str, str]],
    every: int,
    dense_motion_threshold_deg: float,
    dense_motion_radius_frames: int,
) -> list[int]:
    """Keep the regular grid and densify only around fast onboard rotations."""
    selected = set(range(0, len(priors), every))
    if dense_motion_threshold_deg <= 0.0:
        return sorted(selected)
    for index, prior in enumerate(priors):
        if float(prior["angle_deg"]) < dense_motion_threshold_deg:
            continue
        first = max(0, index - dense_motion_radius_frames)
        last = min(len(priors), index + dense_motion_radius_frames + 1)
        selected.update(range(first, last))
    return sorted(selected)


def materialize_depth(sample: dict, output: Path) -> tuple[dict | None, str | None]:
    left = cv2.imread(sample["left_image"], cv2.IMREAD_GRAYSCALE)
    right = cv2.imread(sample["right_image"], cv2.IMREAD_GRAYSCALE)
    if left is None or right is None:
        return None, "missing_stereo_image"
    left_depth, right_depth = compute_bidirectional_depth(
        left,
        right,
        sample["intrinsics"][0][0],
        sample["baseline_m"],
        sample["minimum_depth_m"],
        sample["maximum_depth_m"],
        consistency_tolerance_px=float(
            sample.get("consistency_tolerance_px", 1.0)
        ),
    )
    left_ratio = float(np.count_nonzero(left_depth) / left_depth.size)
    right_ratio = float(np.count_nonzero(right_depth) / right_depth.size)
    if min(left_ratio, right_ratio) < sample["minimum_valid_depth_ratio"]:
        return None, "insufficient_bidirectional_depth"

    sample_dir = output / "depth" / sample["session_id"]
    sample_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{sample['input_index']:010d}"
    left_path = sample_dir / f"{stem}_left.png"
    right_path = sample_dir / f"{stem}_right.png"
    if not cv2.imwrite(str(left_path), depth_to_u16(left_depth)):
        raise IOError(f"failed to write {left_path}")
    if not cv2.imwrite(str(right_path), depth_to_u16(right_depth)):
        raise IOError(f"failed to write {right_path}")
    result = dict(sample)
    result.update(
        depth_left=str(left_path.resolve()),
        depth_right=str(right_path.resolve()),
        valid_depth_ratio_left=left_ratio,
        valid_depth_ratio_right=right_ratio,
    )
    return result, None


def build_manifest(
    corpus_path: Path,
    export_root: Path,
    extra_datasets: list[Path],
    output: Path,
    val_fold: int,
    every: int,
    max_samples_per_session: int,
    minimum_depth_m: float,
    maximum_depth_m: float,
    minimum_valid_depth_ratio: float,
    materialize: bool,
    dense_motion_threshold_deg: float = 0.0,
    dense_motion_radius_frames: int = 0,
    consistency_tolerance_px: float = 1.0,
) -> dict:
    if every < 1:
        raise ValueError("--every must be at least 1")
    if consistency_tolerance_px <= 0.0:
        raise ValueError("stereo consistency tolerance must be positive")
    corpus = load_json(corpus_path)
    if corpus.get("truth_policy", {}).get("never_input_to_slam") is not True:
        raise ValueError("corpus does not forbid external truth as SLAM input")
    exports = discover_exports(export_root, extra_datasets)
    output.mkdir(parents=True, exist_ok=True)

    samples: list[dict] = []
    missing_sessions: list[str] = []
    rejected: list[dict] = []
    source_exports: list[dict] = []
    for dataset in corpus["datasets"]:
        session = Path(dataset["session"]).resolve()
        export = exports.get(session)
        if export is None:
            missing_sessions.append(dataset["id"])
            continue
        manifest_path = export / "dataset_manifest.json"
        manifest = load_json(manifest_path)
        stereo = manifest["stereo_depth_source"]
        if float(stereo["max_left_right_skew_ms"]) > 0.1:
            raise ValueError(f"stereo skew exceeds 0.1 ms: {manifest_path}")
        baseline_m = float(stereo["baseline_m"])
        if not 0.015 <= baseline_m <= 0.025:
            raise ValueError(f"unexpected D405 stereo baseline: {baseline_m}")
        frames = list(csv.DictReader((export / "frames.csv").open(newline="", encoding="utf-8")))
        priors = list(csv.DictReader((export / "imu_rotation_priors.csv").open(newline="", encoding="utf-8")))
        if len(frames) != len(priors):
            raise ValueError(f"frame/IMU prior count mismatch: {export}")
        intrinsics = manifest["camera_info"]
        camera_matrix = [
            [float(intrinsics["fx"]), 0.0, float(intrinsics["ppx"])],
            [0.0, float(intrinsics["fy"]), float(intrinsics["ppy"])],
            [0.0, 0.0, 1.0],
        ]
        selected = select_sample_indices(
            priors,
            every,
            dense_motion_threshold_deg,
            dense_motion_radius_frames,
        )
        if max_samples_per_session > 0:
            selected = selected[:max_samples_per_session]
        source_exports.append(
            {
                "session_id": dataset["id"],
                "dataset_manifest": str(manifest_path.resolve()),
                "dataset_manifest_sha256": sha256(manifest_path),
                "frames_csv_sha256": sha256(export / "frames.csv"),
                "imu_priors_sha256": sha256(export / "imu_rotation_priors.csv"),
            }
        )
        for index in selected:
            row = frames[index]
            prior = priors[index]
            sample = {
                "session_id": dataset["id"],
                "fold": int(dataset["fold"]),
                "split": "validation" if int(dataset["fold"]) == val_fold else "train",
                "input_index": int(row["input_index"]),
                "timestamp_s": float(row["t_sec"]),
                "left_image": str((export / row["image"]).resolve()),
                "right_image": str((export / stereo["right_directory"] / row["image"]).resolve()),
                "intrinsics": camera_matrix,
                "baseline_m": baseline_m,
                "imu_rotation_deg_per_frame": float(prior["angle_deg"]),
                "motion_bin": motion_bin(float(prior["angle_deg"])),
                "minimum_depth_m": minimum_depth_m,
                "maximum_depth_m": maximum_depth_m,
                "minimum_valid_depth_ratio": minimum_valid_depth_ratio,
                "consistency_tolerance_px": consistency_tolerance_px,
            }
            if materialize:
                sample, reason = materialize_depth(sample, output)
                if sample is None:
                    rejected.append(
                        {"session_id": dataset["id"], "input_index": index, "reason": reason}
                    )
                    continue
            samples.append(sample)

    counts = {
        split: sum(sample["split"] == split for sample in samples)
        for split in ("train", "validation")
    }
    if counts["train"] == 0 or counts["validation"] == 0:
        raise ValueError(f"empty train/validation split: {counts}")
    result = {
        "schema": "umi_mast3r_d405_ir_finetune_v1",
        "result": "READY" if materialize else "INDEX_ONLY",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "supervision": [
            "hardware_synchronized_d405_left_right_ir",
            "factory_intrinsics_and_18mm_stereo_baseline",
            "stereo_depth_left_right_consistency",
        ],
        "imu_usage": "onboard_400hz_rotation_prior_for_motion_stratification_only",
        "depth_unit_m": DEPTH_UNIT_M,
        "stereo_depth_filter": {
            "left_right_consistency_tolerance_px": consistency_tolerance_px,
            "minimum_depth_m": minimum_depth_m,
            "maximum_depth_m": maximum_depth_m,
        },
        "val_fold": val_fold,
        "sampling_every_frames": every,
        "dense_motion_sampling": {
            "threshold_deg_per_frame": dense_motion_threshold_deg,
            "radius_frames": dense_motion_radius_frames,
        },
        "counts": counts,
        "motion_bin_counts": {
            name: sum(sample["motion_bin"] == name for sample in samples)
            for name in ("slow", "moderate", "fast", "very_fast")
        },
        "missing_sessions": missing_sessions,
        "rejected_samples": rejected,
        "source_exports": source_exports,
        "samples": samples,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    result["manifest"] = str(manifest_path.resolve())
    result["manifest_sha256"] = sha256(manifest_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--export-root", type=Path, default=DEFAULT_EXPORT_ROOT)
    parser.add_argument("--extra-dataset", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--val-fold", type=int, choices=range(5), default=4)
    parser.add_argument("--every", type=int, default=6)
    parser.add_argument("--max-samples-per-session", type=int, default=0)
    parser.add_argument("--minimum-depth-m", type=float, default=0.15)
    parser.add_argument("--maximum-depth-m", type=float, default=0.65)
    parser.add_argument("--minimum-valid-depth-ratio", type=float, default=0.01)
    parser.add_argument(
        "--consistency-tolerance-px",
        type=float,
        default=1.0,
        help="maximum left-right disparity disagreement used for depth labels",
    )
    parser.add_argument("--materialize-depth", action="store_true")
    parser.add_argument("--dense-motion-threshold-deg", type=float, default=0.0)
    parser.add_argument("--dense-motion-radius-frames", type=int, default=0)
    args = parser.parse_args()
    if args.dense_motion_radius_frames < 0:
        raise ValueError("dense motion radius must be non-negative")
    if args.consistency_tolerance_px <= 0.0:
        raise ValueError("stereo consistency tolerance must be positive")
    report = build_manifest(
        args.corpus.resolve(),
        args.export_root.resolve(),
        [path.resolve() for path in args.extra_dataset],
        args.output.resolve(),
        args.val_fold,
        args.every,
        args.max_samples_per_session,
        args.minimum_depth_m,
        args.maximum_depth_m,
        args.minimum_valid_depth_ratio,
        args.materialize_depth,
        args.dense_motion_threshold_deg,
        args.dense_motion_radius_frames,
        args.consistency_tolerance_px,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
