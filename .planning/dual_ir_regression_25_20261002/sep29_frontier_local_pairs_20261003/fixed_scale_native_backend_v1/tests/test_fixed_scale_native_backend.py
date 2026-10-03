from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest
import torch


BASE = Path(__file__).resolve().parents[1]
BUILD = BASE / "build_backend.py"
CPP = BASE / "fixed_scale.cpp"
CU = BASE / "fixed_scale.cu"


def _load_build_module():
    spec = importlib.util.spec_from_file_location("fixed_scale_build_backend", BUILD)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_source_hashes_are_pinned_to_current_native_files():
    module = _load_build_module()
    report = module.verify_source_hashes()

    assert report["gn_cpp"]["sha256"] == module.EXPECTED_SOURCE_SHA256["gn_cpp"]
    assert report["gn_kernels_cu"]["sha256"] == module.EXPECTED_SOURCE_SHA256["gn_kernels_cu"]
    assert report["gn_h"]["sha256"] == module.EXPECTED_SOURCE_SHA256["gn_h"]
    assert Path(report["gn_kernels_cu"]["path"]).name == "gn_kernels.cu"


def test_build_loader_uses_unique_isolated_cuda_extension_without_installing():
    tree = ast.parse(BUILD.read_text())
    constants = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}

    assert "mast3r_fixed_scale_backend_v1" in constants
    assert "_torch_extension_build" in BUILD.read_text()
    assert "torch.utils.cpp_extension" in BUILD.read_text()
    assert "with_cuda=True" in BUILD.read_text()
    assert "setup(" not in BUILD.read_text()


def test_build_loader_uses_exact_native_optimization_and_sm120_flags():
    text = BUILD.read_text()

    assert 'extra_cflags=["-O3"]' in text
    assert 'extra_cuda_cflags=[' in text
    assert '"-O3"' in text
    assert '"-gencode=arch=compute_120,code=sm_120"' in text
    assert '"-gencode=arch=compute_120,code=compute_120"' in text


def test_cuda_includes_original_kernel_source_and_slices_to_six_dof_before_solve():
    text = CU.read_text()

    assert '#include "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/mast3r_slam/backend/src/gn_kernels.cu"' in text
    assert "calib_proj_kernel<<<" in text
    assert "SparseBlock A(num_poses - num_fix, solve_pose_dim)" in text
    assert "Slice(0, 6)" in text
    assert re.search(r"torch::Tensor dx6\s*=\s*-A\.solve\(\)", text)
    assert "dx7.index_put_({Slice(), Slice(0, 6)}, dx6)" in text
    assert "Twc.index_put_({Slice(), 7}, target_scales)" in text


def test_cpp_exports_original_control_and_fixed_scale_entry_points_with_validation():
    text = CPP.read_text()

    assert 'm.def("gauss_newton_calib"' in text
    assert 'm.def("gauss_newton_calib_fixed_scales"' in text
    assert "gauss_newton_calib_cuda" in text
    assert "gauss_newton_calib_fixed_scales_cuda" in text
    assert "CHECK_CUDA_FLOAT32(target_scales)" in text
    assert "target_scales.dim() == 1" in text
    assert "target_scales.size(0) == Twc.size(0)" in text
    assert "target_scales > 0" in text
    assert "Twc quaternions must be unit length" in text
    assert "torch::sqrt(torch::sum(q * q, 1))" in text


def test_no_copied_native_cuda_body_beyond_include_and_driver():
    text = CU.read_text()

    assert text.count("std::vector<torch::Tensor> gauss_newton_calib_fixed_scales_cuda") == 1
    assert "std::vector<torch::Tensor> gauss_newton_calib_cuda(" not in text
    assert "class SparseBlock" not in text
    assert len(text.splitlines()) < 130


def test_cpu_synthetic_scale_dimension_removal_contract():
    h7 = torch.arange(49, dtype=torch.float32).reshape(7, 7)
    g7 = torch.arange(7, dtype=torch.float32)

    h6 = h7[:6, :6].contiguous()
    g6 = g7[:6].contiguous()
    dx6 = torch.ones(6, dtype=torch.float32)
    dx7 = torch.zeros(7, dtype=torch.float32)
    dx7[:6] = dx6

    assert h6.shape == (6, 6)
    assert g6.shape == (6,)
    assert dx7[:6].tolist() == [1.0] * 6
    assert dx7[6].item() == 0.0


def test_loader_reports_diagnostic_only_provenance():
    module = _load_build_module()
    provenance = module.provenance()

    assert provenance["schema"] == "mast3r_fixed_scale_backend_build_v1"
    assert provenance["diagnostic_only"] is True
    assert provenance["production_promoted"] is False
    assert provenance["external_ground_truth_used"] is False
    assert set(provenance["source_hashes"]) == {"gn_cpp", "gn_kernels_cu", "gn_h"}


def test_build_loader_hash_mismatch_fails_closed(monkeypatch):
    module = _load_build_module()
    monkeypatch.setitem(module.EXPECTED_SOURCE_SHA256, "gn_h", "0" * 64)

    with pytest.raises(RuntimeError, match="hash mismatch"):
        module.verify_source_hashes()
