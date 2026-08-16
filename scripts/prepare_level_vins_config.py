#!/usr/bin/env python3
"""Pair a passed IMU level rotation with both VINS camera extrinsics."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.vins_transform import (
    DEFAULT_VINS_IMU_ROTATION,
    load_vins_imu_rotation,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_opencv_matrix(path: Path, key: str) -> np.ndarray:
    storage = cv2.FileStorage(str(path), cv2.FILE_STORAGE_READ)
    try:
        matrix = storage.getNode(key).mat()
    finally:
        storage.release()
    if matrix is None or matrix.shape != (4, 4):
        raise ValueError(f"{path}缺少4x4矩阵{key}")
    return np.asarray(matrix, dtype=np.float64)


def replace_opencv_matrix(text: str, key: str, matrix: np.ndarray) -> str:
    pattern = re.compile(
        rf"({re.escape(key)}:\s*!!opencv-matrix\s*\n"
        rf"\s*rows:\s*4\s*\n\s*cols:\s*4\s*\n\s*dt:\s*d\s*\n\s*data:\s*\[)(.*?)(\])",
        re.DOTALL,
    )
    values = ", ".join(f"{value:.10f}" for value in matrix.reshape(-1))
    replaced, count = pattern.subn(rf"\g<1> {values} \g<3>", text, count=1)
    if count != 1:
        raise ValueError(f"无法替换{key}")
    return replaced


def prepare(base: Path, level: Path, out_dir: Path) -> tuple[Path, Path]:
    new_rotation = load_vins_imu_rotation(level)
    correction = new_rotation @ DEFAULT_VINS_IMU_ROTATION.T
    text = base.read_text(encoding="utf-8")
    transformed: dict[str, list[list[float]]] = {}
    for key in ("body_T_cam0", "body_T_cam1"):
        matrix = load_opencv_matrix(base, key)
        candidate = matrix.copy()
        candidate[:3, :3] = correction @ matrix[:3, :3]
        candidate[:3, 3] = correction @ matrix[:3, 3]
        text = replace_opencv_matrix(text, key, candidate)
        transformed[key] = candidate.tolist()

    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / base.name
    config_path.write_text(text, encoding="utf-8")
    for calibration_name in ("left.yaml", "right.yaml"):
        shutil.copy2(base.parent / calibration_name, out_dir / calibration_name)
    manifest = {
        "result": "PASS",
        "base_config": str(base.resolve()),
        "base_config_sha256": sha256(base),
        "level_calibration": str(level.resolve()),
        "level_calibration_sha256": sha256(level),
        "old_R_level_from_imu": DEFAULT_VINS_IMU_ROTATION.tolist(),
        "new_R_level_from_imu": new_rotation.tolist(),
        "left_correction": correction.tolist(),
        "body_transforms": transformed,
        "paired_contract": "the same new_R_level_from_imu must be used by IMU publisher",
    }
    manifest_path = out_dir / "level_vins_config_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return config_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--level", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    config, manifest = prepare(args.base, args.level, args.out_dir)
    print(config.resolve())
    print(manifest.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
