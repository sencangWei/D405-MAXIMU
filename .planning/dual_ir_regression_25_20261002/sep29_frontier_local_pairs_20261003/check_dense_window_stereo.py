"""Independent stereo check of copied native-GN window states, not ATE."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from align_mast3r_scale_with_stereo import (  # noqa: E402
    estimate_pair_scale,
    load_stereo_calibration_from_prepared_dataset,
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def state(report, frame_id, variant):
    row = np.asarray(report["pose_states"][str(frame_id)][variant], dtype=float)
    if row.shape != (8,) or not np.isfinite(row).all() or row[7] <= 0:
        raise ValueError(f"Invalid Sim3 state {frame_id}/{variant}")
    if abs(np.linalg.norm(row[3:7]) - 1) > 1e-3:
        raise ValueError(f"Invalid quaternion {frame_id}/{variant}")
    return row


def check_pair(images, p0, p1, calibration):
    cv2.setRNGSeed(0)
    return estimate_pair_scale(
        *images, p0[:3], p1[:3], Rotation.from_quat(p0[3:7]),
        Rotation.from_quat(p1[3:7]), calibration, 128, 0.07, 0.6,
        trajectory_frame="infrared_left", pnp_rotation_mode="free",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--extra-pair", nargs=2, type=int, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    report = json.loads(args.probe.read_text())
    if report.get("status") != "DENSE_WINDOW_SOLVED":
        raise ValueError("Only solved copied-native window states can be checked")
    if report.get("external_ground_truth_used") is not False:
        raise ValueError("Ground-truth-free diagnostic required")
    if Path(report["inputs"]["dataset"]).resolve() != args.dataset.resolve():
        raise ValueError("Dataset does not match native probe")
    cv2.setNumThreads(2)
    with (args.dataset / "frames.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    calibration = load_stereo_calibration_from_prepared_dataset(args.dataset)
    ids = report["window_frame_ids"]
    pairs = list(zip(ids[:-1], ids[1:])) + [tuple(pair) for pair in args.extra_pair]
    results = []
    for i, j in pairs:
        images = []
        hashes = {}
        for frame_id in (i, j):
            path = args.dataset / rows[frame_id]["image"]
            for eye_path in (path, args.dataset / "stereo_right" / path.name):
                image = cv2.imread(str(eye_path), cv2.IMREAD_GRAYSCALE)
                if image is None:
                    raise ValueError(f"Missing stereo image {eye_path}")
                images.append(image)
                hashes[str(eye_path.resolve())] = sha256(eye_path)
        pair_result = {"frames": [i, j], "image_sha256": hashes}
        for variant in ("baseline_after", "variant_after"):
            pair_result[variant] = check_pair(
                images, state(report, i, variant), state(report, j, variant), calibration
            )
        results.append(pair_result)
    summary = {}
    for variant in ("baseline_after", "variant_after"):
        values = [row[variant] for row in results]
        summary[variant] = {
            "pair_count": len(values),
            "accepted_count": sum(bool(row.get("accepted")) for row in values),
            "rejection_reasons": {
                reason: sum(row.get("reason") == reason for row in values)
                for reason in sorted({row["reason"] for row in values if "reason" in row})
            },
        }
    output = {
        "schema": "umi_dense_native_window_stereo_diagnostic_v1",
        "diagnostic_only": True, "precision_pass": False,
        "production_promoted": False, "external_ground_truth_used": False,
        "probe": str(args.probe.resolve()), "probe_sha256": sha256(args.probe),
        "estimator_sha256": sha256(ROOT / "scripts/align_mast3r_scale_with_stereo.py"),
        "factory_baseline_m": calibration["baseline_m"],
        "settings": {"opencv_seed": 0, "threads": 2, "disparities": 128,
                     "depth_range_m": [0.07, 0.6], "rotation_mode": "free"},
        "summary": summary, "pairs": results,
        "interpretation": "Unchanged independent stereo estimator; no ATE, no accepted metric-scale source, no complete trajectory promotion.",
    }
    with args.output.open("x") as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
