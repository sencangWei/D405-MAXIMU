"""Split calibrated native edge residuals by stereo-depth support.

Diagnostic-only CPU helper.  It reuses ``audit_calibrated_graph_edges`` for the
native calibrated residual/mask and then classifies the common pre/post valid
points by whether both corresponding source and target pixels have stereo depth
support.  It does not change masks, gates, weights, graph inputs, or solver
state.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


SCHEMA = "audit_stereo_supported_native_edges_v1"


def _load_edge_audit():
    path = Path(__file__).with_name("audit_calibrated_graph_edges.py")
    spec = importlib.util.spec_from_file_location("audit_calibrated_graph_edges", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"cannot load {path}")
    spec.loader.exec_module(module)
    return module


_edge_audit = _load_edge_audit()


def _as_stereo_mask(torch: Any, stereo_depth_valid: Any, frame_count: int, point_count: int) -> Any:
    mask = stereo_depth_valid.detach().cpu() if torch.is_tensor(stereo_depth_valid) else torch.as_tensor(stereo_depth_valid)
    if tuple(mask.shape) != (frame_count, point_count):
        raise ValueError("stereo_depth_valid must have shape [N, P]")
    if mask.dtype == torch.bool:
        return mask
    if not bool(torch.isfinite(mask.float()).all().item()):
        raise ValueError("stereo_depth_valid contains non-finite values")
    return mask > 0


def _quantile_or_none(torch: Any, values: Any, q: float) -> float | None:
    if values.numel() == 0:
        return None
    return float(torch.quantile(values.to(dtype=torch.float64), q).item())


def _median_or_none(torch: Any, values: Any) -> float | None:
    if values.numel() == 0:
        return None
    return float(torch.median(values.to(dtype=torch.float64)).item())


def _split_stats(torch: Any, q_values: Any, residual: Any, whitened: Any) -> dict[str, Any]:
    count = int(residual.shape[0])
    if count == 0:
        return {
            "count": 0,
            "q_median": None,
            "q_p95": None,
            "pixel_median": None,
            "pixel_p95": None,
            "logdepth_median": None,
            "logdepth_p95": None,
            "huber_rho_sum_analysis": 0.0,
        }
    pixel = torch.linalg.vector_norm(residual[:, :2], dim=1)
    logdepth = torch.abs(residual[:, 2])
    rho = _edge_audit._huber_rho(torch, whitened).sum(dim=1)
    return {
        "count": count,
        "q_median": _median_or_none(torch, q_values),
        "q_p95": _quantile_or_none(torch, q_values, 0.95),
        "pixel_median": _median_or_none(torch, pixel),
        "pixel_p95": _quantile_or_none(torch, pixel, 0.95),
        "logdepth_median": _median_or_none(torch, logdepth),
        "logdepth_p95": _quantile_or_none(torch, logdepth, 0.95),
        "huber_rho_sum_analysis": float(rho.sum().item()),
    }


def summarize_stereo_supported_edge(
    pre_args: tuple[Any, ...],
    post_args: tuple[Any, ...],
    edge_index: int,
    stereo_depth_valid: Any,
) -> dict[str, Any]:
    """Summarize one directed edge split by both-eye stereo-depth support.

    ``stereo_depth_valid`` is indexed as ``[frame, native_pixel_index]``.  For a
    directed edge i->j, source support is looked up with the native
    ``source_indices`` from ``audit_calibrated_graph_edges`` and target support
    uses the target-frame pixel order ``arange(P)``.
    """
    import torch

    pre = _edge_audit.calibrated_edge_residuals(pre_args, edge_index)
    post = _edge_audit.calibrated_edge_residuals(post_args, edge_index)
    common = pre["mask"] & post["mask"]
    point_count = int(common.numel())
    frame_count = int(pre_args[1].shape[0])
    stereo = _as_stereo_mask(torch, stereo_depth_valid, frame_count, point_count)

    ii = pre_args[4].detach().cpu() if torch.is_tensor(pre_args[4]) else torch.as_tensor(pre_args[4])
    jj = pre_args[5].detach().cpu() if torch.is_tensor(pre_args[5]) else torch.as_tensor(pre_args[5])
    edge_index = int(edge_index)
    if edge_index < 0 or edge_index >= int(ii.numel()) or edge_index >= int(jj.numel()):
        raise ValueError("edge_index out of bounds")
    source_frame = int(ii[edge_index])
    target_frame = int(jj[edge_index])
    if source_frame < 0 or target_frame < 0 or source_frame >= frame_count or target_frame >= frame_count:
        raise ValueError("edge frame index out of bounds")

    source_indices = pre["source_indices"].to(dtype=torch.long)
    target_indices = torch.arange(point_count, dtype=torch.long)
    if bool(((source_indices[common] < 0) | (source_indices[common] >= point_count)).any().item()):
        raise ValueError("common source index out of bounds")

    supported = common & stereo[source_frame, source_indices] & stereo[target_frame, target_indices]
    unsupported = common & ~supported
    q = pre_args[8].detach().cpu() if torch.is_tensor(pre_args[8]) else torch.as_tensor(pre_args[8])
    q_values = q[edge_index, :, 0].to(dtype=torch.float64)

    return {
        "schema": SCHEMA,
        "edge_index": edge_index,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "native_common_count": int(common.sum().item()),
        "stereo_supported_count": int(supported.sum().item()),
        "stereo_unsupported_count": int(unsupported.sum().item()),
        "supported_mask": supported,
        "unsupported_mask": unsupported,
        "supported": {
            "pre": _split_stats(torch, q_values[supported], pre["residual"][supported], pre["whitened"][supported]),
            "post": _split_stats(torch, q_values[supported], post["residual"][supported], post["whitened"][supported]),
        },
        "unsupported": {
            "pre": _split_stats(torch, q_values[unsupported], pre["residual"][unsupported], pre["whitened"][unsupported]),
            "post": _split_stats(torch, q_values[unsupported], post["residual"][unsupported], post["whitened"][unsupported]),
        },
        "note": "Split is diagnostic-only under the unchanged native common pre/post mask.",
    }
