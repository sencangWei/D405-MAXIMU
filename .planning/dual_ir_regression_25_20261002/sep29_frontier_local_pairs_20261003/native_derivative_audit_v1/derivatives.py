"""Central differences of the frozen calibrated residual, not a new solver."""
from pathlib import Path
import sys

import numpy as np
import torch
from scipy.linalg import expm
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audit_calibrated_graph_edges as residual


def retract_double(poses, dx):
    """Independent float64 Exp(delta) * pose using homogeneous matrices."""
    p = poses.detach().cpu().double().numpy()
    delta = dx.detach().cpu().double().numpy()
    if p.shape != (len(p), 8) or delta.shape != (len(p), 7):
        raise ValueError("expected poses[N,8] and dx[N,7]")
    output = p.copy()
    for i, step in enumerate(delta):
        x, y, z = step[3:6]
        algebra = np.zeros((4, 4))
        algebra[:3, :3] = [[step[6], -z, y], [z, step[6], -x], [-y, x, step[6]]]
        algebra[:3, 3] = step[:3]
        transform = np.eye(4)
        transform[:3, :3] = p[i, 7] * Rotation.from_quat(p[i, 3:7]).as_matrix()
        transform[:3, 3] = p[i, :3]
        updated = expm(algebra) @ transform
        scale = p[i, 7] * np.exp(step[6])
        output[i, :3] = updated[:3, 3]
        output[i, 3:7] = Rotation.from_matrix(updated[:3, :3] / scale).as_quat()
        output[i, 7] = scale
    return torch.from_numpy(output)


def fixed_mask_whitened(args, poses, mask):
    value = residual.calibrated_edge_residuals(args, 0, poses=poses)
    q = args[8].detach().cpu().double()[0, :, 0]
    scale = torch.sqrt(q[mask])[:, None] / torch.tensor([args[13], args[13], args[14]], dtype=torch.float64)
    return value["residual"][mask] * scale


def check_edge(args, native_H, native_g, epsilon=3e-5):
    poses = args[0].detach().cpu().double()
    base = residual.calibrated_edge_residuals(args, 0)
    mask = base["mask"]
    if not mask.any():
        raise ValueError("no native support")
    r = fixed_mask_whitened(args, poses, mask).flatten()
    columns = []
    cost_gradient = []
    for column in range(14):
        dx = torch.zeros(2, 7, dtype=torch.float64)
        dx.reshape(-1)[column] = epsilon
        plus = fixed_mask_whitened(args, retract_double(poses, dx), mask).flatten()
        minus = fixed_mask_whitened(args, retract_double(poses, -dx), mask).flatten()
        columns.append((plus - minus) / (2 * epsilon))
        cost_gradient.append((residual._huber_rho(torch, plus).sum() - residual._huber_rho(torch, minus).sum()) / (2 * epsilon))
    J = torch.stack(columns, dim=1)
    weights = residual._huber_weight(torch, r)
    g_expected = J.T @ (weights * r)
    H_expected = J.T @ (weights[:, None] * J)
    blocks = native_H.detach().cpu().double()[:, 0]
    H = torch.cat((torch.cat((blocks[0], blocks[1]), dim=1),
                   torch.cat((blocks[2], blocks[3]), dim=1)), dim=0)
    g = native_g.detach().cpu().double()[:, 0].reshape(-1)

    def error(actual, expected):
        return float(torch.linalg.vector_norm(actual - expected) / torch.linalg.vector_norm(expected).clamp(min=1e-12))

    gradient_error = error(g, g_expected)
    hessian_error = error(H, H_expected)
    cost_fd_error = error(torch.stack(cost_gradient), g_expected)
    return {"native_support_count": int(mask.sum()), "epsilon": epsilon,
            "native_gradient_relative_error": gradient_error,
            "native_irls_H_relative_error": hessian_error,
            "cost_directional_fd_relative_error": cost_fd_error,
            "native_H_symmetry_max_abs": float((H-H.T).abs().max()),
            "gradient_norm": float(g.norm()),
            "derivative_agreement": gradient_error < .002 and hessian_error < .002 and cost_fd_error < .002,
            "note": "H is IRLS J^T W J, not the exact true-Huber Hessian; original pre-mask frozen for finite differences"}
