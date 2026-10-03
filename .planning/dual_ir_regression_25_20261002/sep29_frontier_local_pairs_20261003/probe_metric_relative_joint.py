"""Frozen joint-native/metric Sim3 objective trial, not trajectory precision."""
from pathlib import Path
import argparse
import importlib.util
import json
import sys
import time

import numpy as np
import torch

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from audit_pnp_match_partition import _validate_source_trial
from audit_native_objective_change import _validate_frozen_job, summarize_edge_objective_change
from probe_dense_native_graph import _clone_args, _validate_graph_args
from probe_stereo_depth_shape_native_graph import check_chain_pnp, load_depths, pose_check, read_json, sha, validate_job_bindings
from probe_pnp_supported_outlier_mask import REQUIRED, evaluate_candidate
from joint_metric_native_solver import array_hash, solve_joint


def replay_invariants(initial, variant, repeat):
    if initial.shape != variant.shape or initial.shape != repeat.shape or initial.ndim != 2:
        raise ValueError("joint replay pose dimensions differ")
    if not all(torch.isfinite(value).all() for value in (initial, variant, repeat)):
        raise ValueError("nonfinite joint replay poses")
    return {"repeat_exact": torch.equal(variant, repeat),
            "pin_exact": torch.equal(variant[0], initial[0]),
            "repeat_max_abs": float((variant.double() - repeat.double()).abs().max()),
            "pin_max_abs": float((variant[0].double() - initial[0].double()).abs().max()),
            "initial_pose_sha256": array_hash(initial.cpu().numpy()),
            "variant_pose_sha256": array_hash(variant.cpu().numpy()),
            "repeat_pose_sha256": array_hash(repeat.cpu().numpy())}


def load_backend():
    path = BASE / "native_derivative_audit_v1/build_backend.py"
    spec = importlib.util.spec_from_file_location("metric_joint_native_inspection_build", path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build.build_extension()


def run_job(prior, row, output, backend):
    from metric_relative_pose_factor import prepare_factors
    import mast3r_slam_backends

    started = time.monotonic()
    old, saved = _validate_frozen_job(prior, row)
    job = old["job"]
    validate_job_bindings(job)
    graph = torch.load(job["graph"], map_location="cpu", weights_only=True)
    args = graph["args"]
    _validate_graph_args(torch, args)
    depth, images, metadata = load_depths(job, graph)
    if images != old["images"] or metadata != old["stereo_source"]:
        raise ValueError("frozen stereo inputs changed")
    if sha(mast3r_slam_backends.__file__) != old["native_backend_sha256"]:
        raise ValueError("production backend changed")
    factors, measurements = prepare_factors(graph, depth)
    if not factors:
        raise ValueError("no accepted physical factors")
    cuda = _clone_args(torch, args, "cuda")
    mast3r_slam_backends.gauss_newton_calib(*cuda)
    torch.cuda.synchronize()
    control = cuda[0].cpu()
    if not torch.equal(control, saved["poses"]["control"]):
        raise ValueError("original control differs from frozen replay")
    variant, trace = solve_joint(args, factors, backend)
    repeat, repeat_trace = solve_joint(args, factors, backend)
    invariants = replay_invariants(args[0], variant, repeat)
    # Persist both failed and exact runs before asserting; no tolerance change.
    replay_path = output / "invariant_diagnostic.pt"
    torch.save({"initial": args[0], "variant": variant, "repeat": repeat,
                "variant_trace": trace, "repeat_trace": repeat_trace}, replay_path)
    (output / "invariant_diagnostic.json").write_text(json.dumps({
        **invariants, "diagnostic_only": True, "external_ground_truth_used": False,
        "graph_sha256": sha(job["graph"]), "poses_sha256": sha(replay_path),
        "variant_trace": trace, "repeat_trace": repeat_trace}, indent=2, allow_nan=False) + "\n")
    pose_check(variant)
    if not invariants["repeat_exact"] or not invariants["pin_exact"]:
        raise ValueError("joint repeat/pin invariant violated; see invariant_diagnostic.json")
    poses = {"pre": args[0], "control": control, "filtered": variant}
    pnp = check_chain_pnp(graph, depth, poses)
    accepted = [pair for pair in pnp if pair["accepted"]]
    if not accepted:
        raise ValueError("no original accepted chronological PnP diagnostic")
    objectives = [summarize_edge_objective_change(args, control, variant, e, graph["frame_ids"])
                  for e in range(int(args[4].numel()))]
    costs = {"control_common": sum(v["pre_common_cost"] for v in objectives),
             "joint_common": sum(v["post_common_cost"] for v in objectives),
             "common_count": sum(v["common_count"] for v in objectives),
             "control_count": sum(v["pre_dynamic_count"] for v in objectives)}
    costs["common_support_ratio"] = costs["common_count"] / max(costs["control_count"], 1)
    costs["common_cost_ratio"] = costs["joint_common"] / max(costs["control_common"], np.finfo(float).tiny)
    torch.save({"graph_sha256": sha(job["graph"]), "frame_ids": graph["frame_ids"], "poses": poses}, output / "poses.pt")
    report = {"id": row["id"], "status": "DIAGNOSTIC_COMPLETE", "diagnostic_only": True,
              "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
              "job": job, "prior_report_sha256": row["report_sha256"], "original_control_exact": True,
              "repeat_exact": True, "pin_exact": True, "original_dense_graph_unchanged": True,
              "poses_sha256": sha(output / "poses.pt"), "measurements": measurements,
              "metric_factors": factors, "iteration_trace": trace,
              "native_objective": costs, "native_objective_edges": objectives, "chain_pnp": pnp,
              "rotation_disagreement_deg": {name: {"median": float(np.median([p["rotation_disagreement_deg"][name] for p in accepted])),
                  "max": float(np.max([p["rotation_disagreement_deg"][name] for p in accepted]))} for name in poses},
              "support_preserved": costs["common_support_ratio"] >= .99,
              "elapsed_s": time.monotonic() - started}
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return {k: report[k] for k in ("id", "status", "rotation_disagreement_deg", "support_preserved", "native_objective", "elapsed_s")} | {
        "report_sha256": sha(output / "report.json"), "metric_factor_count": len(factors)}


def verdict(rows):
    proxy = evaluate_candidate(rows)
    value = {"source_geometry_criterion_failures": list(proxy["criterion_failures"]),
             "source_geometry_criterion_met": not proxy["criterion_failures"],
             "candidate_status": "SOURCE_TRIAL_COMPLETE_REQUIRES_FIXED10_ATE",
             "formal_result": "NOT_EVALUATED_ATE", "ate_claim": False,
             "trajectory_progress": "NOT_EVALUATED_FIXED10",
             "discard_on_geometry_criterion_alone": False}
    for row in rows:
        if row.get("id", "").startswith("passing_"):
            ratio = row.get("native_objective", {}).get("common_cost_ratio")
            if ratio is None or not np.isfinite(ratio) or ratio > 2.:
                value["source_geometry_criterion_failures"].append(row["id"] + ":passing_dense_objective_exploded")
                value["source_geometry_criterion_met"] = False
    if any(row.get("status") != "DIAGNOSTIC_COMPLETE" for row in rows):
        value["candidate_status"] = "TECHNICAL_EXECUTION_FAILED"
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    opts = parser.parse_args()
    if opts.output.exists() or opts.output.is_symlink():
        raise FileExistsError(opts.output)
    torch.set_num_threads(1)
    prior = BASE / "depth_shape_GN_falsifier_v1"
    source = read_json(prior / "summary.json")
    _validate_source_trial(source)
    if len(source["jobs"]) != len(REQUIRED) or {r["id"] for r in source["jobs"]} != REQUIRED:
        raise ValueError("frozen cohort changed")
    bindings = read_json(BASE / "depth_shape_GN_falsifier_plan_v1.json")["native_source_sha256"]
    for path, expected in bindings.items():
        if sha(path) != expected:
            raise ValueError("production source changed: " + path)
    backend = load_backend()
    opts.output.mkdir()
    result = {"schema": "metric_relative_joint_native_falsifier_v1", "diagnostic_only": True,
              "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
              "runner_sha256": sha(__file__), "plan_sha256": sha(BASE / "metric_relative_joint_plan_v1.md"),
              "source_summary_sha256": sha(prior / "summary.json"), "native_source_sha256": bindings,
              "helper_sha256": {str(p): sha(p) for p in [BASE / "metric_relative_pose_factor.py",
                  BASE / "joint_metric_native_solver.py", BASE / "native_derivative_audit_v1/inspect.cpp",
                  BASE / "native_derivative_audit_v1/inspect.cu", BASE / "native_derivative_audit_v1/build_backend.py"]}, "jobs": []}
    for row in source["jobs"]:
        out = opts.output / row["id"]
        out.mkdir()
        try:
            record = run_job(prior, row, out, backend)
        except Exception as exc:
            record = {"id": row["id"], "status": "DIAGNOSTIC_FAILED", "error": str(exc)}
        result["jobs"].append(record)
        (opts.output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print(json.dumps(record), flush=True)
    result.update(verdict(result["jobs"]))
    (opts.output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return 0 if all(r["status"] == "DIAGNOSTIC_COMPLETE" for r in result["jobs"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
