"""Audit original CUDA derivatives on seven hash-bound graphs; never solve."""
from pathlib import Path
import argparse
import json
import sys

import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import audit_native_objective_change as frozen
from build_backend import build_extension, pinned
from derivatives import check_edge, retract_double


def audit_job(trial, row, backend):
    report, poses = frozen._validate_frozen_job(trial, row)
    graph = torch.load(report["job"]["graph"], map_location="cpu", weights_only=True)
    args = graph["args"]
    frame_ids = [int(i) for i in graph["frame_ids"]]
    pairs = sorted([pair for pair in report["chain_pnp"] if pair["accepted"]],
                   key=lambda pair: pair["rotation_disagreement_deg"]["control"], reverse=True)[:2]
    selected = {(p["first_frame_id"], p["second_frame_id"]) for p in pairs}
    if row["id"] == "right587":
        selected.add((541, 587))
    records = []
    for edge, (i, j) in enumerate(zip(args[4].tolist(), args[5].tolist())):
        pair = (frame_ids[i], frame_ids[j])
        if pair not in selected and pair[::-1] not in selected:
            continue
        compact = frozen.compact_edge_args(torch, args, edge, poses["poses"]["pre"])
        gpu = tuple(t.cuda().contiguous() if torch.is_tensor(t) else t for t in compact[:17])
        before = gpu[0].clone()
        H, g = backend.inspect_calibrated(*gpu)
        torch.cuda.synchronize()
        if not torch.equal(before, gpu[0]):
            raise ValueError("inspection mutated input poses")
        record = check_edge(compact, H, g)
        # Independently confirm the matrix-exp perturbation uses native left retraction.
        dx = torch.zeros(2, 7, dtype=torch.float64)
        dx[1] = torch.tensor([.0003, -.0002, .0001, .0002, -.0003, .0001, .0002])
        native = backend.inspect_retract(gpu[0], dx.float().cuda())
        expected = retract_double(compact[0], dx)
        record["native_retraction_matrix_exp_max_abs"] = float((native.cpu().double() - expected).abs().max())
        record.update({"edge_index": edge, "source_frame_id": pair[0], "target_frame_id": pair[1]})
        records.append(record)
    if not records:
        raise ValueError("no accepted diagnostic pairs")
    return {"id": row["id"], "status": "AUDIT_COMPLETE", "graph_sha256": report["graph_sha256"],
            "report_sha256": row["report_sha256"], "poses_sha256": report["poses_sha256"], "edges": records,
            "all_derivatives_agree": all(e["derivative_agreement"] for e in records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, default=HERE.parent / "depth_shape_GN_falsifier_v1")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists() or options.output.is_symlink():
        raise FileExistsError(options.output)
    torch.set_num_threads(1)
    summary = frozen.read_json(options.trial / "summary.json")
    if (summary.get("schema") != "native_depth_shape_GN_falsifier_v1"
            or summary.get("diagnostic_only") is not True
            or summary.get("external_ground_truth_used") is not False
            or summary.get("production_promoted") is not False):
        raise ValueError("source-only diagnostic trial required")
    backend = build_extension()
    result = {"schema": "native_calibrated_derivative_audit_v1", "diagnostic_only": True,
              "external_ground_truth_used": False, "production_promoted": False, "precision_pass": False,
              "trial_summary_sha256": frozen.sha(options.trial / "summary.json"),
              "source_trial_schema": summary["schema"],
              "source_plan_sha256": summary.get("plan_sha256"),
              "source_runner_sha256": summary.get("runner_sha256"),
              "source_helper_sha256": summary.get("helper_sha256"),
              "code_sha256": {str(p): frozen.sha(p) for p in [HERE / "inspect.cpp", HERE / "inspect.cu", HERE / "build_backend.py", HERE / "derivatives.py", HERE / "probe.py", HERE.parent / "audit_calibrated_graph_edges.py", HERE.parent / "audit_native_objective_change.py"]},
              "pinned_native_sources": pinned.verify_source_hashes(), "jobs": []}
    for row in summary["jobs"]:
        try:
            record = audit_job(options.trial, row, backend)
            # Fail this job, not the entire batch, on nonfinite diagnostic values.
            json.dumps(record, allow_nan=False)
        except Exception as exc:
            record = {"id": row["id"], "status": "AUDIT_FAILED", "error": str(exc)}
        result["jobs"].append(record)
        options.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print(record["id"], record["status"], record.get("all_derivatives_agree"), flush=True)
    return 0 if all(j["status"] == "AUDIT_COMPLETE" for j in result["jobs"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
