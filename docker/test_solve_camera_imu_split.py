#!/usr/bin/env python3
"""Regression tests for the host-side split Kalibr path boundary."""

from pathlib import Path

import pytest

from solve_camera_imu_split import map_container_path


def test_aprilgrid_runtime_aliases_map_to_frozen_host_asset(tmp_path):
    host_data_root = tmp_path / "data"
    target_asset = tmp_path / "assets/aprilgrid_6x6_35mm.yaml"

    aliases = (
        "/home/robot/ego_vio_humble/config/aprilgrid_6x6_35mm.yaml",
        (
            "/home/robot/releases/ego_vio_humble/product_v1_20260824/"
            "config/aprilgrid_6x6_35mm.yaml"
        ),
    )

    for alias in aliases:
        assert map_container_path(alias, host_data_root, target_asset) == target_asset


def test_unapproved_container_path_remains_rejected(tmp_path):
    with pytest.raises(ValueError, match="清单含未授权的容器路径"):
        map_container_path(
            "/home/robot/releases/ego_vio_humble/untrusted/config/target.yaml",
            tmp_path / "data",
            tmp_path / "assets/aprilgrid_6x6_35mm.yaml",
        )
