"""Decode the actual native reverse MASt3R pair from captured encoded inputs.

Diagnostic helper only: it reuses captured per-frame encoder outputs, calls the
original asymmetric decoder once as keyframe->current, and does not run matching,
graph construction, PnP, GN, GT, model loading, or image encoding.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import torch


def _tensor(name: str, value: Any) -> torch.Tensor:
    if not torch.is_tensor(value):
        raise ValueError(f"{name} must be a tensor")
    return value


def _encoded(name: str, data: Any, frame_id: int, device: Any) -> SimpleNamespace:
    if not isinstance(data, dict):
        raise ValueError(f"{name} encoded data missing")
    if "frame_id" in data and int(data["frame_id"]) != int(frame_id):
        raise ValueError(f"{name} encoded frame_id mismatch")
    feat = _tensor(f"{name}.feat", data.get("feat"))
    pos = _tensor(f"{name}.pos", data.get("pos"))
    shape = _tensor(f"{name}.img_true_shape", data.get("img_true_shape"))
    if feat.ndim != 3 or feat.shape[0] != 1 or feat.shape[1] <= 0 or feat.shape[2] <= 0:
        raise ValueError(f"{name}.feat must have shape 1,N,E with positive N/E")
    if not torch.isfinite(feat).all():
        raise ValueError(f"{name}.feat must be finite")
    if pos.shape != (1, feat.shape[1], 2) or not torch.is_floating_point(pos) and pos.dtype not in (torch.int32, torch.int64):
        raise ValueError(f"{name}.pos must have shape 1,N,2")
    if not torch.isfinite(pos).all():
        raise ValueError(f"{name}.pos must be finite")
    if shape.shape != (1, 2) or shape.dtype not in (torch.int32, torch.int64):
        raise ValueError(f"{name}.img_true_shape must have shape 1,2 integer")
    if bool((shape <= 0).any()):
        raise ValueError(f"{name}.img_true_shape must be positive")
    return SimpleNamespace(
        frame_id=int(frame_id),
        feat=feat.detach().clone().to(device),
        pos=pos.detach().clone().to(device),
        img_true_shape=shape.detach().clone().to(device),
        img=None,
    )


def _validate_selected(selected: dict[str, Any]) -> tuple[int, int, dict[str, Any]]:
    current = int(selected.get("current_frame_id"))
    keyframe = int(selected.get("keyframe_id"))
    inference = selected.get("inference")
    if not isinstance(inference, dict):
        raise ValueError("selected inference missing")
    if int(inference.get("frame_i", -1)) != current:
        raise ValueError("selected inference frame_i/current mismatch")
    if int(inference.get("frame_j", -1)) != keyframe:
        raise ValueError("selected inference frame_j/keyframe mismatch")
    encoded = inference.get("encoded_inputs")
    if not isinstance(encoded, dict):
        raise ValueError("selected inference encoded inputs missing")
    return current, keyframe, encoded


def _encoded_pair(encoded: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "frame_i" in encoded and "frame_j" in encoded:
        return encoded["frame_i"], encoded["frame_j"]
    raise ValueError("selected inference encoded inputs missing")


def _validate_output(result: Any) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    if not isinstance(result, tuple) or len(result) != 4:
        raise ValueError("native reverse output must be X,C,D,Q tuple")
    X, C, D, Q = result
    X, C, D, Q = _tensor("X", X), _tensor("C", C), _tensor("D", D), _tensor("Q", Q)
    if X.ndim != 4 or X.shape[0] != 2 or X.shape[-1] != 3:
        raise ValueError("native reverse X shape invalid")
    h, w = X.shape[1], X.shape[2]
    if h <= 0 or w <= 0:
        raise ValueError("native reverse h/w must be positive")
    if D.ndim != 4 or D.shape[0] != 2 or D.shape[1] != h or D.shape[2] != w or D.shape[3] <= 0:
        raise ValueError("native reverse D shape invalid")
    if C.shape != (2, h, w) or Q.shape != (2, h, w):
        raise ValueError("native reverse C/D/Q shape invalid")
    if not (torch.isfinite(X).all() and torch.isfinite(C).all() and torch.isfinite(D).all() and torch.isfinite(Q).all()):
        raise ValueError("native reverse X/C/D/Q must be finite")
    if bool((Q < 0).any()):
        raise ValueError("native reverse Q must be nonnegative")
    return X, C, D, Q


def _clone_dict(data: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in data.items():
        out[str(key)] = value.detach().cpu().clone() if torch.is_tensor(value) else value
    return out


def decode_reverse_pair(model: Any, selected: dict[str, Any], *, device: Any) -> dict[str, Any]:
    current, keyframe, encoded = _validate_selected(selected)
    current_encoded, reference_encoded = _encoded_pair(encoded)
    reference_frame = _encoded("reference", reference_encoded, keyframe, device)
    current_frame = _encoded("current", current_encoded, current, device)
    from mast3r_slam import mast3r_utils

    result = mast3r_utils.mast3r_asymmetric_inference(model, reference_frame, current_frame)
    X, C, D, Q = _validate_output(result)
    return {
        "frame_i": keyframe,
        "frame_j": current,
        "encoded_inputs": {
            "frame_i": {"source_outer_key": "frame_j", **_clone_dict(reference_encoded)},
            "frame_j": {"source_outer_key": "frame_i", **_clone_dict(current_encoded)},
        },
        "result": result,
        "Xjj": X[0].detach().cpu().clone(),
        "Xij": X[1].detach().cpu().clone(),
        "Cjj": C[0].detach().cpu().clone(),
        "Cij": C[1].detach().cpu().clone(),
        "Djj": D[0].detach().cpu().clone(),
        "Dij": D[1].detach().cpu().clone(),
        "Qjj": Q[0].detach().cpu().clone(),
        "Qij": Q[1].detach().cpu().clone(),
    }
