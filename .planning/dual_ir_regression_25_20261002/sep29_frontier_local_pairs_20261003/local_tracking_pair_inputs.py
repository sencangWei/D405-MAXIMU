"""Validate captured forward tracking pair tensors for later local diagnostics.

Pure CPU helper only.  It selects one actual current->keyframe matching call from
an observer payload and checks it matches the tensors passed into native tracking.
It does not run matching, PnP, GN, graph construction, model code, or GT.
"""
from __future__ import annotations

import math
from typing import Any

import torch


SCHEMA = "mast3r_tracking_input_capture_v1"


def _tensor(name: str, value: Any) -> torch.Tensor:
    if not torch.is_tensor(value):
        raise ValueError(f"{name} must be a tensor")
    if value.device.type != "cpu":
        raise ValueError(f"{name} must be CPU")
    return value


def _clone(value: Any) -> Any:
    if torch.is_tensor(value):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {str(k): _clone(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_clone(v) for v in value)
    return value


def _equal(name: str, got: Any, expected: Any) -> None:
    if torch.is_tensor(got) or torch.is_tensor(expected):
        if not torch.equal(_tensor(name, got), _tensor(name, expected)):
            raise ValueError(f"{name} mismatch")
    elif got != expected:
        raise ValueError(f"{name} mismatch")


def _cfg(payload: dict[str, Any]) -> tuple[float, float]:
    cfg = payload.get("track_entry", {}).get("cfg")
    if not isinstance(cfg, dict) or "C_conf" not in cfg or "Q_conf" not in cfg:
        raise ValueError("C_conf/Q_conf must be captured in tracker cfg")
    if bool(cfg.get("stereo_pointmap_scale_prior", False)):
        raise ValueError("stereo_pointmap_scale_prior=True is unsupported")
    thresholds = float(cfg["C_conf"]), float(cfg["Q_conf"])
    if not all(math.isfinite(value) and value >= 0 for value in thresholds):
        raise ValueError("C_conf/Q_conf must be finite nonnegative")
    return thresholds


def _column(name: str, value: Any, count: int, *, boolean: bool = False) -> torch.Tensor:
    tensor = _tensor(name, value)
    if tensor.shape != (count, 1):
        raise ValueError(f"{name} must have shape N,1")
    if boolean:
        if tensor.dtype != torch.bool:
            raise ValueError(f"{name} must have dtype bool")
    elif not tensor.is_floating_point() or not torch.isfinite(tensor).all():
        raise ValueError(f"{name} must be finite floating point")
    return tensor


def _validate_payload(payload: dict[str, Any]) -> tuple[int, int, dict[str, Any]]:
    if payload.get("schema") != SCHEMA:
        raise ValueError("payload schema mismatch")
    if payload.get("status") != "ok":
        raise ValueError("payload status must be ok")
    track = payload.get("track_entry")
    gp = payload.get("get_points_poses")
    if not isinstance(track, dict) or not isinstance(gp, dict):
        raise ValueError("track_entry/get_points_poses missing")
    current = int(track.get("frame_id"))
    keyframe = int(track.get("reference_frame_id"))
    if int(gp.get("frame_id")) != current:
        raise ValueError("get_points_poses current frame mismatch")
    if int(gp.get("keyframe_id")) != keyframe:
        raise ValueError("get_points_poses keyframe mismatch")
    return current, keyframe, gp


def _call_return(call: dict[str, Any]) -> tuple[torch.Tensor, torch.Tensor]:
    if call.get("error") is not None:
        raise ValueError("matching call is not successful")
    ret = call.get("return")
    if not isinstance(ret, dict):
        raise ValueError("matching return missing")
    idx = _tensor("idx_1_to_2", ret.get("idx_1_to_2"))
    raw_valid = _tensor("valid_match", ret.get("valid_match"))
    if idx.ndim != 2 or idx.shape[0] != 1 or idx.dtype != torch.int64:
        raise ValueError("idx_1_to_2 must have shape 1,N and dtype int64")
    if raw_valid.shape != (1, idx.shape[1], 1) or raw_valid.dtype != torch.bool:
        raise ValueError("valid_match must have shape 1,N,1 and dtype bool")
    return idx, raw_valid


def _selected_calls(payload: dict[str, Any], current: int, keyframe: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    inferences = payload.get("mast3r_asymmetric_inference")
    calls = payload.get("matching_calls")
    if not isinstance(inferences, list) or not isinstance(calls, list):
        raise ValueError("inference/matching calls missing")
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        assoc = call.get("asymmetric_call_index")
        if not isinstance(assoc, int) or not 0 <= assoc < len(inferences):
            raise ValueError("matching association is invalid")
        inference = inferences[assoc]
        if not isinstance(inference, dict):
            continue
        if int(inference.get("frame_i", -1)) == current and int(inference.get("frame_j", -1)) == keyframe:
            _call_return(call)
            selected.append((call, inference))
    if len(selected) > 1:
        raise ValueError("ambiguous duplicate selected matching calls")
    if len(selected) != 1:
        raise ValueError("unique selected matching call not found")
    return selected


def _validate_shapes(call: dict[str, Any], inference: dict[str, Any], gp: dict[str, Any]) -> tuple[int, torch.Tensor, torch.Tensor]:
    img_size = tuple(int(v) for v in gp.get("img_size"))
    X11 = _tensor("X11", call.get("X11"))
    X21 = _tensor("X21", call.get("X21"))
    D11 = _tensor("D11", call.get("D11"))
    D21 = _tensor("D21", call.get("D21"))
    if X11.ndim != 4 or X11.shape[0] != 1 or X11.shape[-1] != 3:
        raise ValueError("X11 shape is invalid")
    h, w = int(X11.shape[1]), int(X11.shape[2])
    if img_size != (h, w):
        raise ValueError("matching input img_size mismatch")
    for name, value in (("X21", X21), ("D11", D11), ("D21", D21)):
        if value.shape[:3] != (1, h, w):
            raise ValueError(f"{name} shape/img_size mismatch")
    X = _tensor("inference X", inference.get("X"))
    D = _tensor("inference D", inference.get("D"))
    Q = _tensor("inference Q", inference.get("Q"))
    if X.shape[:3] != (2, h, w) or D.shape[:3] != (2, h, w):
        raise ValueError("inference X/D shape mismatch")
    if Q.shape != (2, h, w) or not torch.isfinite(Q).all() or bool((Q < 0).any()):
        raise ValueError("inference Q must be shape 2,h,w finite nonnegative")
    _equal("X11", X11, X[0:1])
    _equal("X21", X21, X[1:2])
    _equal("D11", D11, D[0:1])
    _equal("D21", D21, D[1:2])
    return h * w, Q[0].reshape(-1, 1), Q[1].reshape(-1, 1)


def select_tracking_match(payload: dict[str, Any]) -> dict[str, Any]:
    current, keyframe, gp = _validate_payload(payload)
    C_conf, Q_conf = _cfg(payload)
    call, inference = _selected_calls(payload, current, keyframe)[0]
    count, Qii, Qji = _validate_shapes(call, inference, gp)
    idx, raw_valid = _call_return(call)
    if idx.shape[1] != count or int(idx.min()) < 0 or int(idx.max()) >= count:
        raise ValueError("idx_1_to_2 range/length invalid")
    idx0 = idx[0]
    _equal("idx_f2k", _tensor("idx_f2k", gp.get("idx_f2k")), idx0)
    result = gp.get("return")
    if not isinstance(result, (tuple, list)) or len(result) != 8:
        raise ValueError("get_points_poses return must have eight entries")
    current_conf = _column("current confidence", gp.get("current", {}).get("confidence"), count)
    reference_conf = _column("reference confidence", gp.get("reference", {}).get("confidence"), count)
    Cf = current_conf[idx0]
    Ck = reference_conf
    recomputed_Qk = torch.sqrt(Qii[idx0] * Qji)
    _equal("get_points_poses Cf", result[4], Cf)
    _equal("get_points_poses Ck", result[5], Ck)
    opt = payload.get("opt_pose_calib_sim3")
    if (not isinstance(opt, dict) or opt.get("error") is not None
            or not isinstance(opt.get("return"), dict)
            or any(opt["return"].get(name) is None for name in ("T_WCf", "T_CkCf"))):
        raise ValueError("opt_pose_calib_sim3 successful return missing")
    Qk = _column("Qk", opt.get("Qk"), count)
    if Qk.dtype != recomputed_Qk.dtype or bool((Qk < 0).any()) or not torch.isfinite(recomputed_Qk).all():
        raise ValueError("Qk dtype/formula invalid")
    # This is a diagnostic CPU/device formula check, not a matching/gate
    # tolerance. Preserve the captured native Q (including at gate boundaries).
    # Permit at most two representable steps for product/sqrt rounding.
    spacing = torch.nextafter(recomputed_Qk, torch.full_like(recomputed_Qk, float("inf"))) - recomputed_Qk
    q_difference = torch.abs(Qk - recomputed_Qk)
    if not bool((q_difference <= 2 * spacing).all()):
        raise ValueError("Qk formula mismatch beyond two ULP")
    _column("opt valid", opt.get("valid"), count, boolean=True)
    expected_valid = raw_valid[0] & (Cf > C_conf) & (Ck > C_conf) & (Qk > Q_conf)
    _equal("valid", opt.get("valid"), expected_valid)
    _equal("img_size", tuple(opt.get("img_size")), tuple(gp.get("img_size")))
    _equal("K", opt.get("K"), gp.get("K"))
    _equal("Xf", opt.get("Xf"), result[0])
    _equal("Xk", opt.get("Xk"), result[1])
    return {
        "current_frame_id": current,
        "keyframe_id": keyframe,
        "matching_call": _clone(call),
        "inference": _clone(inference),
        "forward_index": idx0.detach().cpu().clone(),
        "raw_forward_valid": raw_valid[0].detach().cpu().clone(),
        "tracking_valid": expected_valid.detach().cpu().clone(),
        "forward_Q": Qk.detach().cpu().clone(),
        "Q_recompute_max_abs_diff": float(q_difference.max().item()),
    }
