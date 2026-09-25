#!/usr/bin/env python3
"""Keep a healthy visual frontend; rescue only its measured scale-dispersion failure."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DENSE = "mast3r/stereo_scale_dense10hz_report.json"
RESCUE_REPORTS = (
    "mast3r/stereo_scale_bidirectional_report.json",
    "mast3r/stereo_scale_long_hops_report.json",
    DENSE,
    "mast3r/stereo_scale_multisecond_report.json",
    "mast3r/imu_scale_report.json",
    "mast3r/graph_fusion_report.json",
    "fusion_report.json",
    "input_quality_report.json",
)


def load_report(path: Path) -> dict:
    def reject_nonfinite(value: str):
        raise ValueError(f"nonfinite JSON value {value}: {path}")

    with path.open(encoding="utf-8") as stream:
        report = json.load(stream, parse_constant=reject_nonfinite)
    if not isinstance(report, dict):
        raise ValueError(f"report must be an object: {path}")
    return report


def decide(baseline: Path) -> str:
    dense = load_report(baseline / DENSE)
    if dense.get("result") == "PASS":
        continuity = dense.get("trajectory_continuity")
        if continuity is not None and (
            continuity.get("result") != "PASS"
            or continuity.get("unverified_gap_count", 0) != 0
        ):
            raise ValueError("baseline has an unverified visual gap")
        trajectory = baseline / "trajectory_fused.csv"
        quality = baseline / "input_quality_report.json"
        if not trajectory.is_file() or not quality.is_file():
            raise ValueError("baseline passed dense stereo but final trajectory/quality is incomplete")
        quality_report = load_report(quality)
        if quality_report.get("result") != "PASS":
            raise ValueError("baseline passed dense stereo but final trajectory/quality is incomplete")
        if quality_report.get("external_ground_truth_used") is not False or quality_report.get("slam_supervision") is not False:
            raise ValueError(f"baseline lacks supervision provenance: {quality}")
        return "baseline"
    failures = dense.get("failures")
    if (
        dense.get("result") == "FAIL"
        and isinstance(failures, list)
        and len(failures) == 1
        and isinstance(failures[0], str)
        and failures[0].startswith("stereo scale dispersion too high:")
    ):
        return "rescue"
    raise ValueError(f"baseline failure is not eligible for metric rescue: {failures}")


def select(baseline: Path, rescue: Path) -> Path:
    if decide(baseline) == "baseline":
        return baseline / "trajectory_fused.csv"
    for name in RESCUE_REPORTS:
        path = rescue / name
        report = load_report(path)
        if report.get("result") != "PASS":
            raise ValueError(f"metric rescue did not pass {path}")
        if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
            raise ValueError(f"metric rescue lacks supervision provenance: {path}")
        if name == DENSE:
            continuity = report.get("trajectory_continuity") or {}
            if continuity.get("result") != "PASS" or continuity.get("unverified_gap_count", 0) != 0:
                raise ValueError(f"metric rescue has an unverified visual gap: {path}")
    trajectory = rescue / "trajectory_fused.csv"
    if not trajectory.is_file():
        raise ValueError(f"metric rescue trajectory missing: {trajectory}")
    return trajectory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--rescue-dir", type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.probe:
        print(decide(args.baseline_dir))
        return
    if args.rescue_dir is None or args.output is None or args.report is None:
        parser.error("selection requires --rescue-dir, --output, and --report")
    source = select(args.baseline_dir, args.rescue_dir)
    if args.output.exists() or args.report.exists():
        raise ValueError("selection output already exists; refusing to overwrite it")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, args.output)
    args.report.write_text(json.dumps({
        "schema": "umi_mast3r_metric_rescue_selection_v1",
        "result": "PASS",
        "selected": "baseline" if source.parent == args.baseline_dir else "rescue",
        "source": str(source.resolve()),
        "output": str(args.output.resolve()),
        "slam_supervision": False,
        "external_ground_truth_used": False,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
