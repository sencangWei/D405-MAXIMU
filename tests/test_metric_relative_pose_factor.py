from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import torch
from scipy.linalg import logm
from scipy.spatial.transform import Rotation


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "metric_relative_pose_factor.py"
)
spec = importlib.util.spec_from_file_location("metric_relative_pose_factor", MODULE)
metric = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(metric)


def _pose(t, rot=None, scale=1.0):
    q = (rot or Rotation.identity()).as_quat()
    return np.r_[np.asarray(t, dtype=np.float64), q, float(scale)]


def _args(edge_count: int = 2):
    poses = torch.from_numpy(np.stack([
        _pose([0.0, 0.0, 0.0], scale=1.0),
        _pose([1.0, 0.0, 0.0], scale=2.0),
        _pose([2.0, 0.0, 0.0], scale=2.0),
    ])).to(dtype=torch.float64)
    xs = torch.ones(3, 4, 3, dtype=torch.float64)
    xs[:, :, 0] = torch.tensor([0.0, 1.0, 0.0, 1.0])
    xs[:, :, 1] = torch.tensor([0.0, 0.0, 1.0, 1.0])
    cs = torch.ones(3, 4, 1, dtype=torch.float64)
    k = torch.eye(3, dtype=torch.float64)
    ii = torch.tensor([0, 1] if edge_count == 2 else [0, 1, 1, 2])
    jj = torch.tensor([1, 0] if edge_count == 2 else [1, 0, 2, 1])
    idx = torch.arange(4).repeat(edge_count, 1)
    valid = torch.ones(edge_count, 4, 1, dtype=torch.bool)
    q = torch.ones(edge_count, 4, 1, dtype=torch.float64)
    return (
        poses, xs, cs, k, ii, jj, idx, valid, q,
        2, 2, 0, 1e-6, 2.0, 0.5, 0.2, 0.1, 5, 1e-4,
    )


def _graph(edge_count: int = 2):
    return {"frame_ids": [10, 11, 12], "args": _args(edge_count)}


def _depths(scale=(2.0, 4.0, 4.0), frame_count=3):
    rows = []
    for value in scale[:frame_count]:
        base = float(value)
        rows.append(torch.tensor([base * 0.9, base, base * 1.1, base * 1.05], dtype=torch.float64))
    return torch.stack(rows)


def test_relative_residual_zero_for_nontrivial_source_to_target_measurement():
    rot_j = Rotation.from_euler("z", 20.0, degrees=True)
    pose_i = _pose([0.3, -0.2, 0.1], Rotation.from_euler("x", 5.0, degrees=True), scale=1.2)
    pose_j = _pose([1.0, 0.4, -0.1], rot_j, scale=2.0)
    z = np.linalg.inv(metric._sim3_matrix(pose_j)) @ metric._sim3_matrix(pose_i)
    measurement = {
        "translation_native": z[:3, 3].tolist(),
        "rotation_quat_xyzw": Rotation.from_matrix(z[:3, :3] / np.cbrt(np.linalg.det(z[:3, :3]))).as_quat().tolist(),
        "scale_ratio": float(np.cbrt(np.linalg.det(z[:3, :3]))),
    }

    residual = metric.relative_residual(pose_i, pose_j, measurement)

    assert np.linalg.norm(residual) < 1e-9


def test_relative_residual_detects_wrong_direction_and_scale():
    pose_i = _pose([0.0, 0.0, 0.0], scale=1.0)
    pose_j = _pose([1.0, 0.0, 0.0], scale=2.0)
    wrong = {
        "translation_native": [1.0, 0.0, 0.0],
        "rotation_quat_xyzw": Rotation.identity().as_quat().tolist(),
        "scale_ratio": 1.0,
    }

    residual = metric.relative_residual(pose_i, pose_j, wrong)

    assert abs(residual[0]) > 1.0
    assert abs(residual[6]) > 0.6


def test_sim3_log_is_rng_independent_and_matches_matrix_logarithm():
    xi = np.array([.7, -.4, .3, .24, -.11, .07, .18])
    matrix = metric._sim3_exp(xi)
    random_state = np.random.get_state()
    try:
        np.random.seed(19)
        before = np.random.get_state()
        result = metric._sim3_log(matrix)
        after = np.random.get_state()
        assert before[0] == after[0]
        np.testing.assert_array_equal(before[1], after[1])
        assert before[2:] == after[2:]
        np.random.seed(71)
        np.testing.assert_array_equal(result, metric._sim3_log(matrix))
        np.testing.assert_allclose(result, xi, atol=2e-12, rtol=0)
        algebra = np.real(logm(matrix))
        expected = np.r_[algebra[:3, 3], algebra[2, 1], algebra[0, 2], algebra[1, 0], np.trace(algebra[:3, :3]) / 3]
        np.testing.assert_allclose(result, expected, atol=2e-12, rtol=0)
    finally:
        np.random.set_state(random_state)


def test_sim3_log_near_identity_large_scale_and_rotation_round_trip():
    for angle, sigma in [(0., 0.), (1e-10, 1e-10), (.4, 0.), (0., -.7), (2.8, -2.), (.2, 2.)]:
        xi = np.array([.6, -.3, .2, angle, 0., 0., sigma])
        np.testing.assert_allclose(metric._sim3_log(metric._sim3_exp(xi)), xi, atol=2e-11, rtol=0)


def test_factor_linearization_shapes_and_finite_values():
    poses = np.stack([
        _pose([0.0, 0.0, 0.0], scale=1.0),
        _pose([1.0, 0.0, 0.0], scale=2.0),
    ])
    factor = {
        "source_index": 0,
        "target_index": 1,
        "measurement": {
            "translation_native": [-0.5, 0.0, 0.0],
            "rotation_quat_xyzw": Rotation.identity().as_quat().tolist(),
            "scale_ratio": 0.5,
        },
        "information": np.eye(7).tolist(),
    }

    residual, jac, info = metric.factor_linearization(poses, factor)

    assert residual.shape == (7,)
    assert jac.shape == (7, 14)
    assert info.shape == (7, 7)
    assert np.isfinite(jac).all()
    assert abs(jac[0, 0]) > 0.1


def test_prepare_factors_accepts_pairs_and_uses_native_units(monkeypatch):
    graph = _graph()
    depths = _depths()
    monkeypatch.setattr(metric.mask_helper, "_metric_loop_gate", lambda graph, depths, fwd, rev: (True, {"accepted": True}))

    def fake_pnp(graph, depths, edge_index):
        transform = np.eye(4)
        transform[0, 3] = 2.0
        return transform, {"accepted": True}

    monkeypatch.setattr(metric.audit, "_factorgraph_pnp_transform", fake_pnp)

    factors, report = metric.prepare_factors(graph, depths)

    assert report["accepted_pair_count"] == 1
    assert report["rejected_pair_count"] == 0
    assert report["external_ground_truth_used"] is False
    assert len(factors) == 2
    # r0/r1 use exact medians of D/Z, not exp(median(log(D/Z))).
    r0 = np.median([1.8, 2.0, 2.2, 2.1])
    r1 = np.median([3.6, 4.0, 4.4, 4.2])
    assert np.allclose(factors[0]["measurement"]["translation_native"], [2.0 / r1, 0.0, 0.0])
    assert np.isclose(factors[0]["measurement"]["scale_ratio"], 0.5)
    assert np.linalg.eigvalsh(np.asarray(factors[0]["information"])).min() > 0.0


def test_prepare_factors_converts_nonidentity_forward_and_reverse_measurements(monkeypatch):
    graph = _graph()
    depths = _depths()
    monkeypatch.setattr(metric.mask_helper, "_metric_loop_gate", lambda graph, depths, fwd, rev: (True, {"accepted": True}))
    r0 = np.median([1.8, 2.0, 2.2, 2.1])
    r1 = np.median([3.6, 4.0, 4.4, 4.2])
    forward_rot = Rotation.from_euler("zyx", [12.0, -5.0, 7.0], degrees=True)
    reverse_rot = Rotation.from_euler("xyz", [-4.0, 9.0, 3.0], degrees=True)
    forward_t = np.array([1.2, -0.4, 0.7])
    reverse_t = np.array([-0.3, 0.8, 1.1])

    def fake_pnp(graph, depths, edge_index):
        transform = np.eye(4)
        if edge_index == 0:
            transform[:3, :3] = forward_rot.as_matrix()
            transform[:3, 3] = forward_t
        else:
            transform[:3, :3] = reverse_rot.as_matrix()
            transform[:3, 3] = reverse_t
        return transform, {"accepted": True}

    monkeypatch.setattr(metric.audit, "_factorgraph_pnp_transform", fake_pnp)

    factors, _ = metric.prepare_factors(graph, depths)

    assert len(factors) == 2
    assert factors[0]["edge_index"] == 0
    assert np.allclose(factors[0]["measurement"]["rotation_quat_xyzw"], forward_rot.as_quat())
    assert np.allclose(factors[0]["measurement"]["translation_native"], forward_t / r1)
    assert np.isclose(factors[0]["measurement"]["scale_ratio"], r0 / r1)
    assert factors[1]["edge_index"] == 1
    assert np.allclose(factors[1]["measurement"]["rotation_quat_xyzw"], reverse_rot.as_quat())
    assert np.allclose(factors[1]["measurement"]["translation_native"], reverse_t / r0)
    assert np.isclose(factors[1]["measurement"]["scale_ratio"], r1 / r0)


def test_prepare_factors_preserves_rejected_pairs(monkeypatch):
    graph = _graph()
    monkeypatch.setattr(metric.mask_helper, "_metric_loop_gate", lambda graph, depths, fwd, rev: (False, {"accepted": False, "reason": "cycle"}))

    factors, report = metric.prepare_factors(graph, _depths())

    assert factors == []
    assert report["accepted_pair_count"] == 0
    assert report["rejected_pair_count"] == 1
    assert report["rejected_pairs"][0]["gate_report"]["reason"] == "cycle"


def test_prepare_factors_fails_when_direction_pnp_rejects_after_accepted_gate(monkeypatch):
    graph = _graph()
    monkeypatch.setattr(metric.mask_helper, "_metric_loop_gate", lambda graph, depths, fwd, rev: (True, {"accepted": True}))
    monkeypatch.setattr(metric.audit, "_factorgraph_pnp_transform", lambda graph, depths, edge: (None, {"accepted": False}))

    try:
        metric.prepare_factors(graph, _depths())
    except ValueError as exc:
        assert "accepted gate has rejected direction" in str(exc)
    else:
        raise AssertionError("rejected direction was accepted")
