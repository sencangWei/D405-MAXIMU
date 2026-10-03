#!/usr/bin/env python3
"""Bound selected-frame stereo/local-Sim3 OFF/ON diagnostic; never ATE or GT.

Default is preflight only. --run requires an idle GPU and a completed hash-bound
tracking capture. No production config, solver, model, weight or gate is changed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys

import torch

BASE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]
for directory in (BASE, ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import replay_mast3r_tracking_capture as replay
from local_tracking_pair_inputs import select_tracking_match
from local_tracking_reverse_decoder import decode_reverse_pair


def verify_stereo_inputs(manifest: dict, current: int, keyframe: int) -> dict[str, str]:
    ids, hashes = manifest.get("raw_image_frame_ids"), manifest.get("raw_image_sha256")
    if (not isinstance(ids, list) or not ids or any(type(fid) is not int or fid < 0 for fid in ids)
            or ids != sorted(set(ids)) or not {current, keyframe}.issubset(ids)
            or not isinstance(hashes, dict) or not hashes):
        raise ValueError("capture has no exact frozen raw current/reference stereo binding")
    context = manifest["source_context"]
    native, paired = Path(context["dataset"]), Path(context["paired_left_dataset"])
    with (native / "frames.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    right = json.loads((paired / "dataset_manifest.json").read_text())["stereo_depth_source"]["right_directory"]
    expected, selected = set(), set()
    for fid in ids:
        if fid >= len(rows) or int(rows[fid]["input_index"]) != fid:
            raise ValueError("raw binding frame index differs from actual source")
        paths = (native / rows[fid]["image"], paired / rows[fid]["image"], paired / right / rows[fid]["image"])
        names = {str(path.resolve(strict=True)) for path in paths}
        expected.update(names)
        if fid in (current, keyframe):
            selected.update(names)
    if set(hashes) != expected:
        raise ValueError("raw image SHA bindings missing/extra")
    replay.validate_manifest_inputs({"input_sha256": hashes})
    return {name: hashes[name] for name in sorted(selected)}


def prepare_probe(capture_run: Path, frame_id: int):
    manifest, path = replay.load_capture_run(capture_run, frame_id)
    payload = replay.load_torch_snapshot(path)
    replay.validate_capture_payload(payload, frame_id)
    selected = select_tracking_match(payload)
    verify_stereo_inputs(manifest, selected["current_frame_id"], selected["keyframe_id"])
    return manifest, payload, selected, path


def select_local_factor(factors: list[dict], current: int, keyframe: int) -> dict:
    found = [factor for factor in factors
             if factor.get("source_index") == 0 and factor.get("target_index") == 1
             and factor.get("source_frame_id") == current and factor.get("target_frame_id") == keyframe
             and factor.get("pnp_report", {}).get("accepted") is True]
    if len(found) != 1:
        raise ValueError("unique accepted current-to-keyframe metric factor unavailable")
    return found[0]


def initialize_native(manifest: dict, payload: dict, selected: dict):
    tracker_cls, sim3_cls = replay.import_native()
    from mast3r_slam import config as native_config
    source = json.loads((Path(manifest["source_run"]) / "run_manifest.json").read_text())
    for key in ("config", "checkpoint"):
        path = Path(source[key])
        if manifest["input_sha256"].get(str(path)) != replay.sha256(path):
            raise ValueError("capture config/model input binding missing or changed: " + key)
    before_cwd = Path.cwd()
    try:
        os.chdir(replay.TOOL)
        native_config.load_config(source["config"])
    finally:
        os.chdir(before_cwd)
    cfg = native_config.config
    if cfg["tracking"] != payload["track_entry"]["cfg"]:
        raise ValueError("effective tracking config differs from actual capture")
    if cfg["matching"] != selected["matching_call"].get("matching_config"):
        raise ValueError("effective matching config differs from actual native call")
    return tracker_cls, sim3_cls, cfg, source


def native_reverse_match(decoded: dict, *, device: str) -> dict:
    from mast3r_slam import matching
    X, _C, D, Q = decoded["result"]
    # Same reverse inputs and default match arguments as native symmetric match.
    # No inverted forward index, borrowed confidence, or altered distance gate.
    index, valid = matching.match(X[0:1].to(device), X[1:2].to(device), D[0:1].to(device), D[1:2].to(device))
    count = X.shape[1] * X.shape[2]
    if index.shape != (1, count) or index.dtype != torch.int64:
        raise ValueError("native reverse index shape/dtype mismatch")
    if valid.shape != (1, count, 1) or valid.dtype != torch.bool:
        raise ValueError("native reverse raw validity mismatch")
    if int(index.min()) < 0 or int(index.max()) >= count:
        raise ValueError("native reverse index out of range")
    qj = Q[0].to(device).reshape(-1, 1)
    qi = Q[1].to(device).reshape(-1, 1)
    native_q = torch.sqrt(qj[index[0]] * qi)
    return {"frame_i": decoded["frame_i"], "frame_j": decoded["frame_j"],
            "reverse_index": index[0].detach().cpu().clone(),
            "raw_reverse_valid": valid[0].detach().cpu().clone(),
            "reverse_Q": native_q.detach().cpu().clone()}


def json_trace(trace: list[dict]) -> list[dict]:
    return [{name: value.detach().cpu().reshape(-1).tolist() if torch.is_tensor(value) else value
             for name, value in row.items()} for row in trace]


def probe_sources() -> dict[str, str]:
    paths = [BASE / name for name in (
        "probe_local_tracking_metric.py", "local_tracking_pair_graph.py",
        "local_tracking_pair_inputs.py", "local_tracking_reverse_decoder.py", "local_tracking_metric_candidate.py")]
    paths.append(ROOT / "scripts/replay_mast3r_tracking_capture.py")
    return {str(path): replay.sha256(path) for path in paths}


def validate_probe_bindings(manifest: dict, selected: dict, source_hashes: dict[str, str]) -> None:
    replay.validate_manifest_inputs({"input_sha256": source_hashes})
    replay.validate_manifest_inputs(manifest)
    replay.validate_source_context(manifest)
    verify_stereo_inputs(manifest, selected["current_frame_id"], selected["keyframe_id"])


def run_probe(manifest: dict, payload: dict, selected: dict, output: Path, *, source_hashes: dict[str, str]) -> dict:
    validate_probe_bindings(manifest, selected, source_hashes)
    from local_tracking_pair_graph import make_tracking_pair_graph
    from local_tracking_metric_candidate import opt_pose_calib_sim3_with_metric_factor
    from metric_relative_pose_factor import prepare_factors
    from probe_stereo_depth_shape_native_graph import load_depths
    from mast3r_slam import mast3r_utils

    tracker_cls, sim3_cls, cfg, source = initialize_native(manifest, payload, selected)
    # Native imports/config setup precede model creation. The caller already
    # reserved the idle serial lane before creating any diagnostic output.
    off = replay.replay_twice(payload, tracker_cls, sim3_cls)
    if not off["repeat_exact"]:
        raise ValueError("original CPU local replay is not repeat-exact")
    model = mast3r_utils.load_mast3r(source["checkpoint"], device="cuda")
    decoded = decode_reverse_pair(model, selected, device="cuda")
    reverse = native_reverse_match(decoded, device="cuda")
    graph = make_tracking_pair_graph(payload, selected, reverse, local_cfg=cfg["local_opt"], sim3_cls=sim3_cls)
    current, keyframe = selected["current_frame_id"], selected["keyframe_id"]
    images_before = verify_stereo_inputs(manifest, current, keyframe)
    depth, image_rows, stereo = load_depths(manifest["source_context"], graph)
    if verify_stereo_inputs(manifest, current, keyframe) != images_before:
        raise ValueError("stereo images changed during depth read")
    factors, gate = prepare_factors(graph, depth)
    validate_probe_bindings(manifest, selected, source_hashes)
    report = {"off": off, "gate": gate, "stereo": stereo, "images": image_rows,
              "raw_image_sha256": images_before}
    if not factors:
        report.update(status="ORIGINAL_BIDIRECTIONAL_GATE_REJECTED_NOT_SCORED", on=None)
    else:
        factor = select_local_factor(factors, current, keyframe)
        tracker = tracker_cls.__new__(tracker_cls)
        tracker.cfg = dict(payload["track_entry"]["cfg"])
        world, local, trace = opt_pose_calib_sim3_with_metric_factor(
            tracker, **replay.restore_inputs(payload["opt_pose_calib_sim3"], sim3_cls), factor=factor,
            current_frame_id=current, keyframe_id=keyframe)
        # This is local factor consistency in the stereo camera frame, NOT ATE.
        ratio = factor["scale_stats"]["target"]["ratio"]
        target_translation = torch.tensor(factor["measurement"]["translation_native"], dtype=local.data.dtype)
        off_local = torch.tensor(off["runs"][0]["local_pose_data"], dtype=local.data.dtype)
        disagreements = {"off": float(torch.linalg.vector_norm((off_local[:3] - target_translation) * ratio)),
                         "on": float(torch.linalg.vector_norm((local.data[0, :3].cpu() - target_translation) * ratio))}
        report.update(status="DIAGNOSTIC_LOCAL_METRIC_COMPLETE_NOT_SCORED", factor=factor,
                      local_stereo_translation_disagreement_m=disagreements,
                      on={"world_pose_data": world.data.detach().cpu().reshape(-1).tolist(),
                          "local_pose_data": local.data.detach().cpu().reshape(-1).tolist(), "iterations": json_trace(trace)})
    # Retain the actual graph only after both the gate-rejected and ON branches
    # have completed source checks. The terminal report remains the authority.
    validate_probe_bindings(manifest, selected, source_hashes)
    torch.save({"status": report["status"], "diagnostic_only": True, "precision_pass": False,
                "probe_source_sha256": source_hashes, "raw_image_sha256": images_before,
                "graph": graph, "depth": depth, "reverse_match": reverse}, output / "pair_inputs.pt")
    validate_probe_bindings(manifest, selected, source_hashes)
    report["pair_inputs_sha256"] = replay.sha256(output / "pair_inputs.pt")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-run", type=Path, required=True)
    parser.add_argument("--frame-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    source_hashes = probe_sources()
    manifest, payload, selected, path = prepare_probe(args.capture_run, args.frame_id)
    replay.validate_manifest_inputs({"input_sha256": source_hashes})
    flags = {"schema": "umi_local_tracking_metric_probe_v1", "diagnostic_only": True,
             "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
             "is_full_frontend_evaluation": False, "capture_snapshot": str(path),
             "current_frame_id": selected.get("current_frame_id"), "keyframe_id": selected.get("keyframe_id"),
             "probe_source_sha256": source_hashes}
    if not args.run:
        print(json.dumps({**flags, "status": "PREFLIGHT_ONLY_NO_GPU"}, indent=2))
        return 0
    from run_mast3r_tracking_input_capture import require_idle_gpu
    require_idle_gpu()
    args.output.mkdir(parents=True)
    report = {**flags, "status": "RUNNING"}
    replay.write_json_new(args.output / "started.json", report)
    try:
        report.update(run_probe(manifest, payload, selected, args.output, source_hashes=source_hashes))
        validate_probe_bindings(manifest, selected, source_hashes)
        code = 0
    except Exception as error:
        report.update(status="LOCAL_METRIC_PROBE_FAILED", error=type(error).__name__ + ": " + str(error))
        code = 2
    replay.write_json_new(args.output / "report.json", report)
    print(json.dumps({key: report[key] for key in ("status", "current_frame_id", "keyframe_id")}, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
