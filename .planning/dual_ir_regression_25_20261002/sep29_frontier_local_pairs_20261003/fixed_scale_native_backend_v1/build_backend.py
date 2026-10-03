"""Build/load the isolated fixed-scale MASt3R native backend.

This diagnostic extension does not modify or install into the MASt3R-SLAM
toolchain.  It verifies pinned upstream source hashes, then builds a uniquely
named torch extension in a local build directory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


TOOL_ROOT = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
SOURCE_PATHS = {
    "gn_cpp": TOOL_ROOT / "mast3r_slam/backend/src/gn.cpp",
    "gn_kernels_cu": TOOL_ROOT / "mast3r_slam/backend/src/gn_kernels.cu",
    "gn_h": TOOL_ROOT / "mast3r_slam/backend/include/gn.h",
}
EXPECTED_SOURCE_SHA256 = {
    "gn_cpp": "af24cdba7e3084661afefd23ff9d68dee9e7509ed962f0f5117370d3b1e70c56",
    "gn_kernels_cu": "76dbac54f4f3e3c0839cd5f8aed1a3f5c2a3f19b88c23e7343323f843756d688",
    "gn_h": "e6a2f0c636086b0246e422f0e308aa1ba5ddf5e1b6b21525bcba3cc0f625e718",
}

MODULE_NAME = "mast3r_fixed_scale_backend_v1"
HERE = Path(__file__).resolve().parent
BUILD_DIR = HERE / "_torch_extension_build"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source_hashes() -> dict[str, dict[str, str]]:
    report: dict[str, dict[str, str]] = {}
    for label, path in SOURCE_PATHS.items():
        actual = sha256_file(path)
        expected = EXPECTED_SOURCE_SHA256[label]
        if actual != expected:
            raise RuntimeError(f"{label} hash mismatch: expected {expected}, got {actual} at {path}")
        report[label] = {"path": str(path), "sha256": actual}
    return report


def build_extension(*, verbose: bool = False) -> Any:
    verify_source_hashes()
    from torch.utils.cpp_extension import load

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    return load(
        name=MODULE_NAME,
        sources=[str(HERE / "fixed_scale.cpp"), str(HERE / "fixed_scale.cu")],
        extra_include_paths=[
            str(TOOL_ROOT / "mast3r_slam/backend/include"),
            str(TOOL_ROOT / "mast3r_slam/backend/src"),
            str(TOOL_ROOT / "thirdparty/eigen"),
            str(TOOL_ROOT),
        ],
        build_directory=str(BUILD_DIR),
        extra_cflags=["-O3"],
        extra_cuda_cflags=[
            "-O3",
            "-gencode=arch=compute_120,code=sm_120",
            "-gencode=arch=compute_120,code=compute_120",
        ],
        verbose=verbose,
        with_cuda=True,
    )


def provenance() -> dict[str, Any]:
    return {
        "schema": "mast3r_fixed_scale_backend_build_v1",
        "module_name": MODULE_NAME,
        "tool_root": str(TOOL_ROOT),
        "source_hashes": verify_source_hashes(),
        "diagnostic_only": True,
        "production_promoted": False,
        "external_ground_truth_used": False,
    }


if __name__ == "__main__":
    print(json.dumps(provenance(), indent=2, sort_keys=True))
