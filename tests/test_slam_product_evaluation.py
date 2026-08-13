import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from evaluate_slam_ground_truth import body_trajectory_to_camera, pose_errors
from analyze_depth_plane_constraint import (
    PlaneObservation,
    apply_temporal_gate,
    fit_plane_ransac,
    transform_plane_to_world,
)
from replay_db3_to_ros2 import select_db3
from build_stereo_replay_cache import STEREO_TOPICS, build_cache
from validate_slam_dataset_roles import validate_manifest
from validate_slam_product_release import validate_release


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


def test_depth_plane_ransac_rejects_outliers():
    generator = np.random.default_rng(3)
    xy = generator.uniform(-0.4, 0.4, size=(1000, 2))
    z = 0.6 + generator.normal(0.0, 0.001, size=1000)
    plane = np.column_stack((xy, z))
    outliers = generator.uniform(-0.5, 0.5, size=(200, 3))

    normal, offset, inliers = fit_plane_ransac(
        np.vstack((plane, outliers)), threshold_m=0.004
    )

    np.testing.assert_allclose(normal, [0.0, 0.0, 1.0], atol=0.01)
    assert abs(offset + 0.6) < 0.002
    assert inliers.mean() > 0.8


def test_world_plane_stays_fixed_during_real_vertical_motion():
    world_rotation_camera = Rotation.identity()
    world_offsets = []
    camera_distances = []
    for camera_height in (0.2, 0.35, 0.5):
        normal_camera = np.array([0.0, 0.0, 1.0])
        offset_camera = camera_height
        normal_world, offset_world = transform_plane_to_world(
            normal_camera,
            offset_camera,
            world_rotation_camera,
            np.array([0.0, 0.0, camera_height]),
        )
        world_offsets.append(offset_world)
        camera_distances.append(abs(offset_camera))
        np.testing.assert_allclose(normal_world, [0.0, 0.0, 1.0])

    np.testing.assert_allclose(world_offsets, 0.0, atol=1e-12)
    np.testing.assert_allclose(camera_distances, [0.2, 0.35, 0.5])


def test_replay_selects_largest_nonempty_db3(tmp_path):
    (tmp_path / "empty.db3").touch()
    (tmp_path / "small.db3").write_bytes(b"small")
    expected = tmp_path / "recording.db3"
    expected.write_bytes(b"complete recording")

    assert select_db3(tmp_path) == expected


def test_temporal_gate_rejects_stable_wall_for_z_constraint():
    observations = []
    for index in range(10):
        observation = PlaneObservation(
            epoch_s=float(index),
            relative_s=float(index),
            valid_points=500,
            inlier_ratio=0.5,
            median_residual_m=0.001,
            p95_residual_m=0.003,
            normal_camera=[1.0, 0.0, 0.0],
            offset_camera_m=-0.5,
            local_gate_pass=True,
            pose_matched=True,
            normal_world=[1.0, 0.0, 0.0],
            offset_world_m=-0.5,
        )
        observations.append(observation)

    report = apply_temporal_gate(
        observations,
        angle_gate_deg=4.0,
        offset_gate_m=0.025,
        max_horizontal_tilt_deg=12.0,
    )

    assert report["locally_matched_observations"] == 10
    assert report["horizontal_candidates"] == 0
    assert report["accepted_observations"] == 0


def test_stereo_replay_cache_preserves_only_required_topics(tmp_path):
    source = tmp_path / "source.db3"
    database = sqlite3.connect(source)
    database.execute(
        "CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT NOT NULL,"
        "type TEXT NOT NULL,serialization_format TEXT NOT NULL,"
        "offered_qos_profiles TEXT NOT NULL)"
    )
    database.execute(
        "CREATE TABLE messages(id INTEGER PRIMARY KEY,topic_id INTEGER NOT NULL,"
        "timestamp INTEGER NOT NULL,data BLOB NOT NULL)"
    )
    names = [*STEREO_TOPICS, "/device_0/sensor_0/Depth_0/image/data"]
    for topic_id, name in enumerate(names, 1):
        database.execute(
            "INSERT INTO topics VALUES(?,?,?,?,?)",
            (topic_id, name, "test/msg/Test", "cdr", ""),
        )
        database.execute(
            "INSERT INTO messages VALUES(?,?,?,?)",
            (topic_id, topic_id, topic_id * 10, name.encode()),
        )
    database.commit()
    database.close()

    output = tmp_path / "cache.db3"
    report = build_cache(source, output)

    assert report["result"] == "PASS"
    assert set(report["topics"]) == set(STEREO_TOPICS)
    copied = sqlite3.connect(output)
    copied_names = {row[0] for row in copied.execute("SELECT name FROM topics")}
    copied.close()
    assert copied_names == set(STEREO_TOPICS)


def test_dataset_manifest_detects_missing_hidden_test_without_breaking_dev_validation(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    (session / "acceptance.json").write_text("{}", encoding="utf-8")
    manifest = {
        "datasets": [
            {
                "id": "dev",
                "role": "development",
                "motion": "closed_loop_with_elevation",
                "session": "recordings/session",
                "external_ground_truth": None,
                "acceptance_sha256": hashlib.sha256(b"{}").hexdigest(),
            },
            {
                "id": "val",
                "role": "validation",
                "motion": "closed_loop_horizontal",
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


def test_product_release_gate_rejects_missing_action_matrix(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    acceptance = session / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    report = tmp_path / "run.json"
    report.write_text(
        json.dumps(
            {
                "result": "PASS",
                "pose_coverage": 1.0,
                "loop_input_drop_events": 0,
                "estimator_keyframe_queue_drop_events": 0,
                "automatic_loop_accepts": 0,
            }
        ),
        encoding="utf-8",
    )
    import hashlib

    manifest = {
        "datasets": [
            {
                "id": "only-one-action",
                "role": "validation",
                "motion": "straight_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(acceptance.read_bytes()).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            }
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dataset_module = sys.modules[validate_manifest.__module__]
    release_module = sys.modules[validate_release.__module__]
    old_dataset_root = dataset_module.ROOT
    old_release_root = release_module.ROOT
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        result = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root

    assert result["result"] == "FAIL"
    assert "closed_loop_horizontal" in result["dataset_gate"]["missing_motions"]
    assert result["release_readiness"] == "NOT_READY"


def test_candidate_gate_never_claims_customer_release(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    acceptance = session / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    report = tmp_path / "run.json"
    report.write_text(
        json.dumps(
            {
                "result": "PASS",
                "pose_coverage": 1.0,
                "loop_input_drop_events": 0,
                "estimator_keyframe_queue_drop_events": 0,
                "automatic_loop_accepts": 0,
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "datasets": [
            {
                "id": "candidate-development",
                "role": "development",
                "motion": "straight_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
            {
                "id": "candidate-validation",
                "role": "validation",
                "motion": "free_motion_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dataset_module = sys.modules[validate_manifest.__module__]
    release_module = sys.modules[validate_release.__module__]
    old_dataset_root = dataset_module.ROOT
    old_release_root = release_module.ROOT
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        result = validate_release(manifest_path, require_complete=False)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root

    assert result["result"] == "PASS"
    assert result["release_readiness"] == "CANDIDATE_PASS"
    assert result["customer_release_complete"] is False
    assert result["evaluation_scope"] == "candidate_evidence_only"


def test_hidden_dataset_requires_predeclared_loop_and_hashed_gt_report(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    acceptance = session / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    ground_truth = tmp_path / "truth.csv"
    ground_truth.write_text("t,x,y,z\n", encoding="utf-8")
    report = tmp_path / "run.json"
    report.write_text(
        json.dumps(
            {
                "result": "PASS",
                "pose_coverage": 1.0,
                "loop_input_drop_events": 0,
                "estimator_keyframe_queue_drop_events": 0,
                "automatic_loop_accepts": 0,
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "datasets": [
            {
                "id": "development",
                "role": "development",
                "motion": "free_motion_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
            {
                "id": "validation",
                "role": "validation",
                "motion": "l_shape_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
            {
                "id": "hidden",
                "role": "hidden_test",
                "motion": "straight_open",
                "session": "recordings/session",
                "expected_loop": None,
                "external_ground_truth": "truth.csv",
                "external_ground_truth_sha256": hashlib.sha256(
                    ground_truth.read_bytes()
                ).hexdigest(),
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dataset_module = sys.modules[validate_manifest.__module__]
    release_module = sys.modules[validate_release.__module__]
    old_dataset_root = dataset_module.ROOT
    old_release_root = release_module.ROOT
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        result = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root

    assert result["result"] == "FAIL"
    assert any("predeclare expected_loop" in item for item in result["failures"])
    assert any("ground-truth evaluation report" in item for item in result["failures"])
