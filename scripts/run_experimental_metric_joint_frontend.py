#!/usr/bin/env python3
"""Fresh, source-bound MASt3R metric-joint frontend replay (no GT/scoring).

Experimental only. Preserves input images/times and effective configuration;
the sole algorithm switch is tracking.metric_relative_joint=True. Never turns
a missing metric solve, lost tail, or cached output into candidate acceptance.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import yaml

from experimental_mast3r_metric_joint_adapter import ROOT, TOOL, Runtime, context_for_source, sha
from prepare_mast3r_slam_dataset import load_authoritative_frames


def freeze_config(config_path):
    sys.path.insert(0, str(TOOL))
    from mast3r_slam.config import load_config
    cwd = Path.cwd()
    try:
        os.chdir(TOOL)
        config = load_config(str(config_path.resolve()), is_parent=True)
    finally:
        os.chdir(cwd)
    if config.get("single_thread") is not True or config.get("dataset", {}).get("subsample") != 1:
        raise ValueError("this candidate requires the frozen single-thread full-frame configuration")
    tracking = config["tracking"]
    if tracking.get("metric_relative_joint") or float(tracking.get("vins_backend_position_sigma_m", 0)) > 0:
        raise ValueError("source config must be native baseline without existing joint/VINS metric objective")
    if tracking.get("stereo_fix_pose_scale") or config["local_opt"]["pin"] != 1:
        raise ValueError("source config must preserve pin1/no post-solve scale forcing")
    config.pop("inherit", None)
    tracking["metric_relative_joint"] = True
    return config


def complete_source(dataset, paired, eye):
    nm = json.loads((dataset / "dataset_manifest.json").read_text())
    pm = json.loads((paired / "dataset_manifest.json").read_text())
    if nm.get("stream") != "infrared_" + eye or pm.get("stream") != "infrared_left":
        raise ValueError("source eye mismatch")
    if nm.get("slam_supervision") is not False or pm.get("slam_supervision") is not False:
        raise ValueError("source must be onboard-only")
    if nm.get("source_session") != pm.get("source_session"):
        raise ValueError("native/paired sessions differ")
    if (dataset / "frames.csv").read_bytes() != (paired / "frames.csv").read_bytes():
        raise ValueError("native/paired timelines differ")
    if nm.get("every") != 1 or nm.get("start_index") != 0:
        raise ValueError("source must be full-frame, start_index0")
    source_csv = Path(pm["source_frames_csv"])
    if sha(source_csv) != pm["source_frames_csv_sha256"]:
        raise ValueError("raw source clock changed")
    authoritative = load_authoritative_frames(source_csv, "infrared_" + eye)
    with (dataset / "frames.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(authoritative) or any(
        int(row["input_index"]) != i or abs(float(row["t_sec"]) - raw["t_sec"]) > 1e-6
        for i, (row, raw) in enumerate(zip(rows, authoritative))
    ):
        raise ValueError("dataset is a prefix/subset or differs from the authoritative full clock")
    return nm, len(rows)


def validate_solves(log, context):
    records = [json.loads(line) for line in log.read_text().splitlines()]
    if not records:
        raise ValueError("candidate contributed no graph solves")
    for record in records:
        if (record.get("mode") != "JOINT_METRIC_NATIVE" or record.get("external_ground_truth_used") is not False
                or record.get("factor_count", 0) <= 0 or record.get("accepted_pair_count", 0) <= 0
                or record.get("code_sha256") != context["code_sha256"]):
            raise ValueError("candidate solve log is missing actual bound metric contributions")
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--paired-left-dataset", type=Path, required=True)
    parser.add_argument("--eye", choices=("left", "right"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=TOOL / "checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    dataset = args.dataset.resolve(strict=True)
    paired = args.paired_left_dataset.resolve(strict=True)
    source, count = complete_source(dataset, paired, args.eye)
    frozen = freeze_config(args.config.resolve(strict=True))
    checkpoint = args.checkpoint.resolve(strict=True)
    context = context_for_source(dataset, paired, args.eye)
    output.mkdir(parents=True)
    context_path, solve_log = output / "context.json", output / "solves.jsonl"
    context_path.write_text(json.dumps(context, indent=2) + "\n")
    config_path = output / "effective_config.yaml"
    config_path.write_text(yaml.safe_dump(frozen))
    # Preflight source/code bindings before spawning the learned frontend.
    runtime = Runtime(context_path, output / "preflight.jsonl")
    runtime.log.close()
    (output / "dataset").symlink_to(dataset, target_is_directory=True)
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("MAST3R_"):
            env.pop(name)
    env.update({
        "CUDA_HOME": str(TOOL / ".cuda"), "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
        "MAST3R_METRIC_RELATIVE_JOINT_ADAPTER": str(ROOT / "scripts/experimental_mast3r_metric_joint_adapter.py"),
        "MAST3R_METRIC_RELATIVE_JOINT_CONTEXT": str(context_path), "MAST3R_METRIC_RELATIVE_JOINT_LOG": str(solve_log),
    })
    python = str(TOOL / ".venv/bin/python")
    command = [python, "main.py", "--dataset", str(output / "dataset"), "--config", str(config_path),
               "--save-as", str(output / "mast3r_logs"), "--no-viz", "--no-reconstruction",
               "--calib", str(dataset / "calibration.yaml"), "--checkpoint", str(checkpoint)]
    manifest = {
        "schema": "umi_mast3r_run_v1", "slam_supervision": False, "status": "RUNNING",
        "experimental": True, "precision_pass": False, "production_promoted": False,
        "config": str(config_path), "config_sha256": sha(config_path),
        "checkpoint": str(checkpoint), "checkpoint_sha256": sha(checkpoint),
        "source_config": str(args.config.resolve()), "source_config_sha256": sha(args.config),
        "source_session": source["source_session"], "eye": args.eye, "input_frame_count": count,
        "code_sha256": context["code_sha256"], "runner_sha256": sha(__file__), "command": command,
    }
    manifest_path = output / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    started = time.monotonic()
    try:
        print(f"fresh {args.eye} full frontend: {count} frames -> {output}", flush=True)
        with (output / "mast3r.log").open("x") as log:
            subprocess.run(command, cwd=TOOL, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        runtime.verify_bindings()
        manifest["joint_solve_count"] = validate_solves(solve_log, context)
        subprocess.run([
            python, str(ROOT / "scripts/convert_mast3r_slam_trajectory.py"),
            "--trajectory", str(output / "mast3r_logs/dataset_full.txt"),
            "--frames", str(dataset / "frames.csv"), "--output", str(output / "trajectory_frames.csv"),
            "--dense-output", str(output / "trajectory_all_frames_interpolated.csv"), "--require-complete",
        ], check=True)
        manifest["trajectory_sha256"] = sha(output / "trajectory_frames.csv")
        manifest["status"] = "FRONTEND_COMPLETE_NOT_SCORED"
    except Exception as error:
        manifest.update(status="FRONTEND_FAILED", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        manifest["elapsed_s"] = time.monotonic() - started
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(manifest["status"], flush=True)


if __name__ == "__main__":
    main()
