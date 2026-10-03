"""CPU-only local MASt3R tracking metric-factor diagnostic.

This wraps one FrameTracker instance and one opt_pose_calib_sim3 call.  It is a
diagnostic candidate only: no GT, no model inference, no production integration.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import MethodType
from typing import Any

import numpy as np
import torch


BASE = Path(__file__).resolve().parent
TOOL = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
for root in (BASE, TOOL):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from metric_relative_pose_factor import factor_linearization  # noqa: E402


def _native_helpers():
    from mast3r_slam.nonlinear_optimizer import huber
    from mast3r_slam.stereo_depth import embed_pose_increment, pose_optimization_jacobian

    return huber, embed_pose_increment, pose_optimization_jacobian


def _check_cpu_tensor(name: str, value: Any) -> None:
    if torch.is_tensor(value) and value.device.type != "cpu":
        raise ValueError(f"{name} must be CPU for this diagnostic")


def _check_cpu_inputs(**kwargs: Any) -> None:
    for name, value in kwargs.items():
        if hasattr(value, "data") and torch.is_tensor(value.data):
            _check_cpu_tensor(name + ".data", value.data)
        else:
            _check_cpu_tensor(name, value)


def _pose_np(pose: Any) -> np.ndarray:
    data = pose.data.detach().cpu().to(dtype=torch.float64).numpy()
    if data.shape != (1, 8):
        raise ValueError("Sim3 pose must have shape (1,8)")
    return data[0]


def _validate_factor(factor: dict[str, Any], current_frame_id: int, keyframe_id: int) -> dict[str, Any]:
    if int(factor.get("source_frame_id", -1)) != int(current_frame_id):
        raise ValueError("metric factor source_frame_id must match current_frame_id")
    if int(factor.get("target_frame_id", -1)) != int(keyframe_id):
        raise ValueError("metric factor target_frame_id must match keyframe_id")
    if factor.get("pnp_report", {}).get("accepted") is not True:
        raise ValueError("metric factor must have an accepted PnP report")
    info = np.asarray(factor.get("information"), dtype=np.float64)
    if (
        info.shape != (7, 7)
        or not np.isfinite(info).all()
        or not np.allclose(info, info.T, rtol=0.0, atol=1e-8)
    ):
        raise ValueError("metric factor information must be finite symmetric 7x7")
    if float(np.linalg.eigvalsh(info).min()) <= 0.0:
        raise ValueError("metric factor information must be SPD")
    local = dict(factor)
    local["source_index"] = 0
    local["target_index"] = 1
    local["information"] = info
    return local


def _visual_normal(self: Any, sqrt_info: torch.Tensor, r: torch.Tensor, J: torch.Tensor,
                   visual_row_count: int, visual_effective_count: int):
    huber, _embed, pose_jacobian = _native_helpers()
    fixed_scale = bool(self.cfg.get("stereo_fix_pose_scale", False))
    Jp = pose_jacobian(J, fixed_scale)
    whitened_r = sqrt_info * r
    robust_sqrt_info = sqrt_info * torch.sqrt(huber(whitened_r, k=self.cfg["huber"]))
    if visual_row_count:
        robust_sqrt_info[:visual_row_count] /= np.sqrt(visual_effective_count)
    mdim = Jp.shape[-1]
    A = (robust_sqrt_info[..., None] * Jp).view(-1, mdim)
    b = (robust_sqrt_info * r).view(-1, 1)
    return A.T @ A, -A.T @ b, 0.5 * (b.T @ b).item(), fixed_scale


def _metric_terms(G: Any, factor: dict[str, Any], dtype: torch.dtype):
    residual, jac, info = factor_linearization(np.stack((_pose_np(G), np.array([0, 0, 0, 0, 0, 0, 1, 1], dtype=np.float64))), factor)
    Jm = torch.as_tensor(jac[:, :7], dtype=torch.float64)
    rm = torch.as_tensor(residual, dtype=torch.float64).view(7, 1)
    Info = torch.as_tensor(info, dtype=torch.float64)
    Hm64 = Jm.T @ Info @ Jm
    gm64 = -Jm.T @ Info @ rm
    cm = 0.5 * (rm.T @ Info @ rm).item()
    if not torch.isfinite(Hm64).all() or not torch.isfinite(gm64).all() or not np.isfinite(cm):
        raise ValueError("metric normal equations are not finite")
    return Hm64.to(dtype=dtype), gm64.to(dtype=dtype), cm


def opt_pose_calib_sim3_with_metric_factor(
    tracker: Any,
    *,
    Xf: torch.Tensor,
    Xk: torch.Tensor,
    T_WCf: Any,
    T_WCk: Any,
    Qk: torch.Tensor,
    valid: torch.Tensor,
    conf_w: torch.Tensor,
    meas_k: torch.Tensor,
    valid_meas_k: torch.Tensor,
    K: torch.Tensor,
    img_size: tuple[int, int],
    factor: dict[str, Any] | None = None,
    current_frame_id: int | None = None,
    keyframe_id: int | None = None,
    metric_translation_target: Any = None,
    metric_world_scale: float = 1.0,
) -> tuple[Any, Any] | tuple[Any, Any, list[dict[str, Any]]]:
    """Run original opt_pose_calib_sim3, optionally adding one local metric factor."""
    original_opt = type(tracker).opt_pose_calib_sim3
    if factor is None:
        return original_opt(
            tracker, Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w, meas_k, valid_meas_k, K, img_size,
            metric_translation_target=metric_translation_target,
            metric_world_scale=metric_world_scale,
        )
    if current_frame_id is None or keyframe_id is None:
        raise ValueError("current_frame_id and keyframe_id are required when factor is enabled")
    if bool(tracker.cfg.get("stereo_fix_pose_scale", False)):
        raise ValueError("metric Sim3 factor does not support stereo_fix_pose_scale=True")
    if metric_translation_target is not None:
        raise ValueError("VINS metric_translation_target is unsupported with metric factor enabled")
    _check_cpu_inputs(Xf=Xf, Xk=Xk, T_WCf=T_WCf, T_WCk=T_WCk, Qk=Qk, valid=valid,
                      conf_w=conf_w, meas_k=meas_k, valid_meas_k=valid_meas_k, K=K)
    checked = _validate_factor(factor, current_frame_id, keyframe_id)
    _huber, embed_pose_increment, _pose_jacobian = _native_helpers()
    trace: list[dict[str, Any]] = []
    G = T_WCk.inv() * T_WCf
    had_instance_solve = "solve_pose_increment" in getattr(tracker, "__dict__", {})
    previous_solve = tracker.__dict__.get("solve_pose_increment") if had_instance_solve else None

    def solve_with_metric(self: Any, sqrt_info, r, J, visual_row_count=0, visual_effective_count=1):
        nonlocal G
        Hv, gv, visual_cost, fixed_scale = _visual_normal(
            self, sqrt_info, r, J, visual_row_count, visual_effective_count
        )
        Hm, gm, metric_cost = _metric_terms(G, checked, Hv.dtype)
        H = Hv + Hm
        g = gv + gm
        if not torch.isfinite(H).all() or not torch.isfinite(g).all():
            raise ValueError("combined normal equations are not finite")
        L = torch.linalg.cholesky(H, upper=False)
        tau7 = torch.cholesky_solve(g, L, upper=False).view(1, -1)
        tau = embed_pose_increment(tau7, fixed_scale)
        G = G.retr(tau)
        trace.append({
            "visual_cost": float(visual_cost),
            "metric_cost": float(metric_cost),
            "scale": float(G.data.detach().cpu()[0, 7].item()),
            "tau": tau.detach().cpu().clone(),
        })
        return tau.to(dtype=r.dtype), float(visual_cost + metric_cost)

    try:
        tracker.solve_pose_increment = MethodType(solve_with_metric, tracker)
        T_WCf_out, T_CkCf_out = original_opt(
            tracker, Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w, meas_k, valid_meas_k, K, img_size,
            metric_translation_target=None,
            metric_world_scale=metric_world_scale,
        )
        if trace:
            trace[-1]["final_local_pose_max_abs_diff"] = float(
                torch.max(torch.abs(G.data.detach().cpu() - T_CkCf_out.data.detach().cpu())).item()
            )
        return T_WCf_out, T_CkCf_out, trace
    finally:
        if not had_instance_solve:
            delattr(tracker, "solve_pose_increment")
        else:
            tracker.solve_pose_increment = previous_solve
