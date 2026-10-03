"""Assemble one captured local tracking pair graph for CPU diagnostics.

This is source-only glue for replaying the exact captured current/keyframe
tracking pair.  It validates captured tensors and constructs the native
``solve_GN_calib`` argument list, but it does not run matching, decoding, GN,
GT evaluation, model code, or any production path.
"""
from __future__ import annotations

from pathlib import Path
import math
import sys
from typing import Any

import torch


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
TOOL = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
for root in (BASE, ROOT / "scripts", TOOL):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

_CFG_KEYS = (
    "pixel_border",
    "depth_eps",
    "sigma_pixel",
    "sigma_depth",
    "C_conf",
    "Q_conf",
    "max_iters",
    "delta_norm",
)


def _tensor(name: str, value: Any) -> torch.Tensor:
    if not torch.is_tensor(value):
        raise ValueError(f"{name} must be a tensor")
    if value.device.type != "cpu":
        raise ValueError(f"{name} must be CPU")
    return value


def _clone(value: torch.Tensor) -> torch.Tensor:
    return value.detach().cpu().clone()


def _same_tensor(name: str, got: Any, expected: Any, *, tolerant: bool = False) -> None:
    got_t = _tensor(name, got)
    exp_t = _tensor(name, expected)
    if got_t.shape != exp_t.shape or got_t.dtype != exp_t.dtype:
        raise ValueError(f"{name} mismatch")
    if tolerant and got_t.is_floating_point():
        ok = torch.allclose(got_t, exp_t, rtol=1e-6, atol=1e-6)
    else:
        ok = torch.equal(got_t, exp_t)
    if not bool(ok):
        raise ValueError(f"{name} mismatch")


def _cfg(local_cfg: Any) -> tuple[Any, ...]:
    if not isinstance(local_cfg, dict):
        raise ValueError("local_cfg must be a dict")
    missing = [key for key in _CFG_KEYS if key not in local_cfg]
    if missing:
        raise ValueError(f"local_cfg missing {missing}")
    for key in ("pixel_border", "max_iters"):
        value = local_cfg[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"local_cfg {key} must be int")
        if key == "pixel_border" and value < 0:
            raise ValueError("local_cfg pixel_border must be nonnegative")
        if key == "max_iters" and value <= 0:
            raise ValueError("local_cfg max_iters must be positive")
    for key in ("depth_eps", "sigma_pixel", "sigma_depth", "delta_norm"):
        value = local_cfg[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"local_cfg {key} must be positive finite")
    for key in ("C_conf", "Q_conf"):
        value = local_cfg[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"local_cfg {key} must be nonnegative finite")
    return tuple(local_cfg[key] for key in _CFG_KEYS)


def _img_size(value: Any) -> tuple[int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("img_size must be h,w")
    h, w = int(value[0]), int(value[1])
    if h <= 0 or w <= 0:
        raise ValueError("img_size must be positive")
    return h, w


def _points(name: str, data: Any, count: int) -> torch.Tensor:
    tensor = _tensor(name, data)
    if tensor.shape == (count, 3):
        out = tensor
    elif tensor.ndim == 3 and tensor.shape[-1] == 3 and tensor.numel() == count * 3:
        out = tensor.reshape(count, 3)
    else:
        raise ValueError(f"{name} shape mismatch")
    if not torch.isfinite(out).all():
        raise ValueError(f"{name} must be finite")
    return _clone(out)


def _column(name: str, data: Any, count: int, *, boolean: bool = False) -> torch.Tensor:
    tensor = _tensor(name, data)
    if tensor.shape != (count, 1):
        raise ValueError(f"{name} must have shape N,1")
    if boolean:
        if tensor.dtype != torch.bool:
            raise ValueError(f"{name} must be bool")
    elif not tensor.is_floating_point() or not torch.isfinite(tensor).all():
        raise ValueError(f"{name} must be finite floating point")
    return _clone(tensor)


def _index(name: str, data: Any, count: int) -> torch.Tensor:
    tensor = _tensor(name, data)
    if tensor.shape != (count,) or tensor.dtype != torch.int64:
        raise ValueError(f"{name} must have shape N and dtype int64")
    if count and (int(tensor.min()) < 0 or int(tensor.max()) >= count):
        raise ValueError(f"{name} range invalid")
    return _clone(tensor)


def _valid_q(prefix: str, valid: Any, q: Any, count: int) -> tuple[torch.Tensor, torch.Tensor]:
    valid_t = _column(f"{prefix}_valid", valid, count, boolean=True)
    q_t = _column(f"{prefix}_Q", q, count)
    if bool((q_t < 0).any()):
        raise ValueError(f"{prefix}_Q must be nonnegative")
    return valid_t, q_t


def _check_modes(payload: dict[str, Any], gp: dict[str, Any], opt: dict[str, Any]) -> None:
    if gp.get("use_calib") is not True:
        raise ValueError("use_calib=True is required")
    if opt.get("metric_translation_target") is not None:
        raise ValueError("VINS metric_translation_target is unsupported")
    if bool(payload.get("track_entry", {}).get("cfg", {}).get("stereo_fix_pose_scale", False)):
        raise ValueError("stereo_fix_pose_scale=True is unsupported")


def _pose_data(restored: dict[str, Any]) -> torch.Tensor:
    G = restored["T_WCk"].inv() * restored["T_WCf"]
    first = _tensor("local pose", G.data)
    if first.shape != (1, 8):
        raise ValueError("local pose must have shape 1,8")
    identity = torch.tensor([[0, 0, 0, 0, 0, 0, 1, 1]], dtype=first.dtype)
    return torch.cat((_clone(first), identity), dim=0)


def _selected_forward(payload: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    from local_tracking_pair_inputs import select_tracking_match

    reviewed = select_tracking_match(payload)
    for key in ("current_frame_id", "keyframe_id"):
        if int(selected.get(key, -1)) != int(reviewed.get(key, -2)):
            raise ValueError(f"selected {key} mismatch")
    for key in ("forward_index", "forward_Q"):
        _same_tensor(key, selected.get(key), reviewed.get(key))
    if "raw_forward_valid" not in reviewed or "raw_forward_valid" not in selected:
        raise ValueError("raw_forward_valid is required")
    reviewed_valid = reviewed["raw_forward_valid"]
    selected_valid = selected["raw_forward_valid"]
    _same_tensor("raw_forward_valid", selected_valid, reviewed_valid)
    out = dict(reviewed)
    out["raw_forward_valid"] = reviewed_valid
    return out


def make_tracking_pair_graph(
    payload: dict[str, Any],
    selected: dict[str, Any],
    reverse: dict[str, Any],
    *,
    local_cfg: dict[str, Any],
    sim3_cls: type,
) -> dict[str, Any]:
    cfg_values = _cfg(local_cfg)
    forward = _selected_forward(payload, selected)
    current = int(forward["current_frame_id"])
    keyframe = int(forward["keyframe_id"])
    if int(reverse.get("frame_i", -1)) != keyframe or int(reverse.get("frame_j", -1)) != current:
        raise ValueError("reverse frame ids must be keyframe->current")

    gp = payload.get("get_points_poses")
    opt = payload.get("opt_pose_calib_sim3")
    if not isinstance(gp, dict) or not isinstance(opt, dict):
        raise ValueError("get_points_poses/opt_pose_calib_sim3 missing")
    if int(gp.get("frame_id", -1)) != current or int(gp.get("keyframe_id", -1)) != keyframe:
        raise ValueError("get_points_poses frame ids mismatch")
    _check_modes(payload, gp, opt)
    h, w = _img_size(gp.get("img_size"))
    count = h * w
    _same_tensor("K", opt.get("K"), gp.get("K"))
    if _img_size(opt.get("img_size")) != (h, w):
        raise ValueError("img_size mismatch")

    forward_index = _index("forward_index", forward.get("forward_index"), count)
    reverse_index = _index("reverse_index", reverse.get("reverse_index"), count)
    forward_valid, forward_q = _valid_q("forward", forward.get("raw_forward_valid"), forward.get("forward_Q"), count)
    reverse_valid, reverse_q = _valid_q("reverse", reverse.get("raw_reverse_valid"), reverse.get("reverse_Q"), count)

    current_data = gp.get("current")
    reference_data = gp.get("reference")
    if not isinstance(current_data, dict) or not isinstance(reference_data, dict):
        raise ValueError("current/reference point maps missing")
    current_points = _points("current.X_canon", current_data.get("X_canon"), count).reshape(h, w, 3)
    reference_points = _points("reference.X_canon", reference_data.get("X_canon"), count).reshape(h, w, 3)
    Cs = torch.stack((
        _column("current.confidence", current_data.get("confidence"), count),
        _column("reference.confidence", reference_data.get("confidence"), count),
    ))
    K = _clone(_tensor("K", gp.get("K")))

    from mast3r_slam.geometry import constrain_points_to_ray

    constrained = constrain_points_to_ray((h, w), torch.stack((current_points, reference_points)), K)
    constrained = _tensor("constrained Xs", constrained)
    if constrained.shape != (2, h, w, 3) or not torch.isfinite(constrained).all():
        raise ValueError("constrained Xs shape/finite mismatch")
    Xs = _clone(constrained.reshape(2, count, 3))
    _same_tensor("Xf", Xs[0, forward_index], opt.get("Xf"), tolerant=True)
    _same_tensor("Xk", Xs[1], opt.get("Xk"), tolerant=True)

    from replay_mast3r_tracking_capture import restore_inputs

    restored = restore_inputs(opt, sim3_cls)
    poses = _pose_data(restored)
    args = (
        poses,
        Xs,
        Cs,
        K,
        torch.tensor([0, 1], dtype=torch.int64),
        torch.tensor([1, 0], dtype=torch.int64),
        torch.stack((forward_index, reverse_index)),
        torch.stack((forward_valid, reverse_valid)),
        torch.stack((forward_q, reverse_q)),
        h,
        w,
        *cfg_values,
    )
    return {"args": args, "frame_ids": [current, keyframe]}
