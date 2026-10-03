#!/usr/bin/env python3
"""CPU diagnostic replay of captured MASt3R opt_pose_calib_sim3; not ATE/GT."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from types import MethodType
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOL = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
CAPTURE_SCHEMA = "mast3r_tracking_input_capture_v1"
REPORT_SCHEMA = "umi_mast3r_tracking_opt_replay_v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_new(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def load_torch_snapshot(path: Path) -> dict[str, Any]:
    import torch

    payload = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(payload, dict):
        raise ValueError("capture snapshot is not a dict")
    return payload


def load_capture_run(capture_run: Path, frame_id: int) -> tuple[dict[str, Any], Path]:
    capture_run = capture_run.resolve(strict=True)
    manifest = json.loads((capture_run / "capture_run_manifest.json").read_text(encoding="utf-8"))
    validate_capture_manifest(manifest)
    validate_manifest_inputs(manifest)
    validate_source_context(manifest)
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from run_mast3r_tracking_input_capture import validate_capture

    requested = manifest.get("requested_frame_ids") or []
    if frame_id not in requested:
        raise ValueError("selected frame is not in capture-run requested_frame_ids")
    hashes = validate_capture(capture_run / "captures", requested)
    if manifest.get("snapshot_sha256") != hashes:
        raise ValueError("capture-run output snapshot hashes changed")
    report = json.loads((capture_run / "captures/summary.json").read_text(encoding="utf-8"))
    rows = [row for row in report["captures"] if row.get("frame_id") == frame_id]
    if len(rows) != 1:
        raise ValueError("expected exactly one snapshot for selected frame")
    name = rows[0]["path"]
    if name not in hashes:
        raise ValueError("selected snapshot was not validated")
    return manifest, capture_run / "captures" / name


def validate_capture_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != "umi_bound_tracking_capture_run_v1":
        raise ValueError("capture-run manifest schema mismatch")
    if manifest.get("status") not in ("TRACKING_INPUTS_CAPTURED_NOT_SCORED", "CAPTURED_NOT_SCORED"):
        raise ValueError("capture-run terminal status is not captured-not-scored")
    if manifest.get("precision_pass") is not False:
        raise ValueError("capture-run must not be a precision pass")
    if manifest.get("external_ground_truth_used") is not False:
        raise ValueError("capture-run must not use ground truth")
    if manifest.get("production_promoted") is not False:
        raise ValueError("capture-run must not be promoted")
    if not isinstance(manifest.get("source_context"), dict):
        raise ValueError("capture-run source_context missing")
    if not isinstance(manifest.get("input_sha256"), dict):
        raise ValueError("capture-run input snapshot missing")
    if not isinstance(manifest.get("snapshot_sha256"), dict):
        raise ValueError("capture-run output snapshot hashes missing")


def validate_manifest_inputs(manifest: dict[str, Any]) -> None:
    for text, expected in manifest["input_sha256"].items():
        path = Path(text)
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"capture-run input changed: {text}")


def validate_source_context(manifest: dict[str, Any]) -> None:
    context = manifest["source_context"]
    required = {
        "schema",
        "external_ground_truth_used",
        "dataset",
        "paired_left_dataset",
        "eye",
        "input_sha256",
        "code_sha256",
    }
    if set(context) != required:
        raise ValueError("capture-run source_context fields/schema mismatch")
    if context["external_ground_truth_used"] is not False:
        raise ValueError("capture-run source_context must not use ground truth")
    if context["eye"] not in ("left", "right"):
        raise ValueError("capture-run source_context eye is invalid")
    if not isinstance(context["dataset"], str) or not isinstance(context["paired_left_dataset"], str):
        raise ValueError("capture-run source_context paths must be strings")
    if not isinstance(context["input_sha256"], dict):
        raise ValueError("capture-run source_context input_sha256 missing")
    code_sha256 = context.get("code_sha256")
    if not isinstance(code_sha256, dict):
        raise ValueError("capture-run source_context code_sha256 missing")
    dataset, paired, eye = context.get("dataset"), context.get("paired_left_dataset"), context.get("eye")
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import experimental_mast3r_metric_joint_adapter as adapter
    if context["schema"] != getattr(adapter, "SCHEMA", None):
        raise ValueError("capture-run source_context fields/schema mismatch")
    code_paths = getattr(adapter, "CODE_PATHS", {})
    if set(code_sha256) != set(code_paths):
        raise ValueError("capture-run source_context CODE_PATHS bindings missing/extra")
    for name, path in code_paths.items():
        path = Path(path)
        if not path.is_file():
            raise ValueError(f"current CODE_PATHS binding missing: {name}")
        if code_sha256[name] != sha256(path):
            raise ValueError(f"current CODE_PATHS binding changed: {name}")
    if context != adapter.context_for_source(Path(dataset), Path(paired), eye):
        raise ValueError("source_context no longer matches current bindings")


def validate_capture_payload(payload: dict[str, Any], frame_id: int) -> dict[str, Any]:
    if payload.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("capture payload schema mismatch")
    if payload.get("status") != "ok":
        raise ValueError("capture payload is incomplete or ERROR")
    if payload.get("track_entry", {}).get("frame_id") != frame_id:
        raise ValueError("capture payload frame_id mismatch")
    opt = payload.get("opt_pose_calib_sim3")
    if not isinstance(opt, dict) or opt.get("error") is not None or opt.get("return") is None:
        raise ValueError("capture payload lacks successful opt_pose_calib_sim3")
    if opt.get("metric_translation_target") is not None:
        raise ValueError("VINS metric translation target is unsupported by this replay")
    return opt


def clone_tensor(value: Any):
    import torch

    if not torch.is_tensor(value):
        raise TypeError("expected tensor")
    return value.detach().cpu().clone()


def restore_sim3(value: Any, sim3_cls: type):
    data = value.get("data") if isinstance(value, dict) else None
    if data is None:
        raise ValueError("captured Sim3 is missing data")
    data = clone_tensor(data)
    if tuple(data.shape) != (1, 8):
        raise ValueError(f"captured Sim3 data must be batch (1,8), got {tuple(data.shape)}")
    return sim3_cls(data)


def restore_inputs(opt: dict[str, Any], sim3_cls: type) -> dict[str, Any]:
    return {
        "Xf": clone_tensor(opt["Xf"]),
        "Xk": clone_tensor(opt["Xk"]),
        "T_WCf": restore_sim3(opt["T_WCf"], sim3_cls),
        "T_WCk": restore_sim3(opt["T_WCk"], sim3_cls),
        "Qk": clone_tensor(opt["Qk"]),
        "valid": clone_tensor(opt["valid"]),
        "conf_w": clone_tensor(opt["conf_w"]),
        "meas_k": clone_tensor(opt["meas"]),
        "valid_meas_k": clone_tensor(opt["valid_meas"]),
        "K": clone_tensor(opt["K"]),
        "img_size": tuple(opt["img_size"]),
        "metric_translation_target": None,
        "metric_world_scale": float(opt.get("metric_world_scale", 1.0)),
    }


def max_abs(a, b) -> float:
    import torch

    return float(torch.max(torch.abs(a - b)).item())


def replay_once(payload: dict[str, Any], tracker_cls: type, sim3_cls: type) -> dict[str, Any]:
    opt = validate_capture_payload(payload, int(payload["track_entry"]["frame_id"]))
    tracker = tracker_cls.__new__(tracker_cls)
    tracker.cfg = dict(payload["track_entry"].get("cfg") or {})
    increments: list[dict[str, Any]] = []
    original_solve = tracker_cls.solve_pose_increment

    def recording_solve(self, *args, **kwargs):
        tau, cost = original_solve(self, *args, **kwargs)
        increments.append({"cost": float(cost), "tau": tau.detach().cpu().clone()})
        return tau, cost

    tracker.solve_pose_increment = MethodType(recording_solve, tracker)
    T_WCf, T_CkCf = tracker_cls.opt_pose_calib_sim3(tracker, **restore_inputs(opt, sim3_cls))
    expected = opt["return"]
    return {
        "world_pose": T_WCf.data.detach().cpu().clone(),
        "local_pose": T_CkCf.data.detach().cpu().clone(),
        "increments": increments,
        "increment_diffs_vs_capture": compare_increments(increments, payload.get("solve_pose_increment", [])),
        "world_pose_diff_vs_capture": max_abs(T_WCf.data.detach().cpu(), expected["T_WCf"]["data"]),
        "local_pose_diff_vs_capture": max_abs(T_CkCf.data.detach().cpu(), expected["T_CkCf"]["data"]),
    }


def compare_increments(actual: list[dict[str, Any]], captured: list[dict[str, Any]]) -> dict[str, Any]:
    diffs = []
    for idx, (got, expected) in enumerate(zip(actual, captured)):
        diffs.append({
            "iteration": idx,
            "cost_abs_diff": abs(got["cost"] - float(expected["cost"])),
            "tau_max_abs_diff": max_abs(got["tau"], expected["tau"]),
        })
    return {
        "actual_count": len(actual),
        "captured_count": len(captured),
        "count_match": len(actual) == len(captured),
        "matched_diffs": diffs,
        "missing_captured_iterations": max(0, len(actual) - len(captured)),
        "extra_captured_iterations": max(0, len(captured) - len(actual)),
    }


def increments_exact(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    if len(left) != len(right):
        return False
    return all(
        got["cost"] == expected["cost"] and max_abs(got["tau"], expected["tau"]) == 0.0
        for got, expected in zip(left, right)
    )


def replay_twice(payload: dict[str, Any], tracker_cls: type, sim3_cls: type) -> dict[str, Any]:
    first = replay_once(payload, tracker_cls, sim3_cls)
    second = replay_once(payload, tracker_cls, sim3_cls)
    return {
        "schema": REPORT_SCHEMA,
        "status": "DIAGNOSTIC_REPLAY_COMPLETE_NOT_SCORED",
        "diagnostic_only": True,
        "is_full_frontend_evaluation": False,
        "external_ground_truth_used": False,
        "precision_pass": False,
        "production_promoted": False,
        "units": "native optimizer units, not metres/ATE",
        "repeat_exact": bool(
            max_abs(first["world_pose"], second["world_pose"]) == 0.0
            and max_abs(first["local_pose"], second["local_pose"]) == 0.0
            and increments_exact(first["increments"], second["increments"])
        ),
        "runs": [summarize_run(first), summarize_run(second)],
    }


def summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "iterations": [
            {"cost": item["cost"], "tau": item["tau"].reshape(-1).tolist()}
            for item in run["increments"]
        ],
        "world_pose_diff_vs_captured_opt_return": run["world_pose_diff_vs_capture"],
        "local_pose_diff_vs_captured_opt_return": run["local_pose_diff_vs_capture"],
        "iteration_diffs_vs_captured_observer": run["increment_diffs_vs_capture"],
        "world_pose_data": run["world_pose"].reshape(-1).tolist(),
        "local_pose_data": run["local_pose"].reshape(-1).tolist(),
    }


def import_native():
    for key in list(os.environ):
        if key.startswith("MAST3R_"):
            os.environ.pop(key, None)
    if str(TOOL) not in sys.path:
        sys.path.insert(0, str(TOOL))
    from mast3r_slam.tracker import FrameTracker
    import lietorch

    return FrameTracker, lietorch.Sim3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frame-id", type=int)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    manifest = json.loads((args.capture_run / "capture_run_manifest.json").read_text(encoding="utf-8"))
    requested = manifest.get("requested_frame_ids") or []
    if args.frame_id is None:
        if len(requested) != 1:
            raise ValueError("--frame-id is required for multi-frame capture runs")
        frame_id = int(requested[0])
    else:
        frame_id = int(args.frame_id)
    _manifest, snapshot_path = load_capture_run(args.capture_run, frame_id)
    payload = load_torch_snapshot(snapshot_path)
    validate_capture_payload(payload, frame_id)
    tracker_cls, sim3_cls = import_native()
    report = replay_twice(payload, tracker_cls, sim3_cls)
    report["capture_run"] = str(args.capture_run.resolve())
    report["capture_snapshot"] = snapshot_path.name
    write_json_new(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
