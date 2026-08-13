import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from evaluate_slam_ground_truth import body_trajectory_to_camera, pose_errors
from validate_slam_dataset_roles import validate_manifest


def test_pose_errors_remove_only_rigid_alignment_not_scale():
    gt = np.column_stack((np.linspace(0, 1, 100), np.zeros(100), np.zeros(100)))
    rotation = Rotation.from_euler("z", 30, degrees=True)
    estimate = (gt * 1.1) @ rotation.as_matrix().T + np.array([2.0, -3.0, 0.4])
    quaternions = np.tile(rotation.as_quat(), (100, 1))
    gt_quaternions = np.tile(Rotation.identity().as_quat(), (100, 1))

    metrics = pose_errors(estimate, quaternions, gt, gt_quaternions, delta=10)

    assert metrics["ate_translation_rmse_m"] > 0.02
    assert metrics["endpoint_drift_percent_of_path"] > 9.0


def test_pose_errors_report_position_and_attitude_alignment_separately():
    gt = np.column_stack(
        (
            np.linspace(0, 1, 100),
            0.2 * np.sin(np.linspace(0, 4, 100)),
            0.1 * np.cos(np.linspace(0, 3, 100)),
        )
    )
    world_offset = Rotation.from_euler("xyz", [3.0, -1.0, 12.0], degrees=True)
    estimate = gt @ world_offset.as_matrix().T + np.array([2.0, -3.0, 0.4])
    gt_attitude = Rotation.from_euler(
        "zyx",
        np.column_stack(
            (
                np.linspace(0, 30, 100),
                np.linspace(-5, 4, 100),
                np.linspace(2, -3, 100),
            )
        ),
        degrees=True,
    )
    estimate_attitude = world_offset * gt_attitude

    metrics = pose_errors(
        estimate,
        estimate_attitude.as_quat(),
        gt,
        gt_attitude.as_quat(),
        delta=10,
    )

    assert metrics["attitude_aligned_ate_rotation_rmse_deg"] < 1e-10
    assert metrics["position_vs_attitude_alignment_rotation_deg"] < 1e-10


def test_body_trajectory_to_camera_applies_rotating_lever_arm():
    positions = np.zeros((2, 3))
    body_rotation = Rotation.from_euler("z", [0.0, 90.0], degrees=True)
    body_t_camera = np.eye(4)
    body_t_camera[0, 3] = 0.1

    camera_positions, camera_quaternions = body_trajectory_to_camera(
        positions, body_rotation.as_quat(), body_t_camera
    )

    np.testing.assert_allclose(camera_positions[0], [0.1, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(camera_positions[1], [0.0, 0.1, 0.0], atol=1e-12)
    np.testing.assert_allclose(
        Rotation.from_quat(camera_quaternions).as_matrix(),
        body_rotation.as_matrix(),
        atol=1e-12,
    )


def test_dataset_manifest_detects_missing_hidden_test_without_breaking_dev_validation(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    (session / "acceptance.json").write_text("{}", encoding="utf-8")
    import hashlib

    manifest = {
        "datasets": [
            {
                "id": "dev",
                "role": "development",
                "session": "recordings/session",
                "external_ground_truth": None,
                "acceptance_sha256": hashlib.sha256(b"{}").hexdigest(),
            },
            {
                "id": "val",
                "role": "validation",
                "session": "recordings/session",
                "external_ground_truth": None,
                "acceptance_sha256": hashlib.sha256(b"{}").hexdigest(),
            },
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    old_root = sys.modules[validate_manifest.__module__].ROOT
    sys.modules[validate_manifest.__module__].ROOT = tmp_path
    try:
        assert validate_manifest(manifest_path, False)["result"] == "PASS"
        strict = validate_manifest(manifest_path, True)
    finally:
        sys.modules[validate_manifest.__module__].ROOT = old_root
    assert strict["result"] == "FAIL"
    assert strict["product_test_readiness"] == "MISSING_HIDDEN_TEST"
