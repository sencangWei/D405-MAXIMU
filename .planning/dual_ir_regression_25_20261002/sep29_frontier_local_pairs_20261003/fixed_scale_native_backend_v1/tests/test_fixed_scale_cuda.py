"""Actual CUDA contracts, not a substitute for source/ATE regression."""
from pathlib import Path
import sys

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_backend import build_extension

pytestmark = pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required")


@pytest.fixture(scope="module")
def backend():
    return build_extension()


def tiny_args(iterations=5):
    height, width = 16, 16
    y, x = torch.meshgrid(torch.arange(height), torch.arange(width), indexing="ij")
    z = 1 + .02*x + .03*y + .01*torch.sin(x.float())
    points = torch.stack(((x-7.5)*z/20, (y-7.5)*z/20, z), dim=-1).reshape(-1, 3)
    poses = torch.tensor([[0, 0, 0, 0, 0, 0, 1, 1],
                          [.01, .005, .008, 0, .015, 0, (1-.015**2)**.5, 1.2]], dtype=torch.float32)
    k = torch.tensor([[20, 0, 7.5], [0, 20, 7.5], [0, 0, 1]], dtype=torch.float32)
    args = (poses, torch.stack((points, points/.8)), torch.ones(2, height*width, 1)*5, k,
            torch.tensor([0, 1]), torch.tensor([1, 0]),
            torch.arange(height*width).repeat(2, 1),
            torch.ones(2, height*width, 1, dtype=torch.bool), torch.ones(2, height*width, 1)*4,
            height, width, 0, .01, 1., 10., 1., 1., iterations, 1e-8)
    return tuple(a.cuda().contiguous() if torch.is_tensor(a) else a for a in args)


def test_cuda_fixed_scales_and_pin_survive_multiple_iterations(backend):
    args = tiny_args()
    before = args[0].clone()
    targets = torch.tensor([1., .8], device="cuda")
    dx, norm = backend.gauss_newton_calib_fixed_scales(*args, targets)
    torch.cuda.synchronize()
    assert torch.equal(args[0][:, 7], targets)
    assert torch.equal(dx[:, 6], torch.zeros_like(dx[:, 6]))
    assert torch.equal(args[0][0], before[0])
    assert torch.isfinite(args[0]).all() and torch.isfinite(norm)
    assert not torch.equal(args[0][1, :7], before[1, :7])


def test_cuda_zero_iterations_changes_only_declared_scales(backend):
    args = tiny_args(iterations=0)
    before = args[0].clone()
    targets = torch.tensor([1., .8], device="cuda")
    dx, norm = backend.gauss_newton_calib_fixed_scales(*args, targets)
    assert torch.equal(args[0][:, :7], before[:, :7])
    assert torch.equal(args[0][:, 7], targets)
    assert torch.count_nonzero(dx) == 0 and norm.item() == 0


def test_cuda_bad_target_fails_before_pose_mutation(backend):
    args = tiny_args()
    before = args[0].clone()
    with pytest.raises(RuntimeError, match="positive"):
        backend.gauss_newton_calib_fixed_scales(*args, torch.tensor([1., 0.], device="cuda"))
    assert torch.equal(args[0], before)


def test_isolated_original_control_is_exact_on_synthetic_graph(backend):
    import mast3r_slam_backends
    first, second = tiny_args(), tiny_args()
    mast3r_slam_backends.gauss_newton_calib(*first)
    backend.gauss_newton_calib(*second)
    torch.cuda.synchronize()
    assert torch.equal(first[0], second[0])
