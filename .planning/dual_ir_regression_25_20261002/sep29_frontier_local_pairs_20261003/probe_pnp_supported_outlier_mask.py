"""Frozen source-only membership intervention, not trajectory precision."""
from pathlib import Path
import argparse
import json
import sys
import time

import numpy as np
import torch

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from audit_pnp_match_partition import _validate_source_trial
from audit_native_objective_change import _validate_frozen_job
from probe_dense_native_graph import _clone_args, _validate_graph_args
from probe_stereo_depth_shape_native_graph import (
    check_chain_pnp, load_depths, pose_check, read_json, sha, validate_job_bindings,
)

REQUIRED = {"right587", "right592", "right613", "left877", "left1044",
            "passing_held2_752", "passing_take01_738"}


def evaluate_candidate(rows):
    by_id = {row.get("id"): row for row in rows}
    failures = []
    if len(rows) != len(REQUIRED) or set(by_id) != REQUIRED or any(
        row.get("status") != "DIAGNOSTIC_COMPLETE" for row in rows
    ):
        failures.append("technical_execution_incomplete")
    for name in ("right587", "right592", "right613"):
        value = by_id.get(name, {}).get("rotation_disagreement_deg", {}).get("filtered", {}).get("max")
        if value is None or not np.isfinite(value) or value >= 2.0:
            failures.append(name + ":right_max_not_below_2deg")
    for name in ("passing_held2_752", "passing_take01_738"):
        rotations = by_id.get(name, {}).get("rotation_disagreement_deg", {})
        before = rotations.get("control", {}).get("max")
        after = rotations.get("filtered", {}).get("max")
        if before is None or after is None or not np.isfinite([before, after]).all() or after - before > .05:
            failures.append(name + ":passing_control_regression")
    for row in rows:
        if row.get("support_preserved") is not True:
            failures.append(str(row.get("id")) + ":supported_constraints_depleted_or_missing")
    return {"candidate_status": "REJECTED_GEOMETRY_CRITERION" if failures else "GEOMETRY_CANDIDATE_ONLY",
            "formal_result": "REJECT" if failures else "NOT_EVALUATED_ATE",
            "criterion_failures": failures, "ate_claim": False}


def run_job(prior, row, output):
    from pnp_supported_outlier_mask import filter_supported_outliers
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
        raise ValueError("frozen source stereo changed")
    if sha(mast3r_slam_backends.__file__) != old["native_backend_sha256"]:
        raise ValueError("production native backend changed")
    filtered, filtering = filter_supported_outliers(graph, depth)
    if not all(torch.equal(args[k], filtered[k]) if torch.is_tensor(args[k]) else args[k] == filtered[k]
               for k in range(19) if k != 7):
        raise ValueError("unexpected graph change outside mask")
    if torch.any(filtered[7] & ~args[7]):
        raise ValueError("invalid matches added")

    def solve(values):
        cuda = _clone_args(torch, values, "cuda")
        mast3r_slam_backends.gauss_newton_calib(*cuda)
        torch.cuda.synchronize()
        poses = cuda[0].cpu()
        pose_check(poses)
        return poses

    control = solve(args)
    if not torch.equal(control, saved["poses"]["control"]):
        raise ValueError("original replay differs from frozen control")
    variant = solve(filtered)
    repeat = solve(filtered)
    if not torch.equal(variant, repeat) or not torch.equal(variant[0], args[0][0]):
        raise ValueError("variant repeat/pin0 invariant violated")
    poses = {"pre": args[0], "control": control, "filtered": variant}
    pnp = check_chain_pnp(graph, depth, poses)
    accepted = [pair for pair in pnp if pair["accepted"]]
    if not accepted:
        raise ValueError("no accepted chronological PnP diagnostic")
    # Acceptance uses ORIGINAL membership PnP, not changed masks selecting easy edges.
    torch.save({"graph_sha256": sha(job["graph"]), "frame_ids": graph["frame_ids"],
                "poses": poses}, output / "poses.pt")
    directions = [d for pair in filtering["pairs"] if pair["accepted"] for d in pair["directions"]]
    support_ok = bool(directions) and all(
        d["removed_fraction_of_supported"] <= .5 and d["retained_supported_count"] >= 100
        for d in directions)
    report = {"id": row["id"], "status": "DIAGNOSTIC_COMPLETE", "diagnostic_only": True,
              "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
              "job": job, "original_control_exact": True, "repeat_exact": True, "pin_exact": True,
              "unchanged_graph_args_except_valid_match": True, "prior_report_sha256": row["report_sha256"],
              "images": images, "stereo_source": metadata, "filtering": filtering,
              "support_preserved": support_ok, "chain_pnp": pnp,
              "poses_sha256": sha(output / "poses.pt"), "elapsed_s": time.monotonic() - started,
              "rotation_disagreement_deg": {name: {"median": float(np.median([
                  p["rotation_disagreement_deg"][name] for p in accepted])), "max": float(np.max([
                  p["rotation_disagreement_deg"][name] for p in accepted]))} for name in poses}}
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return {k: report[k] for k in ("id", "status", "rotation_disagreement_deg", "support_preserved", "elapsed_s")} | {
        "report_sha256": sha(output / "report.json"), "removed_count": filtering["total_removed_count"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists() or options.output.is_symlink():
        raise FileExistsError(options.output)
    torch.set_num_threads(1)
    prior = BASE / "depth_shape_GN_falsifier_v1"
    source = read_json(prior / "summary.json")
    _validate_source_trial(source)
    plan = read_json(BASE / "depth_shape_GN_falsifier_plan_v1.json")
    for path, expected in plan["native_source_sha256"].items():
        if sha(path) != expected:
            raise ValueError("production source changed: " + path)
    if len(source["jobs"]) != len(REQUIRED) or {row["id"] for row in source["jobs"]} != REQUIRED:
        raise ValueError("source cohort changed")
    options.output.mkdir()
    result = {"schema": "pnp_supported_outlier_mask_falsifier_v1", "diagnostic_only": True,
              "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
              "source_summary_sha256": sha(prior / "summary.json"), "runner_sha256": sha(__file__),
              "plan_sha256": sha(BASE / "pnp_supported_outlier_trial_plan_v1.md"),
              "source_sha256": {str(path): sha(path) for path in [
                  BASE / "pnp_supported_outlier_mask.py", BASE / "audit_pnp_match_partition.py",
                  BASE / "probe_stereo_depth_shape_native_graph.py", BASE / "audit_native_objective_change.py"]},
              "native_source_sha256": plan["native_source_sha256"], "jobs": []}
    for row in source["jobs"]:
        out = options.output / row["id"]
        out.mkdir()
        try:
            record = run_job(prior, row, out)
        except Exception as exc:
            record = {"id": row["id"], "status": "DIAGNOSTIC_FAILED", "error": str(exc)}
        result["jobs"].append(record)
        (options.output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print(json.dumps(record), flush=True)
    result.update(evaluate_candidate(result["jobs"]))
    (options.output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return 0 if all(row["status"] == "DIAGNOSTIC_COMPLETE" for row in result["jobs"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
