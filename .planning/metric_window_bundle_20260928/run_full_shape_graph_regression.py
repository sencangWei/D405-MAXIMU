"""Matched-node grouped-shape research batch; freeze all estimates before GT.

Reuse frozen original commands. No weights, caps, td, calibration or selectors
are changed. This runner is not production promotion and never selects by GT.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_full_seam_graph_regression as seam_graph
import run_full_shape_controls as shape_controls

ROOT = seam_graph.ROOT
WRAPPER = ROOT / "scripts/fuse_mast3r_grouped_shape_windows.py"
VARIANTS = ("baseline", "endpoint_control", "grouped_shape")
STAGES = ("graph", "complementary", "quality", "smooth", "score")


def validate_shape_controls(folder: Path) -> dict[str, str]:
    paths = [folder / "summary.json", folder / "independent_summary.json"]
    joint, independent = [json.loads(path.read_text()) for path in paths]
    counts = shape_controls._validate_pair_summaries(joint, independent)
    hashes = {str(path.resolve()): seam_graph.digest(path) for path in paths}
    required = {str(path.resolve()) for path in shape_controls.source_paths()}
    if joint.get("source_sha256") != independent.get("source_sha256"):
        raise ValueError("shape source maps differ")
    if not required.issubset(joint.get("source_sha256", {})):
        raise ValueError("shape source closure incomplete")
    for summary, independent_flag in ((joint, False), (independent, True)):
        adapter = summary.get("full_shape_window_adapter", {})
        if (
            adapter.get("case_counts") != counts
            or adapter.get("diagnostic_only") is not True
            or adapter.get("independent_summary") is not independent_flag
            or any(adapter.get(key) is not False for key in (
                "external_ground_truth_used", "used_for_graph_or_selection",
                "available_for_graph", "calibrated_covariance",
                "statistical_independence_claimed",
            ))
        ):
            raise ValueError("shape diagnostic adapter contract mismatch")
        for raw, expected in summary["source_sha256"].items():
            seam_graph.add_hash(hashes, raw, expected)
        for case in summary["cases"]:
            if not case.get("input_sha256"):
                raise ValueError("shape case input closure empty")
            for raw, expected in case["input_sha256"].items():
                seam_graph.add_hash(hashes, raw, expected)
    seam_graph.verify_hashes(hashes)
    return hashes


def prepare_entries(joint: Path, independent: Path, shapes: Path) -> list[dict]:
    shape_hashes = validate_shape_controls(shapes)
    sources = [Path(__file__).resolve(), WRAPPER,
               ROOT / "scripts/stereo_window_shape_refiner.py",
               ROOT / "scripts/stereo_window_shape_graph.py",
               ROOT / "scripts/stereo_window_shape_system.py"]
    for source in sources:
        seam_graph.add_hash(shape_hashes, source)
    originals = seam_graph.load_entries(joint, independent)
    indexed = {(entry["case"], entry["variant"]): entry for entry in originals}
    entries = []
    for case in seam_graph.CASES:
        for variant in VARIANTS:
            original = indexed[(case, "baseline" if variant == "baseline" else "joint")]
            if tuple(stage for stage, _ in original["manifest"]["commands"]) != STAGES:
                raise ValueError("unknown frozen downstream stage order")
            entry = {**original, "variant": variant,
                     "source_sha256": dict(original["source_sha256"])}
            for raw, expected in shape_hashes.items():
                seam_graph.add_hash(entry["source_sha256"], raw, expected)
            seam_graph.verify_hashes(entry["input_sha256"])
            seam_graph.verify_hashes(entry["source_sha256"])
            entries.append(entry)
    if len(entries) != 30:
        raise ValueError("exact ten-by-three batch required")
    return entries


def commands_for(entry: dict, target: Path, joint: Path, shapes: Path):
    variant = entry["variant"]
    commands = seam_graph.variant_commands(
        entry["manifest"]["commands"], entry["validated_graph"],
        entry["old"], target, "baseline" if variant == "baseline" else "joint",
        None if variant == "baseline" else joint, entry["case"],
    )
    if variant != "baseline":
        command = next(command for stage, command in commands if stage == "graph")
        command[1] = str(WRAPPER)
        command += ["--shape-window-controls", str(shapes / "summary.json"),
                    "--shape-window-independent-controls", str(shapes / "independent_summary.json"),
                    "--shape-window-mode", variant]
    return commands


def run_phase(records, output: Path, *, scoring: bool) -> None:
    for target, record in records:
        if scoring and not record.get("estimation_completed"):
            continue
        for stage, command in record["commands"]:
            if (stage == "score") is not scoring:
                continue
            if record.get("failure_stage"):
                break
            seam_graph.verify_hashes(record["input_sha256"])
            seam_graph.verify_hashes(record["source_sha256"])
            started = time.monotonic()
            rc = seam_graph.run_command(command, ROOT, target / f"{stage}.log")
            record["stages"].append({"stage": stage, "returncode": rc,
                                     "runtime_s": time.monotonic() - started})
            seam_graph.verify_hashes(record["input_sha256"])
            seam_graph.verify_hashes(record["source_sha256"])
            if rc != 0 and not (stage in {"quality", "score"} and rc == 3):
                record["failure_stage"] = stage
                break
            if stage == "graph":
                seam_graph.verify_graph_output_times(command)
        if not scoring:
            record["estimation_completed"] = not bool(record.get("failure_stage"))
            if record["estimation_completed"]:
                trajectory = target / "trajectory_fused.csv"
                record["frozen_estimate_sha256"] = seam_graph.digest(trajectory)
                record["frozen_camera_sha256"] = seam_graph.digest(target / "trajectory_graph.csv")
        else:
            record["completed"] = not bool(record.get("failure_stage"))
        (output / "batch_status.json").write_text(json.dumps({
            "cases": [row for _, row in records],
            "gt_scoring_started_after_all_estimation_attempts": scoring,
        }, indent=2) + "\n")
        print(f"{record['variant']}/{record['case']} "
              f"{'score' if scoring else 'estimate'} "
              f"failure={record.get('failure_stage', 'none')}", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--joint-controls", type=Path, required=True)
    parser.add_argument("--independent-controls", type=Path, required=True)
    parser.add_argument("--shape-controls", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists():
        raise ValueError("refuse to overwrite grouped shape batch")
    entries = prepare_entries(args.joint_controls, args.independent_controls, args.shape_controls)
    output.mkdir(parents=True)
    records = []
    for entry in entries:
        target = output / entry["variant"] / entry["case"]
        target.mkdir(parents=True)
        record = {"case": entry["case"], "variant": entry["variant"],
                  "commands": commands_for(entry, target, args.joint_controls, args.shape_controls),
                  "input_sha256": entry["input_sha256"],
                  "source_sha256": entry["source_sha256"],
                  "external_reference_used_in_optimization": False,
                  "experiment_status": "research_diagnostic_not_promoted", "stages": []}
        (target / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        records.append((target, record))
    run_phase(records, output, scoring=False)
    # Freeze all graph + complementary + smoothing outputs before ANY GT score.
    for target, record in records:
        seam_graph.verify_hashes(record["input_sha256"])
        seam_graph.verify_hashes(record["source_sha256"])
        if record.get("estimation_completed"):
            if seam_graph.digest(target / "trajectory_fused.csv") != record["frozen_estimate_sha256"]:
                raise ValueError("final estimate changed before GT")
            if seam_graph.digest(target / "trajectory_graph.csv") != record["frozen_camera_sha256"]:
                raise ValueError("camera estimate changed before GT")
    run_phase(records, output, scoring=True)
    return int(any(record.get("failure_stage") for _, record in records))


if __name__ == "__main__":
    raise SystemExit(main())
