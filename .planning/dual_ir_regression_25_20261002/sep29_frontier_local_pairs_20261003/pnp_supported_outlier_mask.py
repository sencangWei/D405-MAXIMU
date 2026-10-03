"""Mask-only diagnostic removal of stereo-PnP-supported outlier matches.

This helper is source/CPU only.  It clones native graph args, validates that
each unordered frame pair has exactly two directed edges, applies the original
metric loop gate, and only clears ``args[7]`` entries whose source and target
stereo depths are both available but whose accepted PnP reprojection error is
strictly greater than the original 2 px threshold.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import sys

import torch


BASE = Path(__file__).resolve().parent
TOOL_ROOT = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
for import_root in (BASE, TOOL_ROOT, TOOL_ROOT / "thirdparty/lietorch", TOOL_ROOT / "thirdparty/mast3r"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


def _load_audit():
    path = BASE / "audit_pnp_match_partition.py"
    spec = importlib.util.spec_from_file_location("audit_pnp_match_partition", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(path)
    spec.loader.exec_module(module)
    return module


audit = _load_audit()


def _clone_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(value.clone() if torch.is_tensor(value) else value for value in args)


def _directed_edge_map(args: tuple[Any, ...]) -> dict[tuple[int, int], int]:
    ii = args[4].detach().cpu().reshape(-1).tolist()
    jj = args[5].detach().cpu().reshape(-1).tolist()
    edge_map: dict[tuple[int, int], int] = {}
    for edge_index, (source, target) in enumerate(zip(ii, jj)):
        key = (int(source), int(target))
        if key in edge_map:
            raise ValueError(f"duplicate directed edge {key}")
        edge_map[key] = edge_index
    for source, target in list(edge_map):
        if (target, source) not in edge_map:
            raise ValueError(f"missing reverse edge for {(source, target)}")
    return edge_map


def _frame(graph: dict[str, Any], depths: torch.Tensor, frame_index: int):
    args = graph["args"]
    h, w = int(args[9]), int(args[10])
    depth = depths[frame_index]
    return SimpleNamespace(
        frame_id=int(graph["frame_ids"][frame_index]),
        img=torch.empty(1, 3, h, w),
        metric_depth=depth.reshape(h, w),
        metric_anchor_mask=torch.isfinite(depth.reshape(-1)) & (depth.reshape(-1) > 0),
    )


def _metric_loop_gate(graph: dict[str, Any], depths: torch.Tensor, forward_edge: int, reverse_edge: int):
    from mast3r_slam.global_opt import FactorGraph

    args = graph["args"]
    holder = SimpleNamespace(device="cpu", K=args[3].detach().cpu(), cfg={})
    holder._metric_pnp_direction = lambda *values: FactorGraph._metric_pnp_direction(holder, *values)
    frame_i = _frame(graph, depths, int(args[4][forward_edge]))
    frame_j = _frame(graph, depths, int(args[5][forward_edge]))
    valid_j = (args[7][forward_edge].reshape(-1) & (args[8][forward_edge].reshape(-1) > float(args[16]))).detach().cpu().clone()
    valid_i = (args[7][reverse_edge].reshape(-1) & (args[8][reverse_edge].reshape(-1) > float(args[16]))).detach().cpu().clone()
    return FactorGraph.metric_loop_gate(
        holder,
        frame_i,
        frame_j,
        args[6][forward_edge].detach().cpu(),
        valid_j,
        args[6][reverse_edge].detach().cpu(),
        valid_i,
    )


def _direction_report(
    graph: dict[str, Any],
    depths: torch.Tensor,
    variant_valid: torch.Tensor,
    edge_index: int,
) -> dict[str, Any]:
    args = graph["args"]
    transform, pnp_report = audit._factorgraph_pnp_transform(graph, depths, edge_index)
    if transform is None or not pnp_report.get("accepted"):
        raise ValueError(f"accepted pair has rejected direction {edge_index}: {pnp_report}")
    source = int(args[4][edge_index])
    target = int(args[5][edge_index])
    classes = audit.classify_pnp_reprojection(
        K=args[3],
        source_depth_flat=depths[source],
        target_depth_flat=depths[target],
        target_to_source_index=args[6][edge_index],
        valid_target=args[7][edge_index].reshape(-1),
        transform_source_to_target=transform,
        width=int(args[10]),
    )
    original_valid = args[7][edge_index].reshape(-1).detach().cpu().bool()
    remove = (classes["inconsistent"] & classes["both_depth"]).reshape_as(original_valid)
    edge_valid = variant_valid[edge_index].reshape(-1)
    edge_valid[remove.to(device=edge_valid.device)] = False
    supported_count = int(classes["both_depth"].sum().item())
    removed_count = int(remove.sum().item())
    remaining_valid_count = int(edge_valid.sum().item())
    return {
        "edge_index": int(edge_index),
        "status": "MASKED",
        "source_frame_id": int(graph["frame_ids"][source]),
        "target_frame_id": int(graph["frame_ids"][target]),
        "pnp_report": pnp_report,
        "supported_count": supported_count,
        "removed_count": removed_count,
        "retained_supported_count": supported_count - removed_count,
        "removed_fraction_of_supported": float(removed_count / supported_count) if supported_count else 0.0,
        "original_valid_count": int(original_valid.sum().item()),
        "remaining_valid_count": remaining_valid_count,
    }


def _unchanged_direction_report(graph: dict[str, Any], args: tuple[Any, ...], edge_index: int) -> dict[str, Any]:
    source = int(args[4][edge_index])
    target = int(args[5][edge_index])
    valid_count = int(args[7][edge_index].reshape(-1).sum().item())
    return {
        "edge_index": int(edge_index),
        "status": "PAIR_REJECTED_UNCHANGED",
        "source_frame_id": int(graph["frame_ids"][source]),
        "target_frame_id": int(graph["frame_ids"][target]),
        "supported_count": 0,
        "removed_count": 0,
        "retained_supported_count": 0,
        "removed_fraction_of_supported": 0.0,
        "original_valid_count": valid_count,
        "remaining_valid_count": valid_count,
    }


def filter_supported_outliers(graph: dict[str, Any], depths: torch.Tensor) -> tuple[tuple[Any, ...], dict[str, Any]]:
    args = tuple(graph["args"])
    edge_map = _directed_edge_map(args)
    variant_args = _clone_args(args)
    variant_valid = variant_args[7]
    report: dict[str, Any] = {
        "schema": "pnp_supported_outlier_mask_v1",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "accepted_pair_count": 0,
        "rejected_pair_count": 0,
        "total_removed_count": 0,
        "pairs": [],
    }
    visited: set[tuple[int, int]] = set()
    for source, target in sorted(edge_map):
        unordered = tuple(sorted((source, target)))
        if unordered in visited:
            continue
        visited.add(unordered)
        forward = edge_map[(unordered[0], unordered[1])]
        reverse = edge_map[(unordered[1], unordered[0])]
        accepted, gate_report = _metric_loop_gate(graph, depths, forward, reverse)
        pair_report: dict[str, Any] = {
            "source_index": int(unordered[0]),
            "target_index": int(unordered[1]),
            "source_frame_id": int(graph["frame_ids"][unordered[0]]),
            "target_frame_id": int(graph["frame_ids"][unordered[1]]),
            "accepted": bool(accepted),
            "gate_report": gate_report,
            "directions": [],
        }
        if not accepted:
            pair_report["status"] = "PAIR_REJECTED"
            pair_report["directions"] = [
                _unchanged_direction_report(graph, args, forward),
                _unchanged_direction_report(graph, args, reverse),
            ]
            report["rejected_pair_count"] += 1
            report["pairs"].append(pair_report)
            continue
        pair_report["status"] = "PAIR_ACCEPTED"
        report["accepted_pair_count"] += 1
        for edge_index in (forward, reverse):
            direction = _direction_report(graph, depths, variant_valid, edge_index)
            pair_report["directions"].append(direction)
            report["total_removed_count"] += int(direction.get("removed_count", 0))
        report["pairs"].append(pair_report)
    return variant_args, report
