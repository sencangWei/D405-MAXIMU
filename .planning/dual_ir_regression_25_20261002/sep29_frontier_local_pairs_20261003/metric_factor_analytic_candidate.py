"""Isolated analytic-Jacobian candidate for metric Sim3 relative factors.

This file intentionally does not patch the production/native solver.  It keeps
the original residual and information contract from metric_relative_pose_factor
and only replaces the central-difference factor Jacobian with the closed-form
left-perturbation Jacobian for

    r = Log(Z^-1 T_j^-1 T_i),   pose perturbation T <- Exp(delta) T

using xi order [rho_x, rho_y, rho_z, omega_x, omega_y, omega_z, sigma].
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np
from scipy.linalg import expm


BASE = Path(__file__).resolve().parent


def _load_original():
    path = BASE / "metric_relative_pose_factor.py"
    spec = importlib.util.spec_from_file_location("metric_relative_pose_factor_original_for_candidate", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(path)
    spec.loader.exec_module(module)
    return module


_orig = _load_original()


def _hat(v: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(v, dtype=np.float64).reshape(3)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=np.float64)


def _measurement_matrix(measurement: dict[str, Any]) -> np.ndarray:
    meas = np.eye(4, dtype=np.float64)
    meas[:3, :3] = float(measurement["scale_ratio"]) * _orig.Rotation.from_quat(
        measurement["rotation_quat_xyzw"]
    ).as_matrix()
    meas[:3, 3] = np.asarray(measurement["translation_native"], dtype=np.float64).reshape(3)
    return meas


def _sim3_ad_matrix(xi: np.ndarray) -> np.ndarray:
    """Lie algebra adjoint ad_xi for xi=[rho, omega, sigma]."""
    xi = np.asarray(xi, dtype=np.float64).reshape(7)
    rho = xi[:3]
    omega = xi[3:6]
    sigma = float(xi[6])
    ad = np.zeros((7, 7), dtype=np.float64)
    ad[:3, :3] = sigma * np.eye(3) + _hat(omega)
    ad[:3, 3:6] = _hat(rho)
    ad[:3, 6] = -rho
    ad[3:6, 3:6] = _hat(omega)
    return ad


def _sim3_left_jacobian(xi: np.ndarray) -> np.ndarray:
    """Exact J_l(xi) = integral_0^1 exp(u ad_xi) du via augmented expm."""
    ad = _sim3_ad_matrix(xi)
    block = np.zeros((14, 14), dtype=np.float64)
    block[:7, :7] = ad
    block[:7, 7:] = np.eye(7)
    return np.asarray(expm(block)[:7, 7:], dtype=np.float64)


def _sim3_group_adjoint(mat: np.ndarray) -> np.ndarray:
    """Group adjoint for Sim3 matrix [[sR, t], [0, 1]]."""
    mat = np.asarray(mat, dtype=np.float64).reshape(4, 4)
    scale = float(np.cbrt(np.linalg.det(mat[:3, :3])))
    if not np.isfinite(mat).all() or not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("invalid Sim3 adjoint matrix")
    rot = mat[:3, :3] / scale
    trans = mat[:3, 3]
    adj = np.zeros((7, 7), dtype=np.float64)
    adj[:3, :3] = scale * rot
    adj[:3, 3:6] = _hat(trans) @ rot
    adj[:3, 6] = -trans
    adj[3:6, 3:6] = rot
    adj[6, 6] = 1.0
    return adj


def relative_residual(pose_i: np.ndarray, pose_j: np.ndarray, measurement: dict[str, Any]) -> np.ndarray:
    return _orig.relative_residual(pose_i, pose_j, measurement)


def factor_linearization(poses: np.ndarray, factor: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    poses = np.asarray(poses, dtype=np.float64)
    i, j = int(factor["source_index"]), int(factor["target_index"])
    residual0 = relative_residual(poses[i], poses[j], factor["measurement"])
    info = np.asarray(factor["information"], dtype=np.float64)
    if info.shape != (7, 7) or not np.isfinite(info).all():
        raise ValueError("invalid factor information")

    meas = _measurement_matrix(factor["measurement"])
    tj = _orig._sim3_matrix(poses[j])
    left_multiplier = np.linalg.inv(meas) @ np.linalg.inv(tj)
    a = np.linalg.solve(_sim3_left_jacobian(residual0), _sim3_group_adjoint(left_multiplier))
    jac = np.concatenate((a, -a), axis=1)
    if jac.shape != (7, 14) or not np.isfinite(jac).all():
        raise ValueError("invalid analytic metric factor Jacobian")
    return residual0, jac, info
