"""CPU-only native calibrated objective change audit.

Uses frozen graph inputs and frozen pre/original-control poses from
``depth_shape_GN_falsifier_v1``.  Costs are analysis-only true Huber(k=1.345)
on the existing calibrated residual helper; they are not native bit-exact GN or
IRLS costs.  No inputs, weights, masks, or solver state are modified.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


BASE = Path(__file__).resolve().parent
DEFAULT_TRIAL = BASE / "depth_shape_GN_falsifier_v1"
DEFAULT_OUTPUT = BASE / "native_objective_change_v1.json"
SCHEMA = "native_objective_change_v1"


def _load_edge_audit():
    path = BASE / "audit_calibrated_graph_edges.py"
    spec = importlib.util.spec_from_file_location("audit_calibrated_graph_edges", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(path)
    spec.loader.exec_module(module)
    return module


edge_audit = _load_edge_audit()


def sha(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def compact_edge_args(torch: Any, args: tuple[Any, ...], edge_index: int, poses: Any) -> tuple[Any, ...]:
    """Return a compact 2-frame/1-edge args tuple for calibrated residuals."""
    ii = args[4].detach().cpu().to(dtype=torch.long)
    jj = args[5].detach().cpu().to(dtype=torch.long)
    edge_index = int(edge_index)
    if edge_index < 0 or edge_index >= int(ii.numel()):
        raise ValueError("edge_index out of bounds")
    source = int(ii[edge_index])
    target = int(jj[edge_index])
    frame_idx = torch.tensor([source, target], dtype=torch.long)
    compact = (
        poses.index_select(0, frame_idx).contiguous(),
        args[1].detach().cpu().index_select(0, frame_idx).contiguous(),
        args[2].detach().cpu().index_select(0, frame_idx).contiguous(),
        args[3].detach().cpu().contiguous(),
        torch.tensor([0], dtype=torch.long),
        torch.tensor([1], dtype=torch.long),
        args[6].detach().cpu()[edge_index:edge_index + 1].contiguous(),
        args[7].detach().cpu()[edge_index:edge_index + 1].contiguous(),
        args[8].detach().cpu()[edge_index:edge_index + 1].contiguous(),
        *args[9:],
    )
    return compact


def _cost(torch: Any, residuals: dict[str, Any], mask: Any) -> tuple[int, float]:
    count = int(mask.sum().item())
    if count == 0:
        return 0, 0.0
    return count, float(edge_audit._huber_rho(torch, residuals["whitened"][mask]).sum().item())


def summarize_edge_objective_change(
    args: tuple[Any, ...],
    pre_poses: Any,
    post_poses: Any,
    edge_index: int,
    frame_ids: list[int],
) -> dict[str, Any]:
    import torch

    pre_args = compact_edge_args(torch, args, edge_index, pre_poses)
    post_args = compact_edge_args(torch, args, edge_index, post_poses)
    pre = edge_audit.calibrated_edge_residuals(pre_args, 0)
    post = edge_audit.calibrated_edge_residuals(post_args, 0)
    pre_mask = pre["mask"]
    post_mask = post["mask"]
    common = pre_mask & post_mask
    gained = post_mask & ~pre_mask
    lost = pre_mask & ~post_mask
    pre_dynamic_count, pre_dynamic_cost = _cost(torch, pre, pre_mask)
    post_dynamic_count, post_dynamic_cost = _cost(torch, post, post_mask)
    common_count = int(common.sum().item())
    pre_common_cost = _cost(torch, pre, common)[1]
    post_common_cost = _cost(torch, post, common)[1]

    source = int(args[4].detach().cpu()[edge_index])
    target = int(args[5].detach().cpu()[edge_index])
    return {
        "edge_index": int(edge_index),
        "source_index": source,
        "target_index": target,
        "source_frame_id": int(frame_ids[source]),
        "target_frame_id": int(frame_ids[target]),
        "pre_dynamic_count": pre_dynamic_count,
        "post_dynamic_count": post_dynamic_count,
        "common_count": common_count,
        "gained_count": int(gained.sum().item()),
        "lost_count": int(lost.sum().item()),
        "pre_dynamic_cost": pre_dynamic_cost,
        "post_dynamic_cost": post_dynamic_cost,
        "pre_common_cost": pre_common_cost,
        "post_common_cost": post_common_cost,
        "common_cost_delta": post_common_cost - pre_common_cost,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[len(ordered) // 2])


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
    return float(ordered[index])


def _validate_frozen_job(trial: Path, summary_row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    job_id = summary_row["id"]
    report_path = trial / job_id / "report.json"
    poses_path = trial / job_id / "poses.pt"
    if sha(report_path) != summary_row["report_sha256"]:
        raise ValueError(f"{job_id}: report hash mismatch")
    report = read_json(report_path)
    if report.get("status") != "DIAGNOSTIC_COMPLETE":
        raise ValueError(f"{job_id}: report not complete")
    if report.get("job", {}).get("id") != job_id:
        raise ValueError(f"{job_id}: report/job id mismatch")
    if not report.get("poses_sha256"):
        raise ValueError(f"{job_id}: missing frozen poses_sha256")
    if sha(poses_path) != report["poses_sha256"]:
        raise ValueError(f"{job_id}: poses hash mismatch")
    poses = __import__("torch").load(poses_path, map_location="cpu", weights_only=True)
    if poses.get("graph_sha256") != report.get("graph_sha256"):
        raise ValueError(f"{job_id}: pose graph hash mismatch")
    if "pre" not in poses["poses"] or "control" not in poses["poses"]:
        raise ValueError(f"{job_id}: missing pre/control poses")
    if report.get("baseline_replay_exact") is not True:
        raise ValueError(f"{job_id}: original-control replay was not exact")
    graph_path = Path(report["job"]["graph"])
    if sha(graph_path) != report["graph_sha256"]:
        raise ValueError(f"{job_id}: graph hash mismatch")
    return report, poses


def audit_job(trial: Path, summary_row: dict[str, Any]) -> dict[str, Any]:
    import torch

    report, poses = _validate_frozen_job(trial, summary_row)
    graph = torch.load(report["job"]["graph"], map_location="cpu", weights_only=True)
    args = graph["args"]
    frame_ids = [int(value) for value in graph["frame_ids"]]
    edge_count = int(args[4].numel())
    edges = [
        summarize_edge_objective_change(args, poses["poses"]["pre"], poses["poses"]["control"], edge, frame_ids)
        for edge in range(edge_count)
    ]
    total = {
        "pre_dynamic_count": sum(e["pre_dynamic_count"] for e in edges),
        "post_dynamic_count": sum(e["post_dynamic_count"] for e in edges),
        "common_count": sum(e["common_count"] for e in edges),
        "gained_count": sum(e["gained_count"] for e in edges),
        "lost_count": sum(e["lost_count"] for e in edges),
        "pre_dynamic_cost": sum(e["pre_dynamic_cost"] for e in edges),
        "post_dynamic_cost": sum(e["post_dynamic_cost"] for e in edges),
        "pre_common_cost": sum(e["pre_common_cost"] for e in edges),
        "post_common_cost": sum(e["post_common_cost"] for e in edges),
    }
    total["dynamic_cost_delta"] = total["post_dynamic_cost"] - total["pre_dynamic_cost"]
    total["common_cost_delta"] = total["post_common_cost"] - total["pre_common_cost"]
    deltas = [e["common_cost_delta"] for e in edges]
    count_changes = [e["post_dynamic_count"] - e["pre_dynamic_count"] for e in edges]
    largest = sorted(edges, key=lambda row: abs(row["common_cost_delta"]), reverse=True)[:10]
    return {
        "id": summary_row["id"],
        "status": "AUDIT_COMPLETE",
        "job": report["job"],
        "graph_sha256": report["graph_sha256"],
        "report_sha256": sha(trial / summary_row["id"] / "report.json"),
        "poses_sha256": sha(trial / summary_row["id"] / "poses.pt"),
        "frame_count": len(frame_ids),
        "directed_edge_count": edge_count,
        "totals": total,
        "edge_common_cost_delta_median": _median(deltas),
        "edge_common_cost_delta_p95_abs": _p95([abs(v) for v in deltas]),
        "edge_dynamic_count_change_median": _median([float(v) for v in count_changes]),
        "edge_dynamic_count_change_max_abs": max((abs(v) for v in count_changes), default=0),
        "edges_with_mask_change": sum(1 for e in edges if e["gained_count"] or e["lost_count"]),
        "largest_abs_common_delta_edges": largest,
    }


def audit_trial(trial: Path = DEFAULT_TRIAL) -> dict[str, Any]:
    import torch

    torch.set_num_threads(1)
    summary = read_json(trial / "summary.json")
    if summary.get("schema") != "native_depth_shape_GN_falsifier_v1":
        raise ValueError("source trial schema mismatch")
    if (summary.get("diagnostic_only") is not True
            or summary.get("external_ground_truth_used") is not False
            or summary.get("production_promoted") is not False):
        raise ValueError("source trial must be source-only diagnostic")
    output: dict[str, Any] = {
        "schema": SCHEMA,
        "trial": str(trial),
        "trial_summary_sha256": sha(trial / "summary.json"),
        "audit_runner_sha256": sha(Path(__file__)),
        "edge_audit_helper_sha256": sha(BASE / "audit_calibrated_graph_edges.py"),
        "source_trial_schema": summary["schema"],
        "source_plan_sha256": summary.get("plan_sha256"),
        "source_runner_sha256": summary.get("runner_sha256"),
        "source_helper_sha256": summary.get("helper_sha256"),
        "diagnostic_only": True,
        "precision_pass": False,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "cost_note": "true Huber(k=1.345) on calibrated residual helper; analysis-only, not native bit-exact GN/IRLS cost",
        "jobs": [],
    }
    for row in summary["jobs"]:
        try:
            output["jobs"].append(audit_job(trial, row))
        except Exception as exc:  # preserve all failures for review
            output["jobs"].append({"id": row.get("id"), "status": "AUDIT_FAILED", "error": str(exc)})
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, default=DEFAULT_TRIAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    report = audit_trial(args.trial)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return 0 if all(job["status"] == "AUDIT_COMPLETE" for job in report["jobs"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
