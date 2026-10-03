"""Partition native residuals by accepted stereo-PnP reprojection consistency.

CPU/source-only diagnostic.  It reuses frozen graph/report/pose bindings from
``depth_shape_GN_falsifier_v1`` and the existing source depth/PnP helper.  It
does not delete points, change weights, solve, score, or use GT.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import torch


BASE = Path(__file__).resolve().parent
DEFAULT_TRIAL = BASE / "depth_shape_GN_falsifier_v1"
DEFAULT_OUTPUT = BASE / "pnp_match_partition_v2.json"
SCHEMA = "pnp_match_partition_v2"
PNP_THRESHOLD_PX = 2.0
TOOL_ROOT = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
for import_root in (TOOL_ROOT, TOOL_ROOT / "thirdparty/lietorch", TOOL_ROOT / "thirdparty/mast3r"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


def _load_sibling(name: str):
    path = BASE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(path)
    sys.path.insert(0, str(BASE))
    spec.loader.exec_module(module)
    return module


edge_audit = _load_sibling("audit_calibrated_graph_edges")
objective_audit = _load_sibling("audit_native_objective_change")
depth_probe = _load_sibling("probe_stereo_depth_shape_native_graph")


def sha(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def classify_pnp_reprojection(
    *,
    K: torch.Tensor,
    source_depth_flat: torch.Tensor,
    target_depth_flat: torch.Tensor,
    target_to_source_index: torch.Tensor,
    valid_target: torch.Tensor,
    transform_source_to_target: np.ndarray,
    width: int,
    threshold_px: float = PNP_THRESHOLD_PX,
) -> dict[str, torch.Tensor]:
    """Classify target pixels by source-stereo PnP reprojection error."""
    pixel_count = int(target_to_source_index.numel())
    source_index = target_to_source_index.detach().cpu().reshape(-1).long()
    valid = valid_target.detach().cpu().reshape(-1).bool().clone()
    if source_depth_flat.numel() < pixel_count or target_depth_flat.numel() < pixel_count:
        raise ValueError("depth arrays shorter than native point count")
    valid &= source_index >= 0
    valid &= source_index < source_depth_flat.numel()
    safe_source = source_index.clamp(0, source_depth_flat.numel() - 1)
    source_depth = source_depth_flat.detach().cpu().reshape(-1)[safe_source]
    target_depth = target_depth_flat.detach().cpu().reshape(-1)[:pixel_count]
    both_depth = valid & torch.isfinite(source_depth) & (source_depth > 0) & torch.isfinite(target_depth) & (target_depth > 0)

    errors = torch.full((pixel_count,), float("nan"), dtype=torch.float64)
    if int(both_depth.sum()) > 0:
        k_cpu = K.detach().cpu().to(dtype=torch.float64)
        fx, fy, cx, cy = k_cpu[0, 0], k_cpu[1, 1], k_cpu[0, 2], k_cpu[1, 2]
        target_pixels = torch.arange(pixel_count, dtype=torch.long)
        su = (safe_source[both_depth] % width).to(dtype=torch.float64)
        sv = torch.div(safe_source[both_depth], width, rounding_mode="floor").to(dtype=torch.float64)
        z = source_depth[both_depth].to(dtype=torch.float64)
        xyz_source = torch.stack(((su - cx) * z / fx, (sv - cy) * z / fy, z), dim=1)
        transform = torch.as_tensor(transform_source_to_target, dtype=torch.float64)
        xyz_target = xyz_source @ transform[:3, :3].T + transform[:3, 3]
        positive = xyz_target[:, 2] > 0
        projected = torch.empty((xyz_target.shape[0], 2), dtype=torch.float64)
        projected[:, 0] = fx * xyz_target[:, 0] / xyz_target[:, 2] + cx
        projected[:, 1] = fy * xyz_target[:, 1] / xyz_target[:, 2] + cy
        target_u = (target_pixels[both_depth] % width).to(dtype=torch.float64)
        target_v = torch.div(target_pixels[both_depth], width, rounding_mode="floor").to(dtype=torch.float64)
        err = torch.linalg.vector_norm(projected - torch.stack((target_u, target_v), dim=1), dim=1)
        err = torch.where(positive, err, torch.full_like(err, float("inf")))
        errors[both_depth] = err
    consistent = both_depth & (errors <= float(threshold_px))
    inconsistent = both_depth & (errors > float(threshold_px))
    depth_unsupported = valid & ~both_depth
    return {
        "consistent": consistent,
        "inconsistent": inconsistent,
        "depth_unsupported": depth_unsupported,
        "both_depth": both_depth,
        "reprojection_error_px": errors,
    }


def _stats(residual: torch.Tensor, whitened: torch.Tensor, q: torch.Tensor, mask: torch.Tensor) -> dict[str, Any]:
    count = int(mask.sum().item())
    if count == 0:
        return {"count": 0, "q_median": None, "pixel_median": None, "cost_sum": 0.0}
    pixel = torch.linalg.vector_norm(residual[mask, :2], dim=1)
    cost = edge_audit._huber_rho(torch, whitened[mask]).sum(dim=1)
    return {
        "count": count,
        "q_median": float(torch.median(q[mask].to(dtype=torch.float64)).item()),
        "pixel_median": float(torch.median(pixel.to(dtype=torch.float64)).item()),
        "cost_sum": float(cost.sum().item()),
    }


def partition_edge(
    args: tuple[Any, ...],
    pre_poses: torch.Tensor,
    post_poses: torch.Tensor,
    edge_index: int,
    pnp_classes: dict[str, torch.Tensor],
) -> dict[str, Any]:
    pre_args = objective_audit.compact_edge_args(torch, args, edge_index, pre_poses)
    post_args = objective_audit.compact_edge_args(torch, args, edge_index, post_poses)
    pre = edge_audit.calibrated_edge_residuals(pre_args, 0)
    post = edge_audit.calibrated_edge_residuals(post_args, 0)
    common = pre["mask"] & post["mask"]
    q = pre_args[8][0, :, 0].detach().cpu().to(dtype=torch.float64)
    out = {"edge_index": int(edge_index), "common_count": int(common.sum().item()), "classes": {}}
    assigned = torch.zeros_like(common)
    for name in ("consistent", "inconsistent", "depth_unsupported"):
        mask = common & pnp_classes[name]
        assigned |= mask
        out["classes"][name] = {
            "pre": _stats(pre["residual"], pre["whitened"], q, mask),
            "post": _stats(post["residual"], post["whitened"], q, mask),
        }
    unsupported = common & ~assigned
    out["classes"]["pnp_unclassified"] = {
        "pre": _stats(pre["residual"], pre["whitened"], q, unsupported),
        "post": _stats(post["residual"], post["whitened"], q, unsupported),
    }
    return out


def _validate_frozen_job(trial: Path, row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return objective_audit._validate_frozen_job(trial, row)


def _validate_source_trial(summary: dict[str, Any]) -> None:
    if summary.get("schema") != "native_depth_shape_GN_falsifier_v1":
        raise ValueError("unexpected source trial schema")
    if summary.get("diagnostic_only") is not True:
        raise ValueError("source trial is not diagnostic-only")
    if summary.get("external_ground_truth_used") is not False:
        raise ValueError("source trial used external GT")
    if summary.get("production_promoted") is not False:
        raise ValueError("source trial was production-promoted")
    if summary.get("precision_pass") is not False:
        raise ValueError("source trial precision_pass flag must remain false")


def _status_from_pairs(pairs: list[dict[str, Any]]) -> str:
    return "AUDIT_COMPLETE" if all(row.get("status") == "PARTITION_COMPLETE" for row in pairs) else "WITH_FAILURES"


def _select_pairs(report: dict[str, Any]) -> list[dict[str, int | str]]:
    accepted = [
        r for r in report.get("chain_pnp", [])
        if r.get("accepted") and "rotation_disagreement_deg" in r and "control" in r["rotation_disagreement_deg"]
    ]
    accepted.sort(key=lambda r: float(r["rotation_disagreement_deg"]["control"]), reverse=True)
    pairs = [
        {"first_frame_id": int(r["first_frame_id"]), "second_frame_id": int(r["second_frame_id"]), "reason": "top_control_rotation"}
        for r in accepted[:2]
    ]
    if report.get("job", {}).get("id") == "right587":
        extra = {"first_frame_id": 541, "second_frame_id": 587, "reason": "explicit_right587_retrieval"}
        if extra not in pairs:
            pairs.append(extra)
    return pairs


def _edge_index_by_frame_ids(graph: dict[str, Any], first_frame_id: int, second_frame_id: int) -> int | None:
    frame_ids = [int(v) for v in graph["frame_ids"]]
    if first_frame_id not in frame_ids or second_frame_id not in frame_ids:
        return None
    source = frame_ids.index(first_frame_id)
    target = frame_ids.index(second_frame_id)
    ii, jj = graph["args"][4].detach().cpu(), graph["args"][5].detach().cpu()
    matches = torch.where((ii == source) & (jj == target))[0]
    return int(matches[0]) if int(matches.numel()) else None


def _direct_pnp_transform(graph: dict[str, Any], depths: torch.Tensor, edge_index: int):
    from mast3r_slam.stereo_depth import solve_metric_keyframe_pnp

    args = graph["args"]
    source = int(args[4][edge_index])
    target = int(args[5][edge_index])
    h, w = int(args[9]), int(args[10])
    source_depth = depths[source].reshape(-1)
    target_depth = depths[target].reshape(-1)
    source_mask = torch.isfinite(source_depth) & (source_depth > 0)
    target_mask = torch.isfinite(target_depth) & (target_depth > 0)
    pixel_count = int(args[6][edge_index].numel())
    current_indices = torch.arange(pixel_count)
    source_indices = args[6][edge_index].reshape(-1).long().cpu()
    valid = (args[7][edge_index].reshape(-1) & (args[8][edge_index].reshape(-1) > float(args[16]))).cpu()
    valid &= source_indices >= 0
    valid &= source_indices < source_mask.numel()
    valid &= target_mask[:pixel_count]
    safe_source = source_indices.clamp(0, source_mask.numel() - 1)
    valid &= source_mask[safe_source]
    candidates = current_indices[valid]
    maximum_points = 5000
    if candidates.numel() > maximum_points:
        sample = torch.linspace(0, candidates.numel() - 1, maximum_points).long()
        candidates = candidates[sample]
    if candidates.numel() == 0:
        return None, {"accepted": False, "reason": "no_metric_correspondences"}
    source_indices = safe_source[candidates]
    z = source_depth[source_indices]
    finite = torch.isfinite(z) & (z > 0)
    candidates = candidates[finite]
    source_indices = source_indices[finite]
    z = z[finite]
    if candidates.numel() == 0:
        return None, {"accepted": False, "reason": "no_finite_metric_depth"}
    K = args[3].detach().cpu()
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    source_u = (source_indices % w).to(dtype=z.dtype)
    source_v = torch.div(source_indices, w, rounding_mode="floor").to(dtype=z.dtype)
    object_points = torch.stack(((source_u - cx) * z / fx, (source_v - cy) * z / fy, z), dim=-1)
    image_points = torch.stack((candidates % w, torch.div(candidates, w, rounding_mode="floor")), dim=-1).float()
    transform, pnp_report = solve_metric_keyframe_pnp(
        object_points.detach().cpu().numpy(),
        image_points.detach().cpu().numpy(),
        K.numpy(),
        minimum_points=100,
        minimum_inlier_ratio=0.5,
        reprojection_error_px=2.0,
    )
    if not pnp_report.get("accepted"):
        return None, pnp_report
    if float(pnp_report["reprojection_p95_px"]) > 4.0:
        pnp_report["accepted"] = False
        pnp_report["reason"] = "metric_pnp_reprojection_p95_high"
        return None, pnp_report
    return transform, pnp_report


def _factorgraph_pnp_transform(graph: dict[str, Any], depths: torch.Tensor, edge_index: int):
    from mast3r_slam.global_opt import FactorGraph

    args = graph["args"]
    source = int(args[4][edge_index])
    target = int(args[5][edge_index])
    h, w = int(args[9]), int(args[10])
    holder = SimpleNamespace(
        device="cpu",
        K=args[3].detach().cpu(),
        cfg={
            "metric_loop_pnp_max_points": 5000,
            "metric_loop_pnp_min_points": 100,
            "metric_loop_pnp_min_inlier_ratio": 0.5,
            "metric_loop_pnp_reprojection_error_px": 2.0,
            "metric_loop_pnp_max_reprojection_p95_px": 4.0,
        },
    )
    source_frame = SimpleNamespace(
        frame_id=int(graph["frame_ids"][source]),
        img=torch.empty(1, 3, h, w),
        metric_depth=depths[source].reshape(h, w),
        metric_anchor_mask=torch.isfinite(depths[source].reshape(-1)) & (depths[source].reshape(-1) > 0),
    )
    target_frame = SimpleNamespace(
        frame_id=int(graph["frame_ids"][target]),
        img=torch.empty(1, 3, h, w),
        metric_depth=depths[target].reshape(h, w),
        metric_anchor_mask=torch.isfinite(depths[target].reshape(-1)) & (depths[target].reshape(-1) > 0),
    )
    valid = (args[7][edge_index].reshape(-1) & (args[8][edge_index].reshape(-1) > float(args[16]))).detach().cpu().clone()
    return FactorGraph._metric_pnp_direction(holder, source_frame, target_frame, args[6][edge_index].detach().cpu(), valid)


def _compare_factorgraph_equivalence(
    graph: dict[str, Any],
    depths: torch.Tensor,
    edge_index: int,
    direct_transform: np.ndarray | None,
    direct_report: dict[str, Any],
) -> dict[str, Any]:
    try:
        fg_transform, fg_report = _factorgraph_pnp_transform(graph, depths, edge_index)
    except Exception as exc:  # dependency may be absent outside the native venv
        return {"checked": False, "reason": f"factorgraph_unavailable:{type(exc).__name__}:{exc}"}
    if bool(fg_report.get("accepted")) != bool(direct_report.get("accepted")):
        raise ValueError("direct PnP accepted flag differs from FactorGraph")
    if fg_transform is None or direct_transform is None:
        if fg_transform is not direct_transform:
            raise ValueError("direct PnP transform None state differs from FactorGraph")
        return {"checked": True, "accepted": False}
    max_abs = float(np.max(np.abs(np.asarray(fg_transform) - np.asarray(direct_transform))))
    if max_abs > 1e-9:
        raise ValueError(f"direct PnP transform differs from FactorGraph: {max_abs}")
    return {"checked": True, "accepted": True, "max_abs_transform_delta": max_abs}


def _accepted_pnp_transform(graph: dict[str, Any], depths: torch.Tensor, edge_index: int):
    transform, pnp_report = _direct_pnp_transform(graph, depths, edge_index)
    pnp_report = dict(pnp_report)
    pnp_report["factorgraph_equivalence"] = _compare_factorgraph_equivalence(
        graph, depths, edge_index, transform, pnp_report
    )
    return transform, pnp_report


def _directed_pairs(pair: dict[str, int | str]) -> list[dict[str, int | str]]:
    first, second = int(pair["first_frame_id"]), int(pair["second_frame_id"])
    return [
        {**pair, "first_frame_id": first, "second_frame_id": second, "direction": "forward"},
        {**pair, "first_frame_id": second, "second_frame_id": first, "direction": "reverse"},
    ]


def audit_job(trial: Path, row: dict[str, Any]) -> dict[str, Any]:
    report, poses = _validate_frozen_job(trial, row)
    depth_probe.validate_job_bindings(report["job"])
    graph = torch.load(report["job"]["graph"], map_location="cpu", weights_only=True)
    depths, images, metadata = depth_probe.load_depths(report["job"], graph)
    if images != report.get("images"):
        raise ValueError(f"{row['id']}: source image bindings differ from frozen report")
    if metadata != report.get("stereo_source"):
        raise ValueError(f"{row['id']}: stereo metadata differs from frozen report")
    out = {
        "id": row["id"],
        "status": "AUDIT_COMPLETE",
        "job": report["job"],
        "graph_sha256": report["graph_sha256"],
        "report_sha256": sha(trial / row["id"] / "report.json"),
        "poses_sha256": sha(trial / row["id"] / "poses.pt"),
        "pairs": [],
    }
    for pair in _select_pairs(report):
        for directed in _directed_pairs(pair):
            edge_index = _edge_index_by_frame_ids(graph, int(directed["first_frame_id"]), int(directed["second_frame_id"]))
            pair_out = dict(directed)
            if edge_index is None:
                pair_out.update({"status": "PNP_PAIR_MISSING_EDGE"})
                out["pairs"].append(pair_out)
                continue
            transform, pnp_report = _accepted_pnp_transform(graph, depths, edge_index)
            pair_out["edge_index"] = edge_index
            pair_out["pnp_report"] = pnp_report
            if transform is None or not pnp_report.get("accepted"):
                pair_out["status"] = "PNP_REJECTED"
                out["pairs"].append(pair_out)
                continue
            args = graph["args"]
            source = int(args[4][edge_index])
            target = int(args[5][edge_index])
            classes = classify_pnp_reprojection(
                K=args[3],
                source_depth_flat=depths[source],
                target_depth_flat=depths[target],
                target_to_source_index=args[6][edge_index],
                valid_target=args[7][edge_index].reshape(-1),
                transform_source_to_target=transform,
                width=int(args[10]),
            )
            pair_out["status"] = "PARTITION_COMPLETE"
            pair_out["partition"] = partition_edge(
                args,
                poses["poses"]["pre"],
                poses["poses"]["control"],
                edge_index,
                classes,
            )
            pair_out["class_counts_raw"] = {k: int(v.sum().item()) for k, v in classes.items() if k != "reprojection_error_px"}
            out["pairs"].append(pair_out)
    out["status"] = _status_from_pairs(out["pairs"])
    return out


def audit_trial(trial: Path = DEFAULT_TRIAL) -> dict[str, Any]:
    torch.set_num_threads(1)
    summary = read_json(trial / "summary.json")
    _validate_source_trial(summary)
    audit_script = Path(__file__)
    pnp_solver = TOOL_ROOT / "mast3r_slam/stereo_depth.py"
    result = {
        "schema": SCHEMA,
        "trial": str(trial),
        "trial_summary_sha256": sha(trial / "summary.json"),
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "precision_pass": False,
        "classification_threshold_px": PNP_THRESHOLD_PX,
        "note": "Consistent/inconsistent are direct PnP reprojection classes, not RANSAC membership or causality proof.",
        "source_hashes": {
            "audit_pnp_match_partition.py": sha(audit_script),
            "audit_calibrated_graph_edges.py": sha(BASE / "audit_calibrated_graph_edges.py"),
            "audit_native_objective_change.py": sha(BASE / "audit_native_objective_change.py"),
            "probe_stereo_depth_shape_native_graph.py": sha(BASE / "probe_stereo_depth_shape_native_graph.py"),
            "native_stereo_depth.py": sha(pnp_solver),
        },
        "jobs": [],
    }
    for row in summary["jobs"]:
        try:
            result["jobs"].append(audit_job(trial, row))
        except Exception as exc:
            result["jobs"].append({"id": row.get("id"), "status": "AUDIT_FAILED", "error": str(exc)})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, default=DEFAULT_TRIAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    result = audit_trial(args.trial)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return 0 if all(job["status"] == "AUDIT_COMPLETE" for job in result["jobs"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
