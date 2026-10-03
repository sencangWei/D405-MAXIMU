"""CPU-only calibrated MASt3R graph edge residual auditor.

Diagnostic helper only: mirrors the residual/mask side of
``mast3r_slam/backend/src/gn_kernels.cu`` calibrated projection without
solving, mutating inputs, importing GT, or approximating the native Hessian.
"""
from __future__ import annotations

from typing import Any


HUBER_K = 1.345


def _as_cpu(torch: Any, name: str, value: Any) -> Any:
    if not torch.is_tensor(value):
        value = torch.as_tensor(value)
    out = value.detach().cpu()
    if not bool(torch.isfinite(out.float()).all().item()):
        raise ValueError(f"{name} contains non-finite values")
    return out


def _pose_rows(torch: Any, poses: Any) -> Any:
    poses = _as_cpu(torch, "poses", poses).to(dtype=torch.float64)
    if poses.ndim != 2 or poses.shape[1] != 8:
        raise ValueError("poses must have shape [N, 8]")
    if not bool((poses[:, 7] > 0).all().item()):
        raise ValueError("Sim3 scales must be positive")
    quat_norm = torch.linalg.vector_norm(poses[:, 3:7], dim=1)
    if float(torch.max(torch.abs(quat_norm - 1.0))) > 1e-3:
        raise ValueError("pose quaternions must be unit length")
    return poses


def _quat_inv(q):
    out = q.clone()
    out[:3] *= -1.0
    return out


def _quat_comp(torch, qi, qj):
    return torch.stack((
        qi[3] * qj[0] + qi[0] * qj[3] + qi[1] * qj[2] - qi[2] * qj[1],
        qi[3] * qj[1] - qi[0] * qj[2] + qi[1] * qj[3] + qi[2] * qj[0],
        qi[3] * qj[2] + qi[0] * qj[1] - qi[1] * qj[0] + qi[2] * qj[3],
        qi[3] * qj[3] - qi[0] * qj[0] - qi[1] * qj[1] - qi[2] * qj[2],
    ))


def _act_so3(torch, q, x):
    uv = 2.0 * torch.cross(q[:3].expand_as(x), x, dim=-1)
    return x + q[3] * uv + torch.cross(q[:3].expand_as(x), uv, dim=-1)


def _rel_sim3(torch, pose_i, pose_j):
    ti, qi, si = pose_i[:3], pose_i[3:7], pose_i[7]
    tj, qj, sj = pose_j[:3], pose_j[3:7], pose_j[7]
    qi_inv = _quat_inv(qi)
    sij = sj / si
    qij = _quat_comp(torch, qi_inv, qj)
    tij = _act_so3(torch, qi_inv, (tj - ti).reshape(1, 3))[0] / si
    return tij, qij, sij


def _act_sim3(torch, t, q, s, x):
    return _act_so3(torch, q, x) * s + t


def _huber_weight(torch, r):
    abs_r = torch.abs(r)
    return torch.where(abs_r < HUBER_K, torch.ones_like(abs_r), torch.full_like(abs_r, HUBER_K) / abs_r)


def _huber_rho(torch, r):
    abs_r = torch.abs(r)
    return torch.where(abs_r <= HUBER_K, 0.5 * r * r, HUBER_K * (abs_r - 0.5 * HUBER_K))


def calibrated_edge_residuals(args: tuple[Any, ...], edge_index: int, *, poses: Any | None = None) -> dict[str, Any]:
    """Return CPU tensors for one directed calibrated edge.

    ``args`` must be the exact 19-argument tuple passed to native
    ``gauss_newton_calib``.  Residual columns are ``u_px, v_px, log_depth``.
    ``whitened`` is residual times ``sqrt(Q)/sigma`` with invalid points zeroed.
    """
    if len(args) != 19:
        raise ValueError("expected exact 19 calibrated GN args")
    import torch

    Twc, Xs, Cs, K, ii, jj, idx, valid_match, Q = args[:9]
    height, width, pixel_border, z_eps, sigma_pixel, sigma_depth, C_thresh, Q_thresh = args[9:17]
    edge_index = int(edge_index)
    Twc = _pose_rows(torch, Twc if poses is None else poses)
    Xs = _as_cpu(torch, "Xs", Xs).to(dtype=torch.float64)
    Cs = _as_cpu(torch, "Cs", Cs).to(dtype=torch.float64)
    K = _as_cpu(torch, "K", K).to(dtype=torch.float64)
    ii = _as_cpu(torch, "ii", ii).to(dtype=torch.long)
    jj = _as_cpu(torch, "jj", jj).to(dtype=torch.long)
    idx = _as_cpu(torch, "idx_ii2jj", idx).to(dtype=torch.long)
    valid_match = _as_cpu(torch, "valid_match", valid_match).to(dtype=torch.bool)
    Q = _as_cpu(torch, "Q", Q).to(dtype=torch.float64)

    if edge_index < 0 or edge_index >= int(ii.numel()):
        raise ValueError("edge_index out of bounds")
    if Xs.ndim != 3 or Xs.shape[-1] != 3 or Cs.shape[:2] != Xs.shape[:2] or Cs.shape[-1] != 1:
        raise ValueError("Xs/Cs must have shapes [N, P, 3] and [N, P, 1]")
    if K.shape != (3, 3):
        raise ValueError("K must have shape [3, 3]")
    if idx.shape[:2] != Q.shape[:2] or valid_match.shape[:2] != Q.shape[:2] or Q.shape[-1] != 1:
        raise ValueError("idx/valid_match/Q must share [E, P] shape")
    ix, jx = int(ii[edge_index]), int(jj[edge_index])
    if ix < 0 or jx < 0 or ix >= Twc.shape[0] or jx >= Twc.shape[0]:
        raise ValueError("edge references pose out of bounds")

    width_i = int(width)
    height_i = int(height)
    point_count = int(Xs.shape[1])
    match = valid_match[edge_index, :, 0]
    ind_xi = torch.where(match, idx[edge_index], torch.zeros_like(idx[edge_index]))
    if bool(((ind_xi[match] < 0) | (ind_xi[match] >= point_count)).any().item()):
        raise ValueError("matched source point index out of bounds")

    Xi = Xs[ix, ind_xi]
    Xj = Xs[jx, torch.arange(point_count)]
    tij, qij, sij = _rel_sim3(torch, Twc[ix], Twc[jx])
    Xj_ci = _act_sim3(torch, tij, qij, sij, Xj)

    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    valid_z = (Xj_ci[:, 2] > float(z_eps)) & (Xi[:, 2] > float(z_eps))
    zj_inv = torch.where(valid_z, 1.0 / Xj_ci[:, 2], torch.zeros_like(Xj_ci[:, 2]))
    zj_log = torch.where(valid_z, torch.log(Xj_ci[:, 2]), torch.zeros_like(Xj_ci[:, 2]))
    zi_log = torch.where(valid_z, torch.log(Xi[:, 2]), torch.zeros_like(Xi[:, 2]))
    u = fx * Xj_ci[:, 0] * zj_inv + cx
    v = fy * Xj_ci[:, 1] * zj_inv + cy
    u_target = (ind_xi % width_i).to(dtype=torch.float64)
    v_target = torch.div(ind_xi, width_i, rounding_mode="floor").to(dtype=torch.float64)
    residual = torch.stack((u - u_target, v - v_target, zj_log - zi_log), dim=1)

    valid_u = (u > int(pixel_border)) & (u < width_i - 1 - int(pixel_border))
    valid_v = (v > int(pixel_border)) & (v < height_i - 1 - int(pixel_border))
    q = Q[edge_index, :, 0]
    ci = Cs[ix, ind_xi, 0]
    cj = Cs[jx, :, 0]
    mask = match & (q > float(Q_thresh)) & (ci > float(C_thresh)) & (cj > float(C_thresh)) & valid_u & valid_v & valid_z

    sqrt_q = torch.sqrt(torch.clamp(q, min=0.0))
    scales = torch.stack((
        sqrt_q / float(sigma_pixel),
        sqrt_q / float(sigma_pixel),
        sqrt_q / float(sigma_depth),
    ), dim=1)
    whitened = torch.where(mask[:, None], residual * scales, torch.zeros_like(residual))
    return {
        "mask": mask,
        "residual": residual,
        "whitened": whitened,
        "huber_weight": _huber_weight(torch, whitened),
        "huber_rho_analysis": _huber_rho(torch, whitened),
        "projected_uv": torch.stack((u, v), dim=1),
        "target_uv": torch.stack((u_target, v_target), dim=1),
        "source_indices": ind_xi,
        "native_dynamic_mask_count": int(mask.sum().item()),
    }


def _stats(torch, residual, whitened):
    if residual.numel() == 0:
        return {
            "count": 0,
            "pixel_median": None,
            "pixel_p95": None,
            "logdepth_median": None,
            "logdepth_p95": None,
            "huber_rho_sum_analysis": 0.0,
        }
    pixel = torch.linalg.vector_norm(residual[:, :2], dim=1)
    logdepth = torch.abs(residual[:, 2])
    rho = _huber_rho(torch, whitened).sum(dim=1)
    return {
        "count": int(residual.shape[0]),
        "pixel_median": float(torch.median(pixel).item()),
        "pixel_p95": float(torch.quantile(pixel, 0.95).item()),
        "logdepth_median": float(torch.median(logdepth).item()),
        "logdepth_p95": float(torch.quantile(logdepth, 0.95).item()),
        "huber_rho_sum_analysis": float(rho.sum().item()),
    }


def summarize_edge_update(pre_args: tuple[Any, ...], post_args: tuple[Any, ...], edge_index: int) -> dict[str, Any]:
    """Compare pre/post residuals under the same common valid point mask."""
    import torch

    pre = calibrated_edge_residuals(pre_args, edge_index)
    post = calibrated_edge_residuals(post_args, edge_index)
    common = pre["mask"] & post["mask"]
    return {
        "edge_index": int(edge_index),
        "native_dynamic_mask_counts": {
            "pre": pre["native_dynamic_mask_count"],
            "post": post["native_dynamic_mask_count"],
            "common": int(common.sum().item()),
        },
        "common_mask": common,
        "pre_common": _stats(torch, pre["residual"][common], pre["whitened"][common]),
        "post_common": _stats(torch, post["residual"][common], post["whitened"][common]),
        "note": "Huber rho is analysis-only true Huber(k=1.345), not native bit-exact GN cost or IRLS surrogate.",
    }
