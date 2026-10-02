import importlib.util
from pathlib import Path

import numpy as np
import pytest


path = Path(__file__).resolve().parents[1] / "scripts/fuse_mast3r_stereo_imu.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
fusion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion)


@pytest.mark.parametrize("mode", ["global", "per-node", "per-frame"])
def test_known_metric_translation_keeps_full_rate_curvature(mode):
    nodes = np.array([0, 2, 4])
    frames = np.arange(5)
    left, right, alpha = fusion.interpolation_stencil(frames, nodes)
    base = np.array([[0, 0, 0], [0.002, 0.001, 0], [0.004, 0, 0],
                     [0.006, -0.001, 0], [0.008, 0, 0]])
    corrected, requested, scale = fusion.cap_interpolated_position_corrections(
        np.zeros((3, 3)), left, right, alpha, 0.010, mode,
        base_correction=(base, base[nodes]),
    )
    np.testing.assert_allclose(corrected, base, atol=1e-12)
    np.testing.assert_allclose(requested, np.linalg.norm(base, axis=1))
    assert scale == 1.0


@pytest.mark.parametrize("mode", ["global", "per-node", "per-frame"])
def test_cap_applies_to_combined_metric_and_local_correction(mode):
    nodes = np.array([0, 2, 4])
    frames = np.arange(5)
    left, right, alpha = fusion.interpolation_stencil(frames, nodes)
    base = np.column_stack([np.linspace(0, 0.020, 5), np.zeros((5, 2))])
    corrected, requested, scale = fusion.cap_interpolated_position_corrections(
        np.zeros((3, 3)), left, right, alpha, 0.010, mode,
        base_correction=(base, base[nodes]),
    )
    assert max(np.linalg.norm(corrected, axis=1)) <= 0.010 + 1e-12
    assert max(requested) == pytest.approx(0.020)
    assert scale == pytest.approx(0.5)


def test_per_node_reports_extra_full_frame_clipping():
    nodes = np.array([0, 2])
    frames = np.arange(3)
    left, right, alpha = fusion.interpolation_stencil(frames, nodes)
    base = np.array([[0, 0, 0], [0.020, 0, 0], [0, 0, 0]])
    corrected, requested, scale = fusion.cap_interpolated_position_corrections(
        np.zeros((2, 3)), left, right, alpha, 0.010, "per-node",
        base_correction=(base, base[nodes]),
    )
    assert max(requested) == pytest.approx(0.020)
    assert max(np.linalg.norm(corrected, axis=1)) == pytest.approx(0.010)
    assert scale == pytest.approx(0.5)


def test_none_cap_allows_large_requested_correction_and_numeric_cap_scales():
    nodes = np.array([0, 1])
    frames = np.arange(2)
    left, right, alpha = fusion.interpolation_stencil(frames, nodes)
    node_corrections = np.array([[0.0, 0.0, 0.0], [0.100, 0.0, 0.0]])

    uncapped, requested, uncapped_scale = fusion.cap_interpolated_position_corrections(
        node_corrections, left, right, alpha, None, "global"
    )
    capped, capped_requested, capped_scale = fusion.cap_interpolated_position_corrections(
        node_corrections, left, right, alpha, 0.025, "global"
    )

    assert max(requested) == pytest.approx(0.100)
    np.testing.assert_allclose(capped_requested, requested)
    assert max(np.linalg.norm(uncapped, axis=1)) == pytest.approx(0.100)
    assert uncapped_scale == 1.0
    assert max(np.linalg.norm(capped, axis=1)) == pytest.approx(0.025)
    assert capped_scale == pytest.approx(0.25)


def test_cap_helper_validates_mode_and_numeric_limit_even_for_none_path():
    nodes = np.array([0, 1])
    frames = np.arange(2)
    left, right, alpha = fusion.interpolation_stencil(frames, nodes)
    node_corrections = np.zeros((2, 3))

    with pytest.raises(ValueError, match="unsupported correction cap mode"):
        fusion.cap_interpolated_position_corrections(
            node_corrections, left, right, alpha, None, "bad"
        )
    for limit in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite and positive"):
            fusion.cap_interpolated_position_corrections(
                node_corrections, left, right, alpha, limit, "global"
            )
