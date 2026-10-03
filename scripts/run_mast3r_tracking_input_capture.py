#!/usr/bin/env python3
"""Source-bound, read-only tracking-input capture; not a full SLAM evaluation.

Replay the unchanged retained frontend from frame zero past the requested locus.
The observer only clones inputs/outputs; no objective,
weight, frame, scale or precision gate is changed. Never run alongside a GPU
model producer. Prefix exports are diagnostics, not complete trajectories.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from evaluate_metric_joint_frontend import assert_unchanged, snapshot, validate_frontend, write_json
from experimental_mast3r_metric_joint_adapter import ROOT, TOOL, Runtime, context_for_source, sha
from run_experimental_metric_joint_frontend import complete_source

OBSERVER = ROOT / "scripts/capture_mast3r_tracking_inputs.py"


def capture_bounds(frame_ids: list[int], count: int) -> tuple[list[int], int]:
    if not frame_ids or any(type(fid) is not int or not 0 <= fid < count for fid in frame_ids):
        raise ValueError("capture ids must be actual input indices within the full source")
    targets = sorted(set(frame_ids))
    return targets, min(count, targets[-1] + 2)


def require_idle_gpu() -> None:
    processes = subprocess.run(["ps", "-eo", "pid=,args="], check=True, text=True, capture_output=True)
    for line in processes.stdout.splitlines():
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if "--run" in parts and any(Path(part).name == "run_metric_joint_fast10.py" for part in parts):
            raise RuntimeError("GPU producer queue is active, including between model processes")
    result = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader,nounits"],
        check=True, text=True, capture_output=True,
    )
    producers = [line for line in result.stdout.splitlines() if line.strip()
                 and line.split(",", 1)[-1].strip() != "/usr/libexec/gnome-remote-desktop-daemon"]
    if producers:
        raise RuntimeError("GPU producer is active; preserve the serial queue: " + "; ".join(producers))


def capture_command(output: Path, source: dict, targets: list[int], prefix: int) -> list[str]:
    command = [str(TOOL / ".venv/bin/python"), str(OBSERVER),
               "--capture-dir", str(output / "captures"), "--prefix-count", str(prefix)]
    for fid in targets:
        command.extend(["--frame-id", str(fid)])
    return command + ["--", str(TOOL / "main.py"),
                      "--dataset", str(output / "dataset"), "--config", source["config"],
                      "--save-as", str(output / "prefix_logs"), "--no-viz", "--no-reconstruction",
                      "--calib", str(output / "dataset/calibration.yaml"),
                      "--checkpoint", source["checkpoint"]]


def validate_capture(directory: Path, targets: list[int]) -> dict[str, str]:
    report = json.loads((directory / "summary.json").read_text())
    if (report.get("schema") != "umi_mast3r_tracking_input_capture_v1"
            or report.get("status") != "CAPTURED_NOT_SCORED"
            or report.get("external_ground_truth_used") is not False
            or report.get("precision_pass") is not False
            or report.get("production_promoted") is not False
            or report.get("requested_frame_ids") != targets or report.get("missing_frame_ids") != []):
        raise ValueError("incomplete or unbound tracking capture report")
    rows = report.get("captures", [])
    if not rows or {row.get("frame_id") for row in rows} != set(targets):
        raise ValueError("capture does not cover every requested actual frame")
    hashes = {}
    for row in rows:
        name = row.get("path")
        if (row.get("status") != "CAPTURED" or not isinstance(name, str)
                or Path(name).name != name or name in hashes):
            raise ValueError("failed, duplicate or unsafe tracking snapshot")
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
        hashes[name] = sha(path)
    return hashes


def bound_source(source_run: Path) -> tuple[dict, dict, int]:
    manifest = json.loads((source_run / "run_manifest.json").read_text())
    eye = manifest.get("eye")
    if eye not in ("left", "right"):
        raise ValueError("source-run eye is missing")
    validate_frontend(source_run, eye, expected_session=Path(manifest["source_session"]).resolve())
    context = json.loads((source_run / "context.json").read_text())
    dataset, paired = Path(context["dataset"]), Path(context["paired_left_dataset"])
    if context != context_for_source(dataset, paired, eye):
        raise ValueError("source code/input context changed since the retained full replay")
    _source, count = complete_source(dataset, paired, eye)
    if sha(Path(manifest["checkpoint"])) != manifest["checkpoint_sha256"]:
        raise ValueError("source model checkpoint changed")
    return manifest, context, count


def capture_image_hashes(runtime, captures: Path, snapshot_hashes: dict, identities: dict) -> tuple[list[int], dict[str, str]]:
    """Freeze only the actual current/reference stereo inputs after capture."""
    import torch

    frame_ids = set()
    for name, expected in snapshot_hashes.items():
        path = captures / name
        if sha(path) != expected:
            raise ValueError("tracking snapshot changed before image binding")
        payload = torch.load(path, map_location="cpu", weights_only=True)
        track, gp = payload.get("track_entry", {}), payload.get("get_points_poses") or {}
        current, reference = gp.get("frame_id"), gp.get("keyframe_id")
        if (track.get("frame_id") != current or track.get("reference_frame_id") != reference
                or any(type(fid) is not int or fid not in identities for fid in (current, reference))):
            raise ValueError("captured current/reference pair is outside the bound prefix or mismatched")
        frame_ids.update((current, reference))
    image_hashes = {}
    for fid in sorted(frame_ids):
        if runtime.image_identity(fid) != identities[fid]:
            raise ValueError("raw image changed after capture: " + str(fid))
        row = runtime.rows[fid]
        paths = (runtime.native / row["image"], runtime.paired / row["image"],
                 runtime.paired / runtime.right_directory / row["image"])
        for path in paths:
            image_hashes[str(path.resolve(strict=True))] = sha(path)
        if runtime.image_identity(fid) != identities[fid]:
            raise ValueError("raw image changed during image hashing: " + str(fid))
    return sorted(frame_ids), image_hashes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frame-id", type=int, action="append", required=True)
    parser.add_argument("--run", action="store_true", help="default: preflight/dry-run only")
    args = parser.parse_args(argv)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("capture output must be new: " + str(output))
    source_run = args.source_run.resolve(strict=True)
    source, context, count = bound_source(source_run)
    targets, prefix = capture_bounds(args.frame_id, count)
    command = capture_command(output, source, targets, prefix)
    paths = [source_run / "run_manifest.json", source_run / "context.json", Path(source["config"]),
             Path(source["checkpoint"]), OBSERVER, Path(__file__),
             ROOT / "scripts/run_experimental_metric_joint_frontend.py"]
    before = snapshot(paths)
    manifest = {"schema": "umi_bound_tracking_capture_run_v1", "status": "PLANNED",
                "diagnostic_only": True, "is_full_frontend_evaluation": False,
                "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
                "source_run": str(source_run), "source_frame_count": count, "prefix_frame_count": prefix,
                "requested_frame_ids": targets, "source_context": context, "input_sha256": before,
                "command": command}
    if not args.run:
        print(json.dumps(manifest, indent=2))
        return 0
    require_idle_gpu()
    output.mkdir(parents=True)
    started = time.monotonic()
    manifest["status"] = "RUNNING"
    write_json(output / "capture_run_manifest.json", manifest)
    try:
        (output / "dataset").symlink_to(context["dataset"], target_is_directory=True)
        context_path = output / "context.json"
        write_json(context_path, context)
        runtime = Runtime(context_path, output / "preflight.jsonl")
        try:
            identities = {fid: runtime.image_identity(fid) for fid in range(prefix)}
        finally:
            runtime.log.close()
        env = {name: value for name, value in os.environ.items() if not name.startswith("MAST3R_")}
        env.update({"CUDA_HOME": str(TOOL / ".cuda"), "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                    "MAST3R_METRIC_RELATIVE_JOINT_ADAPTER": str(ROOT / "scripts/experimental_mast3r_metric_joint_adapter.py"),
                    "MAST3R_METRIC_RELATIVE_JOINT_CONTEXT": str(context_path),
                    "MAST3R_METRIC_RELATIVE_JOINT_LOG": str(output / "solves.jsonl")})
        assert_unchanged(before)
        runtime.verify_bindings()
        require_idle_gpu()
        with (output / "capture.log").open("x") as stream:
            subprocess.run(command, cwd=TOOL, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        assert_unchanged(before)
        runtime.verify_bindings()
        if any(runtime.image_identity(fid) != identity for fid, identity in identities.items()):
            raise ValueError("raw image identities changed during prefix replay")
        manifest["snapshot_sha256"] = validate_capture(output / "captures", targets)
        manifest["raw_image_frame_ids"], manifest["raw_image_sha256"] = capture_image_hashes(
            runtime, output / "captures", manifest["snapshot_sha256"], identities
        )
        manifest["status"] = "TRACKING_INPUTS_CAPTURED_NOT_SCORED"
        return 0
    except Exception as error:
        manifest.update(status="CAPTURE_FAILED", error=type(error).__name__ + ": " + str(error))
        return 2
    finally:
        manifest["elapsed_s"] = time.monotonic() - started
        write_json(output / "capture_run_manifest.json", manifest)
        print(json.dumps({key: manifest[key] for key in ("status", "requested_frame_ids", "elapsed_s")}))


if __name__ == "__main__":
    raise SystemExit(main())
