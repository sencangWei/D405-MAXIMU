"""Isolated fixed-scale GN falsifier; no GT, trajectory export or acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_backend import build_extension, provenance
from probe_dense_native_graph import _clone_args, _validate_graph_args
from probe_stereo_depth_shape_native_graph import (
    check_chain_pnp, load_depths, pose_check, read_json, sha, validate_job_bindings,
)
from stereo_scale_targets import stereo_scale_targets_from_condition_report


def run_job(job, prior_trial, out, backend):
    validate_job_bindings(job)
    old_report = read_json(prior_trial / job["id"] / "report.json")
    old_summary = read_json(prior_trial / "summary.json")
    old_entry = next(r for r in old_summary["jobs"] if r["id"] == job["id"])
    if sha(prior_trial / job["id"] / "report.json") != old_entry["report_sha256"]:
        raise ValueError("prior stereo report hash mismatch")
    if old_report["job"] != job:
        raise ValueError("prior stereo report source mismatch")
    graph = torch.load(job["graph"], map_location="cpu", weights_only=True)
    a = graph["args"]
    _validate_graph_args(torch, a)
    targets, target_report = stereo_scale_targets_from_condition_report(a[0], old_report["conditioning"])
    depth, images, metadata = load_depths(job, graph)
    if images != old_report["images"] or metadata != old_report["stereo_source"]:
        raise ValueError("stereo source changed since predeclared ratio measurement")
    import mast3r_slam_backends
    if sha(mast3r_slam_backends.__file__) != old_report["native_backend_sha256"]:
        raise ValueError("production native backend changed")

    def solve(fn, fixed=False):
        args = _clone_args(torch, a, "cuda")
        if fixed:
            result = fn(*args, targets.cuda().contiguous())
            if not torch.equal(args[0][:, 7].cpu(), targets):
                raise ValueError("fixed scales changed during solve")
            if not torch.equal(result[0][:, 6], torch.zeros_like(result[0][:, 6])):
                raise ValueError("nonzero scale update in six-DOF solve")
        else:
            fn(*args)
        torch.cuda.synchronize()
        poses = args[0].cpu()
        pose_check(poses)
        del args
        torch.cuda.empty_cache()
        return poses

    control = solve(mast3r_slam_backends.gauss_newton_calib)
    isolated = solve(backend.gauss_newton_calib)
    if not torch.equal(control, isolated):
        raise ValueError("isolated original control is not byte-exact production replay")
    variant = solve(backend.gauss_newton_calib_fixed_scales, fixed=True)
    repeat = solve(backend.gauss_newton_calib_fixed_scales, fixed=True)
    if not torch.equal(variant, repeat):
        raise ValueError("fixed-scale repeated solve is not exact")
    if not torch.equal(variant[0], a[0][0]):
        raise ValueError("pinned pose0 changed")
    poses = {"pre": a[0], "control": control, "fixed_scales": variant}
    pnp = check_chain_pnp(graph, depth, poses)
    torch.save({"graph_sha256": sha(job["graph"]), "frame_ids": graph["frame_ids"],
                "poses": poses, "target_scales": targets}, out / "poses.pt")
    report = {"status": "DIAGNOSTIC_COMPLETE", "job": job, "diagnostic_only": True,
              "external_ground_truth_used": False, "precision_pass": False,
              "production_promoted": False, "original_control_byte_exact": True,
              "fixed_replay_exact": True, "scale_update_exact_zero": True,
              "targets_exact": True, "pinned_pose_exact": True,
              "original_Xs_edges_matching_confidence_thresholds_unchanged": True,
              "stereo_targets": target_report, "stereo_source": metadata, "images": images,
              "prior_report_sha256": sha(prior_trial / job["id"] / "report.json"),
              "chain_pnp": pnp, "poses_sha256": sha(out / "poses.pt")}
    (out / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--prior-trial", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    opt = parser.parse_args()
    if opt.output.exists() or opt.output.is_symlink():
        raise FileExistsError(opt.output)
    plan = read_json(opt.plan)
    if plan.get("external_ground_truth_used") is not False or plan.get("diagnostic_only") is not True:
        raise ValueError("source-only diagnostic plan required")
    prior = read_json(opt.prior_trial / "summary.json")
    if prior.get("plan_sha256") != sha(opt.plan) or prior.get("external_ground_truth_used") is not False:
        raise ValueError("prior trial/plan mismatch")
    ids = [j["id"] for j in plan["jobs"]]
    if len(ids) != len(set(ids)) or any(not s.replace("_", "").isalnum() for s in ids):
        raise ValueError("unsafe/duplicate IDs")
    for path, expected in plan["native_source_sha256"].items():
        if sha(path) != expected:
            raise ValueError("production native source changed")
    backend = build_extension()
    opt.output.mkdir()
    summary = {"schema": "native_fixed_stereo_scale_GN_falsifier_v1", "diagnostic_only": True,
               "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
               "plan_sha256": sha(opt.plan), "prior_summary_sha256": sha(opt.prior_trial / "summary.json"),
               "build_provenance": provenance(), "isolated_backend_sha256": sha(backend.__file__),
               "source_sha256": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")},
               "hypothesis": "Remove per-KF Sim3 scale DOF; fix s_i=s0*r_i/r0 from frozen raw stereo, original Xs unchanged.",
               "jobs": []}
    for job in plan["jobs"]:
        out = opt.output / job["id"]
        out.mkdir()
        try:
            report = run_job(job, opt.prior_trial, out, backend)
            accepted = [r for r in report["chain_pnp"] if r["accepted"]]
            row = {"id": job["id"], "status": report["status"], "accepted_chain_pairs": len(accepted),
                   "report_sha256": sha(out / "report.json")}
            if accepted:
                row["rotation_disagreement_deg"] = {
                    n: {"median": float(np.median([r["rotation_disagreement_deg"][n] for r in accepted])),
                        "max": float(np.max([r["rotation_disagreement_deg"][n] for r in accepted]))}
                    for n in ("pre", "control", "fixed_scales")}
        except Exception as error:
            row = {"id": job["id"], "status": "DIAGNOSTIC_FAILED", "error": str(error)}
        summary["jobs"].append(row)
        (opt.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        print(json.dumps(row, allow_nan=False), flush=True)
    return 0 if all(r["status"] == "DIAGNOSTIC_COMPLETE" for r in summary["jobs"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
