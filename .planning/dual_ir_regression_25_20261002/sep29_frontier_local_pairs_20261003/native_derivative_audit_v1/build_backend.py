"""Isolated hash-pinned normal-equation diagnostic; never replaces production."""
from pathlib import Path
import importlib.util

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pinned_fixed_scale_build", HERE.parent / "fixed_scale_native_backend_v1/build_backend.py")
pinned = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pinned)


def build_extension():
    pinned.verify_source_hashes()
    from torch.utils.cpp_extension import load
    build = HERE / "_torch_extension_build"
    build.mkdir(exist_ok=True)
    return load(name="mast3r_native_derivative_audit_v1",
                sources=[str(HERE / "inspect.cpp"), str(HERE / "inspect.cu")],
                extra_include_paths=[str(pinned.TOOL_ROOT / p) for p in
                                     ["mast3r_slam/backend/include", "mast3r_slam/backend/src", "thirdparty/eigen", "."]],
                build_directory=str(build), extra_cflags=["-O3"],
                extra_cuda_cflags=["-O3", "-gencode=arch=compute_120,code=sm_120", "-gencode=arch=compute_120,code=compute_120"],
                with_cuda=True)
