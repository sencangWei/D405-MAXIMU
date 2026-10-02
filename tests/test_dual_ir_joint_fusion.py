import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "dual_joint_fusion", ROOT / "scripts/fuse_mast3r_stereo_imu.py"
)
fusion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion)


def solve(secondary=None):
    times = np.linspace(0, 2, 21)
    truth = np.column_stack((0.1 * times, np.zeros((21, 2))))
    visual = truth.copy()
    visual[6:15, 1] += 0.015
    imu_times = np.linspace(0, 2, 801)
    stereo = [
        {
            "accepted": True, "first_index": i, "second_index": i + 2,
            "metric_displacement_camera_i_m": [0.02, 0, 0],
            "pnp_inlier_ratio": 0.5,
        }
        for i in range(0, 19, 2)
    ]
    factors = None if secondary is None else [
        {
            "first_index": i, "second_index": i + 6,
            "metric_displacement_world_m": (truth[i + 6] - truth[i]).tolist(),
            "confidence": secondary,
        }
        for i in range(15)
    ]
    refined, report = fusion.refine_positions_visual_inertial(
        visual, Rotation.identity(21), stereo, times, imu_times,
        np.zeros((801, 3)), np.tile([0, 0, 9.80665], (801, 1)),
        np.eye(4), td_s=0, node_stride=2, secondary_visual_factors=factors,
    )
    return refined, truth, report


def test_secondary_local_factors_reduce_primary_shape_error():
    baseline, truth, _ = solve()
    dual, _, report = solve(1.0)
    assert np.max(np.linalg.norm(dual - truth, axis=1)) < np.max(
        np.linalg.norm(baseline - truth, axis=1)
    )
    assert report["secondary_visual_motion"]["edges"] == 15
    assert report["secondary_visual_motion"]["rmse_after_m"] < report[
        "secondary_visual_motion"
    ]["rmse_before_m"]
    assert report["secondary_visual_motion"]["external_ground_truth_used"] is False


def test_missing_secondary_preserves_existing_solver_and_zero_confidence_has_no_influence():
    baseline, _, baseline_report = solve()
    unavailable, _, report = solve(0.0)
    np.testing.assert_allclose(unavailable, baseline, atol=1e-9)
    assert "secondary_visual_motion" not in baseline_report
    assert report["secondary_visual_motion"]["prior_confidence_median"] == 0


def test_secondary_frontend_is_explicitly_limited_to_joint_left_ir():
    args = SimpleNamespace(
        secondary_right_trajectory=Path("right.csv"),
        stream="infrared_right", position_mode="keyframe-graph",
    )
    with pytest.raises(ValueError, match="left-IR joint"):
        fusion.prepare_secondary_visual_inputs(args, None, None, None, None, None, None, None)


def test_no_secondary_does_not_read_any_additional_sensor_product():
    assert fusion.prepare_secondary_visual_inputs(
        SimpleNamespace(), None, None, None, None, None, None, None
    ) == (None, None)
