"""UMI-only regression: graph must not manufacture motion during a long stop."""
import importlib.util
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


path = Path(__file__).resolve().parents[1] / "scripts/fuse_mast3r_stereo_imu.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
fusion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion)


def test_joint_graph_preserves_static_relative_motion_despite_cross_window_edges():
    times = np.linspace(0, 12, 121)
    positions = np.zeros((len(times), 3))
    positions[:, 0] = np.maximum(times - 8, 0) * 0.03
    # Consistent tiny observed motion is preserved, not replaced by a flat GT.
    positions[:, 1] = np.minimum(times, 8) * 0.00004
    observations = []
    for first, second in [(30, 90), (40, 100), (50, 110), (60, 120)]:
        delta = positions[second] - positions[first]
        delta[2] += 0.025  # biased cross-static-to-motion visual geometry
        observations.append({
            "accepted": True, "first_index": first, "second_index": second,
            "metric_displacement_camera_i_m": delta.tolist(),
            "pnp_inlier_ratio": 0.9,
        })
    imu_times = np.linspace(0, 12, 4801)
    refined, report = fusion.refine_positions_visual_inertial(
        positions, Rotation.identity(len(times)), observations, times,
        imu_times, np.zeros((len(imu_times), 3)),
        np.tile([0, 0, fusion.STANDARD_GRAVITY], (len(imu_times), 1)),
        np.eye(4), 0, node_stride=5,
        relative_motion_positions_body=positions.copy(),
        relative_motion_valid=np.ones(len(times), dtype=bool),
        correction_cap_mode="per-node",
    )
    static_correction = (refined - positions)[:71]
    assert np.linalg.norm(np.ptp(static_correction, axis=0)) < 0.001
    assert report["stationary_motion_guard"]["protected_frames"] >= 70
    assert refined[-1, 0] - refined[80, 0] > 0.10


def test_full_rate_guard_preserves_graph_static_motion_against_accel_bias():
    times = np.linspace(0, 6, 181)
    positions = np.zeros((len(times), 3))
    positions[:, 1] = times * 0.00004
    imu_times = np.linspace(0, 6, 2401)
    accel = np.tile([0.1, 0, fusion.STANDARD_GRAVITY], (len(imu_times), 1))
    refined, report = fusion.refine_positions_full_rate_imu(
        positions, Rotation.identity(len(times)), times, imu_times, accel,
        np.eye(4), 0, np.array([0, 0, -fusion.STANDARD_GRAVITY]),
        stationary_segments=[(0, len(times) - 1)],
    )
    assert np.linalg.norm(np.ptp(refined - positions, axis=0)) < 0.001
    assert report["stationary_motion_guard"]["protected_frames"] == len(times)
