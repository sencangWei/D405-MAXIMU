#!/usr/bin/env python3
"""Fresh, source-bound MASt3R native frontend replay with metric joint OFF.

Control only. It uses the same source clock, code/context binding, native
toolchain command, and strict full-coverage converter as
run_experimental_metric_joint_frontend.py. The intended single algorithmic
difference is tracking.metric_relative_joint=False.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import yaml

from experimental_mast3r_metric_joint_adapter import ROOT, TOOL, Runtime, context_for_source, sha
from run_experimental_metric_joint_frontend import complete_source


MODE = "native_default_off_control"
TERMINAL_STATUS = "FRONTEND_COMPLETE_NOT_SCORED"


def freeze_config(config_path: Path) -> dict:
    """Flatten the official config and force only the metric-joint flag off."""
    sys.path.insert(0, str(TOOL))
    from mast3r_slam.config import load_config

    cwd = Path.cwd()
    try:
        os.chdir(TOOL)
        config = load_config(str(config_path.resolve()), is_parent=True)
    finally:
        os.chdir(cwd)
    if config.get("single_thread") is not True or config.get("dataset", {}).get("subsample") != 1:
        raise ValueError("this control requires the frozen single-thread full-frame configuration")
    tracking = config["tracking"]
    if tracking.get("metric_relative_joint"):
        raise ValueError("source config must be native baseline without existing joint objective")
    if float(tracking.get("vins_backend_position_sigma_m", 0)) > 0:
        raise ValueError("source config must preserve native baseline without VINS metric objective")
    if tracking.get("stereo_fix_pose_scale") or config["local_opt"]["pin"] != 1:
        raise ValueError("source config must preserve pin1/no post-solve scale forcing")
    config.pop("inherit", None)
    tracking["metric_relative_joint"] = False
    return config


def scrub_mast3r_env(environ: dict[str, str]) -> dict[str, str]:
    """Return a MASt3R-clean environment for a default-off native control."""
    env = {key: value for key, value in environ.items() if not key.startswith("MAST3R_")}
    env.update({
        "CUDA_HOME": str(TOOL / ".cuda"),
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    })
    return env


def output_hashes(output: Path) -> dict[str, str]:
    names = (
        "context.json",
        "effective_config.yaml",
        "trajectory_frames.csv",
        "trajectory_all_frames_interpolated.csv",
        "trajectory_frames.manifest.json",
        "mast3r.log",
    )
    return {name: sha(output / name) for name in names if (output / name).exists()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--paired-left-dataset", type=Path, required=True)
    parser.add_argument("--eye", choices=("left", "right"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=TOOL / "checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth",
    )
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
    context_path = output / "context.json"
    context_path.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
    config_path = output / "effective_config.yaml"
    config_path.write_text(yaml.safe_dump(frozen), encoding="utf-8")

    # Preflight source/code bindings before spawning the learned frontend.
    runtime = Runtime(context_path, output / "preflight.jsonl")
    runtime.log.close()
    (output / "dataset").symlink_to(dataset, target_is_directory=True)

    env = scrub_mast3r_env(os.environ.copy())
    python = str(TOOL / ".venv/bin/python")
    command = [
        python,
        "main.py",
        "--dataset",
        str(output / "dataset"),
        "--config",
        str(config_path),
        "--save-as",
        str(output / "mast3r_logs"),
        "--no-viz",
        "--no-reconstruction",
        "--calib",
        str(dataset / "calibration.yaml"),
        "--checkpoint",
        str(checkpoint),
    ]
    converter_command = [
        python,
        str(ROOT / "scripts/convert_mast3r_slam_trajectory.py"),
        "--trajectory",
        str(output / "mast3r_logs/dataset_full.txt"),
        "--frames",
        str(dataset / "frames.csv"),
        "--output",
        str(output / "trajectory_frames.csv"),
        "--dense-output",
        str(output / "trajectory_all_frames_interpolated.csv"),
        "--require-complete",
    ]
    manifest = {
        "schema": "umi_mast3r_run_v1",
        "slam_supervision": False,
        "status": "RUNNING",
        "frontend_mode": MODE,
        "native_control": True,
        "metric_relative_joint_enabled": False,
        "experimental": True,
        "precision_pass": False,
        "precision_accepted": False,
        "production_promoted": False,
        "config": str(config_path),
        "config_sha256": sha(config_path),
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": sha(checkpoint),
        "source_config": str(args.config.resolve()),
        "source_config_sha256": sha(args.config),
        "source_session": source["source_session"],
        "eye": args.eye,
        "input_frame_count": count,
        "input_sha256": context["input_sha256"],
        "context": str(context_path),
        "context_sha256": sha(context_path),
        "code_sha256": context["code_sha256"],
        "runner_sha256": sha(__file__),
        "output": str(output),
        "command": command,
        "converter_command": converter_command,
    }
    manifest_path = output / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    started = time.monotonic()
    try:
        print(f"fresh {args.eye} native default-off control: {count} frames -> {output}", flush=True)
        with (output / "mast3r.log").open("x", encoding="utf-8") as log:
            subprocess.run(command, cwd=TOOL, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        runtime.verify_bindings()
        subprocess.run(converter_command, check=True)
        runtime.verify_bindings()
        manifest["trajectory_sha256"] = sha(output / "trajectory_frames.csv")
        manifest["output_sha256"] = output_hashes(output)
        manifest["status"] = TERMINAL_STATUS
    except Exception as error:
        manifest.update(status="FRONTEND_FAILED", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        manifest["elapsed_s"] = time.monotonic() - started
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(manifest["status"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
