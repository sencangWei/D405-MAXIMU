#!/usr/bin/env python3
"""Score an already generated body/IMU SLAM trajectory; never run or tune SLAM."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from apply_lighthouse_aprilgrid_calibration import ROOT, write_ground_truth


DEFAULT_REFERENCE = ROOT / "reports/steamvr_reference_frozen_20260927_v1/reference_manifest.json"
DEFAULT_CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml")


def score(capture_dir: Path, estimate: Path, output: Path,
          reference_path: Path, body_config: Path, query_domain: str) -> int:
    """All inputs are read-only. Output must not overwrite an input artifact."""
    capture_dir, estimate, output = capture_dir.resolve(), estimate.resolve(), output.resolve()
    if output.exists():
        raise ValueError("use a new output directory; existing reports are preserved")
    capture = json.loads((capture_dir / "capture_manifest.json").read_text())
    reference = json.loads(reference_path.read_text())
    calibration = ROOT / reference["artifacts"]["calibration"]["path"]
    frames = Path(capture["d405_session"]) / "d405_frames.csv"
    estimate_hash = hashlib.sha256(estimate.read_bytes()).hexdigest()
    provenance = write_ground_truth(
        estimate, capture_dir / "tracker.csv", calibration, frames, body_config,
        output / "steamvr_body_reference.csv", "body", 0.03,
        reference_manifest_path=reference_path,
        capture_manifest_path=capture_dir / "capture_manifest.json",
        query_time_domain=query_domain,
    )
    (output / "reference_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    command = [sys.executable, str(ROOT / "scripts/evaluate_slam_ground_truth.py"),
        "--estimate", str(estimate), "--ground-truth", str(output / "steamvr_body_reference.csv"),
        "--output", str(output / "precision.json"), "--plot", str(output / "precision.png"),
        "--report-md", str(output / "precision.md"), "--max-ate-rmse-mm", "10",
        "--max-ate-p95-mm", "10", "--max-ate-max-mm", "10",
        "--min-within-10mm-ratio", "0.95", "--max-rotation-rmse-deg", "2",
        # Exported reference is at the requested ~30Hz stamps, not raw120Hz.
        # Raw Tracker bracketing remains capped at30ms in write_ground_truth.
        "--min-timestamp-overlap-ratio", "0.98", "--max-interpolation-gap-s", "0.05"]
    process = subprocess.run(command, text=True, capture_output=True)
    (output / "evaluation.log").write_text(process.stdout + process.stderr)
    unchanged = hashlib.sha256(estimate.read_bytes()).hexdigest() == estimate_hash
    manifest = {
        "schema": "official_steamvr_slam_score_v1", "scope": "post_slam_scoring_only",
        "estimate": str(estimate), "estimate_frame": "body_imu_origin",
        "estimate_sha256": estimate_hash, "estimate_unchanged": unchanged,
        "query_time_domain": query_domain, "reference_backend": "steamvr_official",
        "reference_pose_frame": "steamvr_standing_tracker", "slam_supervision": False,
        "reference_manifest": str(reference_path.resolve()),
        "reference_manifest_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
        "evaluation_command": command, "evaluation_return_code": process.returncode,
        "result": "SCORING_COMPLETED" if process.returncode in (0, 3) and unchanged else "SCORING_ERROR",
    }
    (output / "workflow_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if not unchanged:
        raise ValueError("estimate changed during scoring; result is not reproducible")
    if process.returncode not in (0, 3):
        raise RuntimeError(f"evaluation failed; inspect {output / 'evaluation.log'}")
    print(f"报告: {output / 'precision.md'}")
    print(f"轨迹图: {output / 'precision.png'}")
    return process.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--estimate", type=Path, required=True,
                        help="Body/IMU-origin trajectory, not a raw camera-origin trajectory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--body-camera-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--query-time-domain", choices=("camera", "imu"), default="camera",
                        help="VINS/complementary-fusion published stamps are camera-domain")
    args = parser.parse_args()
    return score(args.capture, args.estimate, args.output, args.reference_manifest,
                 args.body_camera_config, args.query_time_domain)


if __name__ == "__main__":
    raise SystemExit(main())
