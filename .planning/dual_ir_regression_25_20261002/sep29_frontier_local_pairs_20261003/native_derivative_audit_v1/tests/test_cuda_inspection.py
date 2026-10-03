"""Actual CUDA contracts for isolated original normal-equation inspection."""
from pathlib import Path
import sys
import torch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_backend import build_extension
from derivatives import check_edge, retract_double

pytestmark = pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required")


@pytest.fixture(scope="module")
def backend():
    return build_extension()


def tiny_args():
    height = width = 16
    y, x = torch.meshgrid(torch.arange(height), torch.arange(width), indexing="ij")
    z = 1 + .02*x + .03*y + .01*torch.sin(x.float())
    points = torch.stack(((x-7.5)*z/20, (y-7.5)*z/20, z), dim=-1).reshape(-1, 3)
    poses = torch.tensor([[.1, -.2, .05, 0, .01, 0, (1-.01**2)**.5, 1.1],
                          [.11, -.195, .058, 0, .015, 0, (1-.015**2)**.5, 1.2]], dtype=torch.float32)
    k = torch.tensor([[20, 0, 7.5], [0, 20, 7.5], [0, 0, 1]], dtype=torch.float32)
    return (poses, torch.stack((points, points/.8)), torch.ones(2, height*width, 1)*5, k,
            torch.tensor([0]), torch.tensor([1]), torch.arange(height*width).reshape(1, -1),
            torch.ones(1, height*width, 1, dtype=torch.bool), torch.ones(1, height*width, 1)*4,
            height, width, 0, .01, 1., 10., 1., 1., 5, 1e-8)


def gpu_args(args):
    return tuple(v.cuda().contiguous() if torch.is_tensor(v) else v for v in args)


def test_native_derivatives_match_independent_fd_without_input_mutation(backend):
    args = tiny_args()
    gpu = gpu_args(args[:17])
    before = [v.clone() for v in gpu[:9]]
    H, g = backend.inspect_calibrated(*gpu)
    report = check_edge(args, H, g)
    assert report["derivative_agreement"], report
    assert all(torch.equal(v, saved) for v, saved in zip(gpu[:9], before))
    dx = torch.tensor([[.001, -.003, .002, .004, -.005, .002, .003]]*2)
    native = backend.inspect_retract(gpu[0], dx.cuda())
    assert torch.allclose(native.cpu().double(), retract_double(args[0], dx), atol=2e-7, rtol=2e-7)
    assert torch.equal(gpu[0], before[0])


def test_zero_support_and_invalid_indices_rejected(backend):
    args = list(gpu_args(tiny_args()[:17]))
    args[7].zero_()
    H, g = backend.inspect_calibrated(*args)
    assert torch.count_nonzero(H) == 0 and torch.count_nonzero(g) == 0
    args[7].fill_(True)
    args[6][0, 0] = 10000
    with pytest.raises(RuntimeError, match="out of bounds"):
        backend.inspect_calibrated(*args)
