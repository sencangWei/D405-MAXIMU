"""Diagnostic joint normal solve: original CUDA factors plus onboard Sim3 factors.

No objective normalization, new weights, damping, clipping or pose priors.
This small CPU normal-system adapter is not a production CUDA replacement.
"""
import hashlib
import numpy as np
from scipy.linalg import cho_factor, cho_solve
import torch


def array_hash(value):
    value = np.ascontiguousarray(value)
    header = f"{value.dtype}:{value.shape}:".encode()
    return hashlib.sha256(header + value.tobytes()).hexdigest()


def assemble_native_blocks(blocks, gradients, ii, jj, pose_count):
    blocks, gradients = np.asarray(blocks, dtype=np.float64), np.asarray(gradients, dtype=np.float64)
    ii, jj = np.asarray(ii, dtype=int), np.asarray(jj, dtype=int)
    if blocks.shape != (4, len(ii), 7, 7) or gradients.shape != (2, len(ii), 7) or len(ii) != len(jj):
        raise ValueError("native block dimensions differ")
    if np.any(ii < 0) or np.any(jj < 0) or np.any(ii >= pose_count) or np.any(jj >= pose_count):
        raise ValueError("native edge out of bounds")
    H, g = np.zeros((7 * pose_count, 7 * pose_count)), np.zeros(7 * pose_count)
    for edge, (i, j) in enumerate(zip(ii, jj)):
        si, sj = slice(7*i, 7*i+7), slice(7*j, 7*j+7)
        for block, first, second in ((0, si, si), (1, si, sj), (2, sj, si), (3, sj, sj)):
            H[first, second] += blocks[block, edge]
        g[si] += gradients[0, edge]
        g[sj] += gradients[1, edge]
    return H, g


def pinned_increment(H, g):
    if H.shape != (len(g), len(g)) or len(g) % 7 or not np.isfinite(H).all() or not np.isfinite(g).all():
        raise ValueError("invalid joint normal system")
    dx = np.zeros_like(g)
    # Native SparseBlock uses Eigen's lower-triangle LLT and pins pose0.
    dx[7:] = -cho_solve(cho_factor(H[7:, 7:], lower=True, check_finite=True), g[7:])
    return dx.reshape(-1, 7)


def solve_joint(args, factors, backend):
    from metric_relative_pose_factor import factor_linearization
    from probe_dense_native_graph import _clone_args

    cuda = _clone_args(torch, args, "cuda")
    count = int(args[0].shape[0])
    ii, jj = args[4].cpu().numpy(), args[5].cpu().numpy()
    trace = []
    for iteration in range(int(args[17])):
        blocks, gradients = backend.inspect_calibrated(*cuda[:17])
        native_H, native_g = blocks.cpu().numpy(), gradients.cpu().numpy()
        H, g = assemble_native_blocks(native_H, native_g, ii, jj, count)
        poses = cuda[0].cpu().double().numpy()
        metric_cost = 0.0
        for factor in factors:
            residual, J, information = factor_linearization(poses, factor)
            if not np.isfinite(information).all() or information.shape != (7, 7):
                raise ValueError("invalid metric information")
            i, j = int(factor["source_index"]), int(factor["target_index"])
            indices = np.r_[np.arange(7*i, 7*i+7), np.arange(7*j, 7*j+7)]
            H[np.ix_(indices, indices)] += J.T @ information @ J
            g[indices] += J.T @ information @ residual
            metric_cost += .5 * float(residual @ information @ residual)
        dx = pinned_increment(H, g)
        norm = float(np.linalg.norm(dx))
        updated = backend.inspect_retract(cuda[0], torch.from_numpy(dx).to(device="cuda", dtype=torch.float32).contiguous())
        if not torch.equal(updated[0], cuda[0][0]):
            raise ValueError("pose0 moved")
        cuda = (updated, *cuda[1:])
        trace.append({"iteration": iteration, "delta_norm": norm, "metric_pre_cost": metric_cost,
                      "pose_before_sha256": array_hash(poses),
                      "native_H_sha256": array_hash(native_H), "native_g_sha256": array_hash(native_g),
                      "joint_H_sha256": array_hash(H), "joint_g_sha256": array_hash(g),
                      "dx_sha256": array_hash(dx), "pose_after_sha256": array_hash(updated.cpu().numpy())})
        if norm < float(args[18]):
            break
    torch.cuda.synchronize()
    poses = cuda[0].cpu()
    if not torch.isfinite(poses).all():
        raise ValueError("nonfinite joint solution")
    return poses, trace
