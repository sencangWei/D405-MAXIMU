"""Source-only native residual partition on the frozen seven-graph trial."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from audit_stereo_supported_native_edges import summarize_stereo_supported_edge
from condition_native_pointmap_depth_shape import condition_depth_shape
from probe_stereo_depth_shape_native_graph import load_depths, sha, validate_job_bindings


def compact_edge(args, poses, source, target, edge):
    """Keep exact original pixels/matches but avoid converting a full graph."""
    indices = [source, target]
    return (poses[indices].clone(), args[1][indices].clone(), args[2][indices].clone(),
            args[3], torch.tensor([0]), torch.tensor([1]),
            args[6][edge:edge+1], args[7][edge:edge+1], args[8][edge:edge+1], *args[9:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    opt = parser.parse_args()
    if opt.output.exists() or opt.output.is_symlink():
        raise FileExistsError(opt.output)
    summary = json.loads((opt.trial/"summary.json").read_text())
    if summary.get("external_ground_truth_used") is not False or summary.get("diagnostic_only") is not True:
        raise ValueError("source-only trial required")
    result = {"schema": "native_stereo_support_partition_audit_v1", "diagnostic_only": True,
              "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
              "trial_summary_sha256": sha(opt.trial/"summary.json"), "runner_sha256": sha(__file__),
              "selection": "two largest original-control stereo-PnP rotation disagreements per graph; no reference errors",
              "jobs": []}
    depth_cache = {}
    for entry in summary["jobs"]:
        report_path = opt.trial/entry["id"]/"report.json"
        if sha(report_path) != entry["report_sha256"]:
            raise ValueError("stale trial report")
        report = json.loads(report_path.read_text())
        job = report["job"]
        validate_job_bindings(job)
        pose_path = report_path.with_name("poses.pt")
        if sha(pose_path) != report["poses_sha256"]:
            raise ValueError("stale original/variant poses")
        poses = torch.load(pose_path, map_location="cpu", weights_only=True)
        graph = torch.load(job["graph"], map_location="cpu", weights_only=True)
        if poses["frame_ids"] != graph["frame_ids"] or poses["graph_sha256"] != report["graph_sha256"]:
            raise ValueError("graph/pose binding mismatch")
        accepted = [r for r in report["chain_pnp"] if r["accepted"]]
        chosen = sorted(accepted, key=lambda r:r["rotation_disagreement_deg"]["control"], reverse=True)[:2]
        edges = {(int(i),int(j)): k for k,(i,j) in enumerate(zip(graph["args"][4],graph["args"][5]))}
        rows = []
        for pair in chosen:
            ids = [pair["first_frame_id"], pair["second_frame_id"]]
            depths, bindings = [], []
            for fid in ids:
                key = (str(Path(job["dataset"]).resolve()), job["eye"], fid)
                if key not in depth_cache:
                    view = {"args": graph["args"], "frame_ids": [fid]}
                    d, b, _ = load_depths(job, view)
                    depth_cache[key] = (d[0], b[0])
                d, b = depth_cache[key]
                depths.append(d)
                bindings.append(b)
            for direction in (0,1):
                ordered = ids if direction == 0 else ids[::-1]
                indices = [graph["frame_ids"].index(fid) for fid in ordered]
                edge = edges[tuple(indices)]
                d = torch.stack(depths if direction == 0 else depths[::-1])
                valid = torch.isfinite(d)&(d>0)
                pre = compact_edge(graph["args"], poses["poses"]["pre"], *indices, edge)
                control = compact_edge(graph["args"], poses["poses"]["control"], *indices, edge)
                shaped, _ = condition_depth_shape(pre, d, valid)
                shaped_post = list(shaped)
                shaped_post[0] = poses["poses"]["depth_shape"][indices].clone()
                native = summarize_stereo_supported_edge(pre, control, 0, valid)
                conditioned = summarize_stereo_supported_edge(shaped, tuple(shaped_post), 0, valid)
                for item in (native, conditioned):
                    item.pop("supported_mask")
                    item.pop("unsupported_mask")
                rows.append({"frame_ids": ordered, "original_edge_index": edge,
                             "original_control": native, "depth_shape": conditioned,
                             "images": bindings})
        row = {"id": entry["id"], "edges": rows}
        result["jobs"].append(row)
        print(json.dumps({"id": entry["id"], "audited_edges": len(rows)}), flush=True)
        del graph, poses
    opt.output.write_text(json.dumps(result, indent=2)+"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
