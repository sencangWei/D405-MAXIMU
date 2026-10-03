from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
FROZEN = BASE / "metric_relative_joint_native_falsifier_v2"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


orig = _load_module(BASE / "metric_relative_pose_factor.py", "metric_relative_pose_factor_original_for_test")
candidate = _load_module(BASE / "metric_factor_analytic_candidate.py", "metric_factor_analytic_candidate_for_test")
benchmark = _load_module(
    BASE / "benchmark_metric_factor_analytic_candidate.py",
    "benchmark_metric_factor_analytic_candidate_for_test",
)


def _pose(translation, rotvec, scale):
    return np.r_[translation, Rotation.from_rotvec(rotvec).as_quat(), scale].astype(np.float64)


def _measurement_from_poses(pose_i, pose_j, residual_xi=None):
    z = np.linalg.inv(orig._sim3_matrix(pose_j)) @ orig._sim3_matrix(pose_i)
    if residual_xi is not None:
        z = z @ np.linalg.inv(orig._sim3_exp(np.asarray(residual_xi, dtype=np.float64)))
    scale = float(np.cbrt(np.linalg.det(z[:3, :3])))
    return {
        "translation_native": z[:3, 3].tolist(),
        "rotation_quat_xyzw": Rotation.from_matrix(z[:3, :3] / scale).as_quat().tolist(),
        "scale_ratio": scale,
    }


def _factor(source, target, measurement):
    info = np.diag([1.0, 2.0, 3.0, 1.5, 2.5, 3.5, 4.0])
    return {
        "source_index": source,
        "target_index": target,
        "measurement": measurement,
        "information": info.tolist(),
    }


def _assert_linearization_matches(poses, factor, *, atol=4e-8, rtol=2e-7):
    residual_ref, jac_ref, info_ref = orig.factor_linearization(poses, factor)
    residual_new, jac_new, info_new = candidate.factor_linearization(poses, factor)
    np.testing.assert_allclose(residual_new, residual_ref, atol=1e-12, rtol=0.0)
    np.testing.assert_allclose(info_new, info_ref, atol=0.0, rtol=0.0)
    np.testing.assert_allclose(jac_new, jac_ref, atol=atol, rtol=rtol)

    h_ref = jac_ref.T @ info_ref @ jac_ref
    g_ref = jac_ref.T @ info_ref @ residual_ref
    h_new = jac_new.T @ info_new @ jac_new
    g_new = jac_new.T @ info_new @ residual_new
    np.testing.assert_allclose(h_new, h_ref, atol=2e-7, rtol=8e-7)
    np.testing.assert_allclose(g_new, g_ref, atol=2e-8, rtol=8e-7)


def test_matches_original_for_nontrivial_actual_indices():
    poses = np.vstack([
        _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 1.0),
        _pose([0.2, -0.1, 0.05], [0.05, -0.04, 0.03], 0.92),
        _pose([-0.3, 0.4, 0.2], [0.4, -0.2, 0.3], 1.35),
        _pose([0.7, -0.5, 0.6], [-0.3, 0.6, -0.2], 0.73),
        _pose([-0.6, -0.2, 0.9], [0.2, 0.3, -0.5], 1.18),
    ])
    residual_xi = [0.03, -0.02, 0.04, 0.015, -0.025, 0.02, -0.035]
    factor = _factor(3, 1, _measurement_from_poses(poses[3], poses[1], residual_xi))

    _assert_linearization_matches(poses, factor)


def test_matches_original_near_identity():
    poses = np.vstack([
        _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 1.0),
        _pose([1e-6, -2e-6, 3e-6], [2e-6, -1e-6, 3e-6], np.exp(4e-6)),
        _pose([-4e-6, 1e-6, -2e-6], [-2e-6, 5e-6, -1e-6], np.exp(-3e-6)),
    ])
    factor = _factor(1, 2, _measurement_from_poses(poses[1], poses[2], [1e-6, -2e-6, 3e-6, 4e-7, -5e-7, 6e-7, -7e-7]))

    _assert_linearization_matches(poses, factor, atol=2e-10, rtol=2e-8)


@pytest.mark.parametrize("angle", [0.75 * np.pi, 0.93 * np.pi])
def test_matches_original_below_principal_log_cut(angle):
    poses = np.vstack([
        _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 1.0),
        _pose([0.4, -0.2, 0.3], [0.0, 0.0, angle], 0.8),
        _pose([-0.2, 0.5, -0.1], [0.2, -0.1, -0.3], 1.25),
    ])
    factor = _factor(1, 2, _measurement_from_poses(poses[1], poses[2], [0.01, 0.02, -0.03, 0.02, -0.01, 0.015, 0.01]))

    _assert_linearization_matches(poses, factor, atol=2e-7, rtol=8e-7)


def test_rng_stability_against_original_central_difference():
    rng = np.random.default_rng(20261003)
    for _ in range(40):
        pose_count = 6
        poses = []
        for _frame in range(pose_count):
            rotvec = rng.normal(scale=0.35, size=3)
            if np.linalg.norm(rotvec) > 1.2:
                rotvec *= 1.2 / np.linalg.norm(rotvec)
            poses.append(_pose(rng.normal(scale=0.4, size=3), rotvec, float(np.exp(rng.normal(scale=0.25)))))
        poses = np.vstack(poses)
        source, target = rng.choice(np.arange(pose_count), size=2, replace=False)
        residual = rng.normal(scale=[0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.015])
        factor = _factor(int(source), int(target), _measurement_from_poses(poses[source], poses[target], residual))
        _assert_linearization_matches(poses, factor, atol=8e-8, rtol=4e-7)


@pytest.mark.parametrize("case", ["left877", "left1044", "right587", "right592", "right613", "passing_held2_752", "passing_take01_738"])
def test_frozen_context_representative_factors_match_original(case):
    report = json.loads((FROZEN / case / "report.json").read_text())
    poses = torch.load(FROZEN / case / "poses.pt", map_location="cpu", weights_only=True)["poses"]["pre"].double().numpy()
    factors = report["metric_factors"]
    assert factors, case
    indices = sorted({0, len(factors) // 2, len(factors) - 1})
    for factor_index in indices:
        _assert_linearization_matches(poses, factors[factor_index], atol=1.2e-7, rtol=7e-7)


def test_benchmark_source_report_hash_mismatch_fails_closed():
    report = {
        "id": "left877",
        "status": "DIAGNOSTIC_COMPLETE",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "poses_sha256": "declared",
        "metric_factors": [{"source_index": 0, "target_index": 1}],
    }
    with pytest.raises(ValueError, match="poses_sha256 mismatch"):
        benchmark._validate_source_report("left877", report, "actual")


def test_benchmark_refuses_to_overwrite_output(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("{}\n")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        benchmark._write_new_output(output, {"schema": "unused"})
