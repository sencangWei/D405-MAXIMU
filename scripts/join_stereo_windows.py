"""Pure-array helpers to join two adjacent five-node stereo windows."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def _arrays(data: dict, name: str):
    obs = np.asarray(data.get("observations"), dtype=float)
    valid = np.asarray(data.get("valid"), dtype=bool)
    points = np.asarray(data.get("initial_points"), dtype=float)
    if obs.ndim != 3 or obs.shape[0] != 5 or obs.shape[2] != 4:
        raise ValueError(f"{name} observations shape")
    if valid.shape != obs.shape[:2] or points.shape != (obs.shape[1], 3):
        raise ValueError(f"{name} shape")
    if not np.all(np.isfinite(obs[valid])) or not np.all(np.isfinite(points)):
        raise ValueError(f"{name} nonfinite")
    return obs, valid, points


def _rotations(value, name: str) -> Rotation:
    if not isinstance(value, Rotation) or len(value) != 5:
        raise ValueError(f"{name} rotation shape")
    return value


def _mask(mask, shape, name: str) -> np.ndarray:
    out = np.asarray(mask, dtype=bool)
    if out.shape != shape:
        raise ValueError(f"{name} shape")
    return out


def boundary_matches(first_data: dict, second_data: dict, max_px: float = 1.0) -> dict:
    """Match boundary stereo pixels.

    ``rejected_ambiguous`` is diagnostic-only: it counts boundary observations
    involved in non-one-to-one candidate conflicts, including A rows with
    multiple B candidates and B columns with multiple A candidates. Matching
    remains strict mutual one-to-one.
    """
    first_obs, first_valid, _ = _arrays(first_data, "first")
    second_obs, second_valid, _ = _arrays(second_data, "second")
    if not np.isfinite(max_px) or max_px <= 0:
        raise ValueError("max_px")
    a_idx = np.flatnonzero(first_valid[-1])
    b_idx = np.flatnonzero(second_valid[0])
    if len(a_idx) == 0 or len(b_idx) == 0:
        pairs_a = np.array([], dtype=int)
        pairs_b = np.array([], dtype=int)
    else:
        a = first_obs[-1, a_idx]
        b = second_obs[0, b_idx]
        left = np.linalg.norm(a[:, None, :2] - b[None, :, :2], axis=2)
        right = np.linalg.norm(a[:, None, 2:] - b[None, :, 2:], axis=2)
        distance = np.maximum(left, right)
        candidates = distance <= max_px
        pairs = []
        for ai, row in enumerate(candidates):
            cols = np.flatnonzero(row)
            if len(cols) != 1:
                continue
            bj = int(cols[0])
            if np.count_nonzero(candidates[:, bj]) != 1:
                continue
            if np.argmin(distance[ai]) == bj and np.argmin(distance[:, bj]) == ai:
                pairs.append((int(a_idx[ai]), int(b_idx[bj])))
        pairs_a = np.array([p[0] for p in pairs], dtype=int)
        pairs_b = np.array([p[1] for p in pairs], dtype=int)
    holdout_b = np.arange(second_obs.shape[1]) % 5 == 0
    for a, b in zip(pairs_a, pairs_b):
        holdout_b[b] = (a % 5) == 0
    rejected = 0
    if len(a_idx) and len(b_idx):
        ambiguous_a = np.count_nonzero(candidates, axis=1) > 1
        ambiguous_b = np.count_nonzero(candidates, axis=0) > 1
        rejected = int(np.count_nonzero(ambiguous_a) + np.count_nonzero(ambiguous_b))
    return {
        "pairs_a": pairs_a,
        "pairs_b": pairs_b,
        "holdout_b": holdout_b,
        "rejected_ambiguous": rejected,
        "max_px": float(max_px),
    }


def join_stereo_windows(
    first_data: dict,
    second_data: dict,
    first_admission,
    second_admission,
    first_centers,
    first_rotations,
    second_centers,
    second_rotations,
    first_holdout=None,
    second_holdout=None,
    max_px: float = 1.0,
) -> dict:
    first_obs, first_valid, first_points = _arrays(first_data, "first")
    second_obs, second_valid, second_points = _arrays(second_data, "second")
    first_admission = _mask(first_admission, first_valid.shape, "first_admission")
    second_admission = _mask(second_admission, second_valid.shape, "second_admission")
    if np.any(first_admission & ~first_valid) or np.any(second_admission & ~second_valid):
        raise ValueError("admission rawinvalid")
    first_centers = np.asarray(first_centers, dtype=float)
    second_centers = np.asarray(second_centers, dtype=float)
    first_rotations = _rotations(first_rotations, "first")
    second_rotations = _rotations(second_rotations, "second")
    if first_centers.shape != (5, 3) or second_centers.shape != (5, 3):
        raise ValueError("center shape")
    if not np.all(np.isfinite(first_centers)) or not np.all(np.isfinite(second_centers)):
        raise ValueError("center nonfinite")
    if np.any(first_points[:, 2] <= 0):
        raise ValueError("first initial depth")
    first_holdout = np.arange(first_obs.shape[1]) % 5 == 0 if first_holdout is None else np.asarray(first_holdout, dtype=bool)
    first_holdout = first_holdout.copy()
    if first_holdout.shape != (first_obs.shape[1],):
        raise ValueError("first_holdout shape")

    matches = boundary_matches(first_data, second_data, max_px=max_px)
    b_to_a = {int(b): int(a) for a, b in zip(matches["pairs_a"], matches["pairs_b"])}
    second_holdout = matches["holdout_b"] if second_holdout is None else np.asarray(second_holdout, dtype=bool)
    second_holdout = second_holdout.copy()
    if second_holdout.shape != (second_obs.shape[1],):
        raise ValueError("second_holdout shape")
    for a, b in b_to_a.items():
        second_holdout[a] = bool(first_holdout[b])

    end_rotation = first_rotations[4]
    end_center = first_centers[4]
    transformed_second_points = end_rotation.apply(second_points) + end_center
    if np.any(transformed_second_points[[j for j in range(len(second_points)) if j not in b_to_a], 2] <= 0):
        raise ValueError("invalid birth depth")

    mapping = {}
    points = [p.copy() for p in first_points]
    holdout = [bool(v) for v in first_holdout]
    birth_node = [0] * len(points)
    birth_points = [p.copy() for p in first_points]
    for b in range(len(second_points)):
        if b in b_to_a:
            mapping[b] = b_to_a[b]
        else:
            mapping[b] = len(points)
            points.append(transformed_second_points[b].copy())
            holdout.append(bool(second_holdout[b]))
            birth_node.append(4)
            birth_points.append(second_points[b].copy())

    count = len(points)
    observations = np.full((9, count, 4), np.nan, dtype=float)
    valid = np.zeros((9, count), dtype=bool)
    admission = np.zeros((9, count), dtype=bool)
    observations[:5, : len(first_points)] = first_obs
    valid[:5, : len(first_points)] = first_valid
    admission[:5, : len(first_points)] = first_admission

    for b, target in mapping.items():
        for frame in range(5):
            node = frame + 4
            if not second_valid[frame, b]:
                continue
            if frame == 0 and b in b_to_a and first_admission[4, target]:
                continue
            observations[node, target] = second_obs[frame, b]
            valid[node, target] = True
            admission[node, target] = bool(second_admission[frame, b])

    centers = np.vstack((first_centers, end_rotation.apply(second_centers[1:]) + end_center))
    rotations = Rotation.concatenate([first_rotations, end_rotation * second_rotations[1:]])
    heldout = np.asarray(holdout, dtype=bool)
    birth_node = np.asarray(birth_node, dtype=int)
    shared = np.zeros(count, dtype=bool)
    for a, b in zip(matches["pairs_a"], matches["pairs_b"]):
        target = int(a)
        shared[target] = bool(np.any(admission[:4, target]) and np.any(second_admission[1:, int(b)]))
    train = ~heldout
    heldout_rawvalid = valid & heldout[None, :]
    admitted_train = admission & train[None, :]
    return {
        "observations": observations,
        "valid": valid,
        "initial_points": np.asarray(points, dtype=float),
        "initial_centers": centers,
        "initial_rotations": rotations,
        "admission": admission,
        "admitted_train": admitted_train,
        "heldout": heldout,
        "train": train,
        "heldout_rawvalid": heldout_rawvalid,
        "birth_indices": birth_node,
        "birth_points": np.asarray(birth_points, dtype=float),
        "matches": matches,
        "shared_train_cross_window": shared,
        "shared_train_count": int(np.count_nonzero(shared & train)),
        "mapping": {
            "second_to_joined": {int(k): int(v) for k, v in mapping.items()},
            "matched_first": matches["pairs_a"].astype(int).tolist(),
            "matched_second": matches["pairs_b"].astype(int).tolist(),
        },
        "policy": "adjacent windows joined without fallback; second poses/points transformed by first initial PnP endpoint",
    }
