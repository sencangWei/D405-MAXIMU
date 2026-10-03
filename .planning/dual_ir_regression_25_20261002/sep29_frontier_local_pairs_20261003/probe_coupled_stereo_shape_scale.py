"""One source-only coupled depth-shape/scale trial, no new weights or gates."""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

import numpy as np
import torch

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from audit_native_objective_change import _validate_frozen_job
from condition_native_pointmap_depth_shape import condition_depth_shape
from probe_dense_native_graph import _clone_args, _validate_graph_args
from probe_stereo_depth_shape_native_graph import check_chain_pnp, load_depths, pose_check, read_json, sha, validate_job_bindings

def load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / "fixed_scale_native_backend_v1" / filename)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper

build = load_helper("coupled_pinned_build", "build_backend.py")
scales = load_helper("coupled_scale_targets", "stereo_scale_targets.py")


def prepare(args, depth):
    conditioned, conditioning = condition_depth_shape(args, depth, torch.isfinite(depth) & (depth > 0))
    targets, target_report = scales.stereo_scale_targets_from_condition_report(args[0], conditioning)
    return conditioned, targets, conditioning, target_report


def evaluate_candidate(summary):
    """Separate completed execution from the predeclared geometry criterion."""
    rows = {r["id"]: r for r in summary["jobs"]}
    required = {"right587", "right592", "right613", "left877", "left1044",
                "passing_held2_752", "passing_take01_738"}
    failures = []
    if set(rows) != required or any(r["status"] != "DIAGNOSTIC_COMPLETE" for r in rows.values()):
        failures.append("technical_execution_incomplete")
    for name in ["right587", "right592", "right613"]:
        value = rows.get(name, {}).get("rotation_disagreement_deg", {}).get("coupled", {}).get("max")
        if value is None or not np.isfinite(value) or value >= 2.0:
            failures.append(name + ":right_max_not_below_2deg")
    for name in ["passing_held2_752", "passing_take01_738"]:
        rotations = rows.get(name, {}).get("rotation_disagreement_deg", {})
        before = rotations.get("control", {}).get("max")
        after = rotations.get("coupled", {}).get("max")
        if before is None or after is None or not np.isfinite([before, after]).all() or after - before > .05:
            failures.append(name + ":passing_control_regression")
    return {"candidate_status": "REJECTED_GEOMETRY_CRITERION" if failures else "GEOMETRY_CANDIDATE_ONLY",
            "formal_result": "REJECT" if failures else "NOT_EVALUATED_ATE",
            "criteria": {"right_coupled_max_lt_deg": 2., "passing_control_max_regress_le_deg": .05},
            "criterion_failures": failures, "ate_claim": False}


def run_job(prior, row, output, backend):
    old, saved = _validate_frozen_job(prior, row)
    job = old["job"]
    validate_job_bindings(job)
    graph = torch.load(job["graph"], map_location="cpu", weights_only=True)
    a = graph["args"]
    _validate_graph_args(torch, a)
    depth, images, metadata = load_depths(job, graph)
    if images != old["images"] or metadata != old["stereo_source"]:
        raise ValueError("frozen stereo source changed")
    conditioned, targets, conditioning, target_report = prepare(a, depth)
    if conditioning != old["conditioning"]:
        raise ValueError("frozen stereo ratio/shape measurement changed")
    if not all(torch.equal(a[i], conditioned[i]) if torch.is_tensor(a[i]) else a[i] == conditioned[i]
               for i in range(19) if i != 1):
        raise ValueError("unexpected graph input change")
    import mast3r_slam_backends
    if sha(mast3r_slam_backends.__file__) != old["native_backend_sha256"]:
        raise ValueError("production native backend changed")

    def solve(args, fixed):
        cuda = _clone_args(torch, args, "cuda")
        if fixed:
            dx, _ = backend.gauss_newton_calib_fixed_scales(*cuda, targets.cuda().contiguous())
            if not torch.equal(cuda[0][:, 7].cpu(), targets) or torch.count_nonzero(dx[:, 6]):
                raise ValueError("fixed-scale invariant violated")
        else:
            mast3r_slam_backends.gauss_newton_calib(*cuda)
        torch.cuda.synchronize()
        poses = cuda[0].cpu()
        pose_check(poses)
        return poses

    control = solve(a, False)
    if not torch.equal(control, saved["poses"]["control"]):
        raise ValueError("control does not match frozen native replay")
    variant = solve(conditioned, True)
    repeat = solve(conditioned, True)
    if not torch.equal(variant, repeat) or not torch.equal(variant[0], a[0][0]):
        raise ValueError("repeat/pin invariant violated")
    pnp = check_chain_pnp(graph, depth, {"pre": a[0], "control": control, "coupled": variant})
    torch.save({"graph_sha256": sha(job["graph"]), "frame_ids": graph["frame_ids"],
                "poses": {"pre": a[0], "control": control, "coupled": variant}}, output / "poses.pt")
    accepted = [pair for pair in pnp if pair["accepted"]]
    if not accepted:
        raise ValueError("no accepted chronological PnP control")
    report = {"id": row["id"], "status": "DIAGNOSTIC_COMPLETE", "diagnostic_only": True,
              "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
              "job": job, "original_control_exact": True, "repeat_exact": True,
              "pin_exact": True, "scales_exact": True, "unchanged_graph_args_except_Xs": True,
              "prior_report_sha256": row["report_sha256"], "poses_sha256": sha(output / "poses.pt"),
              "conditioning": conditioning, "targets": target_report, "chain_pnp": pnp,
              "rotation_disagreement_deg": {name: {"median": float(np.median([p["rotation_disagreement_deg"][name] for p in accepted])),
                  "max": float(np.max([p["rotation_disagreement_deg"][name] for p in accepted]))} for name in ["control", "coupled"]}}
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return {k: report[k] for k in ["id", "status", "rotation_disagreement_deg"]} | {"report_sha256": sha(output / "report.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists() or options.output.is_symlink():
        raise FileExistsError(options.output)
    torch.set_num_threads(1)
    prior = BASE / "depth_shape_GN_falsifier_v1"
    summary = read_json(prior / "summary.json")
    if (summary.get("schema") != "native_depth_shape_GN_falsifier_v1"
            or summary.get("diagnostic_only") is not True or summary.get("external_ground_truth_used") is not False
            or summary.get("production_promoted") is not False):
        raise ValueError("invalid source diagnostic")
    backend = build.build_extension()
    options.output.mkdir()
    result = {"schema": "coupled_stereo_shape_scale_falsifier_v1", "diagnostic_only": True,
              "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
              "source_summary_sha256": sha(prior / "summary.json"),
              "source_plan_sha256": summary.get("plan_sha256"),
              "source_runner_sha256": summary.get("runner_sha256"),
              "source_helper_sha256": summary.get("helper_sha256"),
              "helper_sha256": {str(p): sha(p) for p in [BASE / "condition_native_pointmap_depth_shape.py",
                  BASE / "probe_stereo_depth_shape_native_graph.py", BASE / "audit_native_objective_change.py",
                  BASE / "fixed_scale_native_backend_v1/stereo_scale_targets.py"]},
              "plan_sha256": sha(BASE / "coupled_stereo_shape_scale_plan_v1.md"),
              "runner_sha256": sha(__file__), "build_provenance": build.provenance(), "jobs": []}
    for row in summary["jobs"]:
        out = options.output / row["id"]
        out.mkdir()
        try:
            record = run_job(prior, row, out, backend)
        except Exception as exc:
            record = {"id": row["id"], "status": "DIAGNOSTIC_FAILED", "error": str(exc)}
        result["jobs"].append(record)
        (options.output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print(json.dumps(record), flush=True)
    result.update(evaluate_candidate(result))
    (options.output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return 0 if all(j["status"] == "DIAGNOSTIC_COMPLETE" for j in result["jobs"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
