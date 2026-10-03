"""CPU-only onboard metric Sim3 relative factor preparation.

Diagnostic helper only: it reads an existing native graph and stereo metric
depths, keeps the original metric loop gate, and emits directed Sim3 relative
measurements plus finite-difference linearization utilities.  It does not solve,
score, tune weights, or use external ground truth.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
import sys

import numpy as np
import torch
from scipy.linalg import expm
from scipy.spatial.transform import Rotation


BASE = Path(__file__).resolve().parent
for import_root in (BASE, Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


def _load(name: str):
    path = BASE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(path)
    spec.loader.exec_module(module)
    return module


mask_helper = _load("pnp_supported_outlier_mask")
audit = _load("audit_pnp_match_partition")


def _sim3_matrix(pose: np.ndarray) -> np.ndarray:
    pose = np.asarray(pose, dtype=np.float64).reshape(8)
    if not np.isfinite(pose).all() or pose[7] <= 0.0:
        raise ValueError("invalid Sim3 pose")
    quat_norm = np.linalg.norm(pose[3:7])
    if abs(quat_norm - 1.0) > 1e-5:
        raise ValueError("nonunit Sim3 quaternion")
    mat = np.eye(4, dtype=np.float64)
    mat[:3, :3] = pose[7] * Rotation.from_quat(pose[3:7]).as_matrix()
    mat[:3, 3] = pose[:3]
    return mat


def _pose_from_matrix(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float64).reshape(4, 4)
    scale = float(np.cbrt(np.linalg.det(mat[:3, :3])))
    if not np.isfinite(mat).all() or scale <= 0.0:
        raise ValueError("invalid Sim3 matrix")
    rot = mat[:3, :3] / scale
    return np.r_[mat[:3, 3], Rotation.from_matrix(rot).as_quat(), scale]


def _sim3_exp(xi: np.ndarray) -> np.ndarray:
    xi = np.asarray(xi, dtype=np.float64).reshape(7)
    algebra = np.zeros((4, 4), dtype=np.float64)
    wx, wy, wz = xi[3:6]
    algebra[:3, :3] = np.array([[0.0, -wz, wy], [wz, 0.0, -wx], [-wy, wx, 0.0]])
    algebra[:3, :3] += np.eye(3) * xi[6]
    algebra[:3, 3] = xi[:3]
    return np.asarray(expm(algebra), dtype=np.float64)


def _sim3_log(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float64).reshape(4, 4)
    scale = float(np.cbrt(np.linalg.det(mat[:3, :3])))
    if not np.isfinite(mat).all() or not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("invalid Sim3 matrix")
    sigma = float(np.log(scale))
    omega = Rotation.from_matrix(mat[:3, :3] / scale).as_rotvec()
    wx, wy, wz = omega
    generator = np.array([[sigma, -wz, wy], [wz, sigma, -wx], [-wy, wx, sigma]])
    # exp([[A, I], [0, 0]]) has integral_0^1 exp(u A) du in its
    # upper-right block, including the singular A=0 case.  This is the
    # exact Sim3 translation Jacobian; generic logm uses randomized norm
    # estimates which perturb finite-difference factor Jacobians.
    block = np.zeros((6, 6), dtype=np.float64)
    block[:3, :3] = generator
    block[:3, 3:] = np.eye(3)
    translation_jacobian = expm(block)[:3, 3:]
    rho = np.linalg.solve(translation_jacobian, mat[:3, 3])
    return np.r_[rho, omega, sigma]


def relative_residual(pose_i: np.ndarray, pose_j: np.ndarray, measurement: dict[str, Any]) -> np.ndarray:
    pred = np.linalg.inv(_sim3_matrix(pose_j)) @ _sim3_matrix(pose_i)
    meas = np.eye(4, dtype=np.float64)
    meas[:3, :3] = float(measurement["scale_ratio"]) * Rotation.from_quat(measurement["rotation_quat_xyzw"]).as_matrix()
    meas[:3, 3] = np.asarray(measurement["translation_native"], dtype=np.float64).reshape(3)
    return _sim3_log(np.linalg.inv(meas) @ pred)


def _left_retract(pose: np.ndarray, delta: np.ndarray) -> np.ndarray:
    return _pose_from_matrix(_sim3_exp(delta) @ _sim3_matrix(pose))


def factor_linearization(poses: np.ndarray, factor: dict[str, Any], eps: float = 3e-5) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    poses = np.asarray(poses, dtype=np.float64)
    i, j = int(factor["source_index"]), int(factor["target_index"])
    residual0 = relative_residual(poses[i], poses[j], factor["measurement"])
    jac = np.zeros((7, 14), dtype=np.float64)
    for block, index in enumerate((i, j)):
        for dim in range(7):
            delta = np.zeros(7, dtype=np.float64)
            delta[dim] = eps
            plus = poses.copy()
            minus = poses.copy()
            plus[index] = _left_retract(plus[index], delta)
            minus[index] = _left_retract(minus[index], -delta)
            jac[:, block * 7 + dim] = (
                relative_residual(plus[i], plus[j], factor["measurement"])
                - relative_residual(minus[i], minus[j], factor["measurement"])
            ) / (2.0 * eps)
    info = np.asarray(factor["information"], dtype=np.float64)
    if info.shape != (7, 7) or not np.isfinite(info).all():
        raise ValueError("invalid factor information")
    return residual0, jac, info


def _frame_scale_stats(args: tuple[Any, ...], depths: torch.Tensor) -> list[dict[str, float]]:
    xs = args[1].detach().cpu().to(dtype=torch.float64)
    stats: list[dict[str, float]] = []
    for frame in range(xs.shape[0]):
        native_z = xs[frame, :, 2]
        metric_z = depths[frame].detach().cpu().reshape(-1).to(dtype=torch.float64)
        valid = torch.isfinite(native_z) & (native_z > 0) & torch.isfinite(metric_z) & (metric_z > 0)
        if int(valid.sum()) == 0:
            raise ValueError(f"frame {frame} has no metric/native depth support")
        ratios = (metric_z[valid] / native_z[valid]).numpy()
        logs = np.log(ratios)
        log_median = float(np.median(logs))
        mad = float(1.4826 * np.median(np.abs(logs - log_median)))
        if mad <= 0.0 or not np.isfinite(mad):
            raise ValueError(f"frame {frame} has degenerate metric/native scale dispersion")
        stats.append({"ratio": float(np.median(ratios)), "log_mad": mad, "support_count": int(valid.sum())})
    return stats


def _consistent_support(graph: dict[str, Any], depths: torch.Tensor, edge_index: int, transform: np.ndarray) -> dict[str, Any]:
    args = graph["args"]
    source = int(args[4][edge_index])
    target = int(args[5][edge_index])
    valid = (args[7][edge_index].reshape(-1) & (args[8][edge_index].reshape(-1) > float(args[16]))).detach().cpu().bool()
    classes = audit.classify_pnp_reprojection(
        K=args[3],
        source_depth_flat=depths[source],
        target_depth_flat=depths[target],
        target_to_source_index=args[6][edge_index],
        valid_target=valid,
        transform_source_to_target=transform,
        width=int(args[10]),
    )
    keep = (classes["consistent"] & classes["both_depth"]).detach().cpu().bool()
    indices = torch.where(keep)[0]
    if int(indices.numel()) == 0:
        raise ValueError(f"edge {edge_index} has no direct<=2px metric support")
    if int(indices.numel()) > 5000:
        sample = torch.linspace(0, indices.numel() - 1, 5000).long()
        indices = indices[sample]
    source_index = args[6][edge_index].detach().cpu().reshape(-1).long()[indices]
    k = args[3].detach().cpu().numpy().astype(np.float64)
    width = int(args[10])
    z = depths[source].detach().cpu().reshape(-1).to(dtype=torch.float64)[source_index].numpy()
    u = (source_index % width).numpy().astype(np.float64)
    v = torch.div(source_index, width, rounding_mode="floor").numpy().astype(np.float64)
    object_points = np.column_stack(((u - k[0, 2]) * z / k[0, 0], (v - k[1, 2]) * z / k[1, 1], z)).astype(np.float64)
    target_u = (indices % width).numpy().astype(np.float64)
    target_v = torch.div(indices, width, rounding_mode="floor").numpy().astype(np.float64)
    image_points = np.column_stack((target_u, target_v)).astype(np.float64)
    return {"object_points": object_points, "image_points": image_points, "indices": indices}


def _project_native(
    z_native: np.ndarray,
    object_metric_i: np.ndarray,
    ratio_j: float,
    k: np.ndarray,
) -> np.ndarray:
    points_j = (z_native[:3, :3] @ object_metric_i.T).T + z_native[:3, 3]
    physical_j = points_j * float(ratio_j)
    if np.any(physical_j[:, 2] <= 0.0) or not np.isfinite(physical_j).all():
        raise ValueError("nonpositive projected depth")
    return np.column_stack((
        k[0, 0] * physical_j[:, 0] / physical_j[:, 2] + k[0, 2],
        k[1, 1] * physical_j[:, 1] / physical_j[:, 2] + k[1, 2],
    ))


def _measurement_information(
    measurement: dict[str, Any],
    support: dict[str, Any],
    k: np.ndarray,
    ratio_i: float,
    ratio_j: float,
    scale_sigma: float,
    sigma_pixel_floor: float,
    eps: float = 3e-5,
) -> tuple[np.ndarray, dict[str, Any]]:
    if scale_sigma <= 0.0 or not np.isfinite(scale_sigma):
        raise ValueError("degenerate Sim3 scale dispersion")
    z_native = np.eye(4, dtype=np.float64)
    z_native[:3, :3] = float(measurement["scale_ratio"]) * Rotation.from_quat(measurement["rotation_quat_xyzw"]).as_matrix()
    z_native[:3, 3] = np.asarray(measurement["translation_native"], dtype=np.float64).reshape(3)
    object_metric_i = support["object_points"] / float(ratio_i)
    residual0 = (_project_native(z_native, object_metric_i, ratio_j, k) - support["image_points"]).reshape(-1)
    measured_mad = 1.4826 * float(np.median(np.abs(residual0 - np.median(residual0))))
    noise = max(float(sigma_pixel_floor), measured_mad)
    if noise <= 0.0 or not np.isfinite(noise):
        raise ValueError("invalid pixel noise floor")
    jac = np.zeros((residual0.size, 7), dtype=np.float64)
    for dim in range(7):
        delta = np.zeros(7, dtype=np.float64)
        delta[dim] = eps
        plus = (_project_native(z_native @ _sim3_exp(delta), object_metric_i, ratio_j, k) - support["image_points"]).reshape(-1)
        minus = (_project_native(z_native @ _sim3_exp(-delta), object_metric_i, ratio_j, k) - support["image_points"]).reshape(-1)
        jac[:, dim] = (plus - minus) / (2.0 * eps)
    info = (jac.T @ jac) / (noise * noise)
    info[6, 6] += 1.0 / (scale_sigma * scale_sigma)
    eig = np.linalg.eigvalsh(info)
    if not np.isfinite(info).all() or float(eig.min()) <= 0.0:
        raise ValueError("ill-conditioned metric relative information")
    return info, {
        "support_count": int(len(support["indices"])),
        "pixel_noise_px": noise,
        "pixel_mad_px": measured_mad,
        "information_min_eigenvalue": float(eig.min()),
    }


def _factor_for_direction(graph: dict[str, Any], depths: torch.Tensor, edge_index: int, scale_stats: list[dict[str, float]]) -> dict[str, Any]:
    args = graph["args"]
    source = int(args[4][edge_index])
    target = int(args[5][edge_index])
    transform, pnp_report = audit._factorgraph_pnp_transform(graph, depths, edge_index)
    if transform is None or not pnp_report.get("accepted"):
        raise ValueError(f"accepted gate has rejected direction {edge_index}: {pnp_report}")
    support = _consistent_support(graph, depths, edge_index, transform)
    ratio_i = scale_stats[source]["ratio"]
    ratio_j = scale_stats[target]["ratio"]
    scale_sigma = float(np.hypot(scale_stats[source]["log_mad"], scale_stats[target]["log_mad"]))
    measurement = {
        "translation_native": (np.asarray(transform[:3, 3], dtype=np.float64) / ratio_j).tolist(),
        "rotation_quat_xyzw": Rotation.from_matrix(transform[:3, :3]).as_quat().tolist(),
        "scale_ratio": float(ratio_i / ratio_j),
    }
    info, info_report = _measurement_information(
        measurement,
        support,
        args[3].detach().cpu().numpy().astype(np.float64),
        ratio_i,
        ratio_j,
        scale_sigma,
        float(args[13]),
    )
    return {
        "source_index": source,
        "target_index": target,
        "source_frame_id": int(graph["frame_ids"][source]),
        "target_frame_id": int(graph["frame_ids"][target]),
        "edge_index": int(edge_index),
        "measurement": measurement,
        "information": info.tolist(),
        "pnp_report": pnp_report,
        "information_report": info_report,
        "scale_stats": {"source": scale_stats[source], "target": scale_stats[target]},
    }


def prepare_factors(graph: dict[str, Any], depths: torch.Tensor) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    args = tuple(graph["args"])
    edge_map = mask_helper._directed_edge_map(args)
    scale_stats = _frame_scale_stats(args, depths)
    factors: list[dict[str, Any]] = []
    report = {
        "schema": "metric_relative_pose_factor_v1",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "accepted_pair_count": 0,
        "rejected_pair_count": 0,
        "factors": [],
        "rejected_pairs": [],
    }
    visited: set[tuple[int, int]] = set()
    for source, target in sorted(edge_map):
        unordered = tuple(sorted((source, target)))
        if unordered in visited:
            continue
        visited.add(unordered)
        forward = edge_map[(unordered[0], unordered[1])]
        reverse = edge_map[(unordered[1], unordered[0])]
        accepted, gate_report = mask_helper._metric_loop_gate(graph, depths, forward, reverse)
        if not accepted:
            report["rejected_pair_count"] += 1
            report["rejected_pairs"].append({
                "source_index": int(unordered[0]),
                "target_index": int(unordered[1]),
                "accepted": False,
                "gate_report": gate_report,
            })
            continue
        report["accepted_pair_count"] += 1
        for edge_index in (forward, reverse):
            factor = _factor_for_direction(graph, depths, edge_index, scale_stats)
            factors.append(factor)
            report["factors"].append({
                "edge_index": factor["edge_index"],
                "source_index": factor["source_index"],
                "target_index": factor["target_index"],
                "support_count": factor["information_report"]["support_count"],
            })
    return factors, report
