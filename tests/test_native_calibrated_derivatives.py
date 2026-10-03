from pathlib import Path
import importlib.util
import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003/native_derivative_audit_v1"
spec = importlib.util.spec_from_file_location("diagnostic_derivatives", HERE / "derivatives.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def poses():
    q = Rotation.from_rotvec([.03, -.02, .01]).as_quat()
    return torch.tensor(np.array([[.1, -.2, .3, *q, 1.4], [.2, .1, -.1, *q, .7]]), dtype=torch.float64)


def test_matrix_exp_translation_rotation_scale_left_action_without_mutation():
    p = poses()
    before = p.clone()
    dx = torch.zeros(2, 7, dtype=torch.float64)
    dx[0, :3] = torch.tensor([.1, .2, .3])
    dx[1, 6] = .04
    output = module.retract_double(p, dx)
    assert torch.equal(p, before)
    assert torch.allclose(output[0, :3], p[0, :3] + dx[0, :3])
    assert torch.allclose(output[1, :3], p[1, :3] * np.exp(.04))
    assert output[1, 7] == pytest.approx(float(p[1, 7] * np.exp(.04)))


def test_matrix_exp_retraction_roundtrip():
    p = poses()
    dx = torch.tensor([[.03, .04, -.02, .03, -.07, .01, .06]] * 2, dtype=torch.float64)
    output = module.retract_double(module.retract_double(p, dx), -dx)
    assert torch.allclose(output, p, atol=1e-12)


def test_invalid_retraction_shape_rejected():
    with pytest.raises(ValueError):
        module.retract_double(poses(), torch.zeros(2, 6))
