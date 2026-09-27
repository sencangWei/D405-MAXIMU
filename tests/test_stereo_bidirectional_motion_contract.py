import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "align_mast3r_scale_with_stereo.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
stereo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stereo)


def observation(
    displacement,
    *,
    scale=1.0,
    rotation=Rotation.identity(),
    accepted=True,
):
    displacement = np.asarray(displacement, dtype=float)
    return {
        "accepted": accepted,
        "scale": scale,
        "metric_distance_m": float(np.linalg.norm(displacement)),
        "metric_displacement_camera_i_m": displacement.tolist(),
        "pnp_rotation_quaternion_xyzw": rotation.as_quat().tolist(),
        "pnp_inlier_ratio": 0.8,
        "rotation_error_deg": 0.2,
        "method": "sift",
    }


def test_bidirectional_motion_maps_reverse_through_forward_rotation():
    forward_rotation = Rotation.from_euler("z", 90, degrees=True)
    forward = observation([0.10, 0.0, 0.0], rotation=forward_rotation)
    reverse = observation(-forward_rotation.apply([0.10, 0.0, 0.0]))

    validated = stereo.validate_bidirectional_motion(forward, reverse)

    assert validated["accepted"] is True
    assert validated["scale"] == forward["scale"]
    assert validated["metric_displacement_camera_i_m"] == forward[
        "metric_displacement_camera_i_m"
    ]
    assert validated["bidirectional_motion_closure_mm"] == pytest.approx(0.0)
    assert validated["bidirectional_relative_vector_disagreement"] == pytest.approx(0.0)
    np.testing.assert_allclose(
        validated["mapped_reverse_metric_displacement_camera_i_m"],
        [0.10, 0.0, 0.0],
        atol=1e-12,
    )


def test_bidirectional_motion_rejects_same_scale_inconsistent_vectors():
    forward = observation([0.10, 0.0, 0.0], scale=1.0)
    reverse = observation([0.0, -0.10, 0.0], scale=1.0)

    validated = stereo.validate_bidirectional_motion(forward, reverse)

    assert validated["accepted"] is False
    assert validated["reason"] == "bidirectional_motion_vector_disagrees"
    assert validated["forward_scale"] == pytest.approx(1.0)
    assert validated["reverse_scale"] == pytest.approx(1.0)
    assert validated["bidirectional_motion_closure_mm"] > 8.0
    assert validated["bidirectional_relative_vector_disagreement"] > 0.20


def test_bidirectional_motion_allows_small_absolute_noise_even_when_relative_large():
    forward = observation([0.004, 0.0, 0.0])
    reverse = observation([-0.004, -0.003, 0.0])

    validated = stereo.validate_bidirectional_motion(forward, reverse)

    assert validated["accepted"] is True
    assert validated["bidirectional_motion_closure_mm"] == pytest.approx(3.0)
    assert validated["bidirectional_relative_vector_disagreement"] > 0.20


def test_bidirectional_motion_rejects_missing_reverse():
    forward = observation([0.10, 0.0, 0.0])
    reverse = {"accepted": False, "reason": "missing_cached_reverse"}

    validated = stereo.validate_bidirectional_motion(forward, reverse)

    assert validated["accepted"] is False
    assert validated["reason"] == "reverse_motion_failed"
    assert validated["reverse_failure_reason"] == "missing_cached_reverse"


def test_bidirectional_motion_fails_closed_when_forward_rotation_missing():
    forward = observation([0.10, 0.0, 0.0])
    forward.pop("pnp_rotation_quaternion_xyzw")
    reverse = observation([-0.10, 0.0, 0.0])

    validated = stereo.validate_bidirectional_motion(forward, reverse)

    assert validated["accepted"] is False
    assert validated["reason"] == "bidirectional_motion_contract_missing"


@pytest.mark.parametrize(
    "bad_vector",
    ([np.nan, 0.0, 0.0], [0.0, 0.0], [0.0, 0.0, 0.0, 0.0]),
)
def test_bidirectional_motion_invalid_forward_vector_raises(bad_vector):
    forward = observation([0.10, 0.0, 0.0])
    forward["metric_displacement_camera_i_m"] = bad_vector
    reverse = observation([-0.10, 0.0, 0.0])

    with pytest.raises(ValueError, match="forward metric displacement"):
        stereo.validate_bidirectional_motion(forward, reverse)


@pytest.mark.parametrize(
    "bad_vector",
    ([np.nan, 0.0, 0.0], [0.0, 0.0], [0.0, 0.0, 0.0, 0.0]),
)
def test_bidirectional_motion_fails_closed_on_invalid_reverse_vector(bad_vector):
    forward = observation([0.10, 0.0, 0.0])
    reverse = observation([-0.10, 0.0, 0.0])
    reverse["metric_displacement_camera_i_m"] = bad_vector

    validated = stereo.validate_bidirectional_motion(forward, reverse)

    assert validated["accepted"] is False
    assert validated["reason"] == "bidirectional_motion_contract_malformed"


def test_combine_bidirectional_scale_default_remains_scalar_only():
    forward = observation([0.10, 0.0, 0.0], scale=1.0)
    reverse = observation([0.0, -0.10, 0.0], scale=1.0)

    combined = stereo.combine_bidirectional_scale(forward, reverse)

    assert combined["accepted"] is True
    assert combined["scale_estimator"] == "bidirectional_pnp_weighted_mean"
