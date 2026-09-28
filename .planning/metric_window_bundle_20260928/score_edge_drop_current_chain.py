#!/usr/bin/env python3
"""Controlled downstream replay of a UMI-only frontend intervention.

Use the frozen current graph/complement/quality/smoothing commands, changing
only the frontend-derived metric trajectory, IMU scale report, keyframe cache,
and output paths. The frozen seam measurements are held fixed deliberately.
This is a causal probe, not a promoted production recipe.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
STAGES = ("graph", "complementary", "quality", "smooth", "score")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def replace_flag(command, flag, value):
    result = list(command)
    positions = [i for i, item in enumerate(result) if item == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(result):
        raise ValueError(f"expected exactly one {flag} argument")
    result[positions[0] + 1] = str(value)
    return result


def commands_for(manifest, old, target, candidate, keyframes):
    commands = []
    for stage, original in manifest["commands"]:
        if stage not in STAGES:
            raise ValueError(f"unexpected stage {stage}")
        command = [str(target) + item[len(str(old)):] if item.startswith(str(old) + "/")
                   else item for item in original]
        if stage == "graph":
            for flag, path in (
                ("--trajectory", candidate / "trajectory_imu_metric.csv"),
                ("--imu-scale-report", candidate / "imu_scale_report.json"),
                ("--keyframe-dir", keyframes),
            ):
                command = replace_flag(command, flag, path)
        commands.append((stage, command))
    if tuple(stage for stage, _ in commands) != STAGES:
        raise ValueError("frozen command stage order changed")
    return commands


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--candidate-mast3r", type=Path, required=True)
    parser.add_argument("--candidate-keyframe-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frozen = args.frozen.resolve()
    candidate = args.candidate_mast3r.resolve()
    keyframes = args.candidate_keyframe_dir.resolve()
    target = args.output.resolve()
    if target.exists():
        parser.error("refusing to overwrite candidate output")
    source = frozen / "manifest.json"
    manifest = json.loads(source.read_text())
    commands = commands_for(manifest, frozen, target, candidate, keyframes)
    required = [candidate / "trajectory_imu_metric.csv",
                candidate / "imu_scale_report.json",
                keyframes]
    if not all(path.exists() for path in required):
        parser.error("candidate metric-stage inputs are incomplete")
    target.mkdir(parents=True)
    provenance = {str(path): digest(path) for path in required[:2]}
    provenance[str(source)] = digest(source)
    record = dict(schema="edge_drop_current_chain_causal_probe_v1",
                  diagnostic_only=True, external_gt_used_for_estimation=False,
                  frozen_control_inputs="stereo_seam_window_measurements_unchanged",
                  frontend_inputs_sha256=provenance, commands=commands, stages=[])
    (target / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    for stage, command in commands:
        if stage == "score":
            record["estimate_sha256_before_gt"] = digest(target / "trajectory_fused.csv")
            record["graph_sha256_before_gt"] = digest(target / "trajectory_graph.csv")
        with (target / f"{stage}.log").open("w") as log:
            process = subprocess.run(command, cwd=ROOT, stdout=log,
                                     stderr=subprocess.STDOUT, check=False)
        record["stages"].append(dict(stage=stage, returncode=process.returncode))
        (target / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"{stage}: rc={process.returncode}", flush=True)
        if process.returncode != 0 and not (stage in ("quality", "score")
                                            and process.returncode == 3):
            raise RuntimeError(f"candidate stage failed: {stage}")
    if digest(target / "trajectory_fused.csv") != record["estimate_sha256_before_gt"]:
        raise ValueError("estimate changed during external scoring")


if __name__ == "__main__":
    main()
