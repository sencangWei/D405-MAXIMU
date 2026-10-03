import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
path = ROOT / "scripts" / "derive_right_ir_stereo_scale.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
right_stereo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(right_stereo)

imu_path = ROOT / "scripts" / "align_mast3r_scale_with_imu.py"
imu_spec = importlib.util.spec_from_file_location(imu_path.stem, imu_path)
imu_scale = importlib.util.module_from_spec(imu_spec)
imu_spec.loader.exec_module(imu_scale)

fusion_path = ROOT / "scripts" / "fuse_mast3r_stereo_imu.py"
fusion_spec = importlib.util.spec_from_file_location(fusion_path.stem, fusion_path)
fusion = importlib.util.module_from_spec(fusion_spec)
fusion_spec.loader.exec_module(fusion)

complementary_path = ROOT / "scripts" / "fuse_docker2_mast3r_complementary.py"
complementary_spec = importlib.util.spec_from_file_location(
    complementary_path.stem, complementary_path
)
complementary = importlib.util.module_from_spec(complementary_spec)
complementary_spec.loader.exec_module(complementary)


def test_right_eye_observation_uses_rigid_baseline_and_right_motion():
    rotation = Rotation.from_euler("z", 20, degrees=True)
    left_displacement = np.array([0.1, 0.0, 0.0])
    baseline = np.array([-0.018, 0.0, 0.0])
    left_translation = -rotation.apply(left_displacement)
    right_translation = baseline + left_translation - rotation.apply(baseline)
    right_displacement = -rotation.inv().apply(right_translation)
    observation = {
        "accepted": True,
        "first_t_sec": 1.0,
        "second_t_sec": 2.0,
        "first_index": 0,
        "second_index": 1,
        "metric_displacement_camera_i_m": left_displacement.tolist(),
        "metric_displacement_frame": "infrared_left_camera_i",
        "pnp_rotation_quaternion_xyzw": rotation.as_quat().tolist(),
        "pnp_inlier_ratio": 0.9,
    }
    times = np.array([1.0, 1.5, 2.0])
    positions = np.array([np.zeros(3), np.zeros(3), right_displacement / 0.25])
    quaternions = Rotation.from_quat(
        np.array([[0, 0, 0, 1], [0, 0, 0, 1], rotation.inv().as_quat()])
    )

    converted = right_stereo.convert_observation(
        observation, times, positions, quaternions, Rotation.identity(), baseline
    )

    assert converted["accepted"] is True
    assert converted["second_index"] == 2
    assert converted["metric_displacement_frame"] == "infrared_right_camera_i"
    np.testing.assert_allclose(converted["metric_displacement_camera_i_m"], right_displacement)
    assert converted["scale"] == pytest.approx(0.25)


def test_right_eye_observation_rejects_missing_corresponding_pose():
    observation = {
        "accepted": True,
        "first_t_sec": 1.0,
        "second_t_sec": 2.0,
        "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
        "metric_displacement_frame": "infrared_left_camera_i",
        "pnp_rotation_quaternion_xyzw": [0, 0, 0, 1],
    }
    converted = right_stereo.convert_observation(
        observation,
        np.array([1.0, 2.04]),
        np.array([[0, 0, 0], [0.4, 0, 0]]),
        Rotation.identity(2),
        Rotation.identity(),
        np.array([-0.018, 0, 0]),
    )
    assert converted["accepted"] is False
    assert converted["reason"] == "missing_right_pose"


def test_right_eye_imu_uses_factory_extrinsic_instead_of_left_camera_origin():
    body_t_left = np.eye(4)
    body_t_left[:3, 3] = [-0.01, -0.02, 0.03]
    stereo_report = {
        "observation_frame": "infrared_right_camera_i",
        "factory_stereo_calibration": {
            "right_rotation_from_left": np.eye(3).tolist(),
            "right_translation_from_left_m": [-0.018, 0.0, 0.0],
        },
    }

    body_t_right = imu_scale.body_t_camera_for_stream(
        body_t_left, "infrared_right", stereo_report
    )

    np.testing.assert_allclose(body_t_right[:3, 3], [0.008, -0.02, 0.03])
    np.testing.assert_allclose(
        imu_scale.body_t_camera_for_stream(body_t_left, "infrared_left", None),
        body_t_left,
    )
    np.testing.assert_allclose(
        fusion.body_t_trajectory_camera_from_stereo_report(body_t_left, stereo_report),
        body_t_right,
    )
    graph_report = {
        "result": "PASS",
        "inputs": {"trajectory": "/tmp/right-input.csv"},
        "camera_extrinsics": {
            "vins_body_T_left_ir": body_t_left.tolist(),
            "effective_body_T_trajectory_camera": body_t_right.tolist(),
            "trajectory_observation_frame": "infrared_right_camera_i",
        },
        "output": "/tmp/right-graph.csv",
    }
    np.testing.assert_allclose(
        complementary.body_t_camera_from_graph_report(
            body_t_left, graph_report, Path("/tmp/right-graph.csv")
        ),
        body_t_right,
    )
    with pytest.raises(ValueError, match="different graph trajectory"):
        complementary.body_t_camera_from_graph_report(
            body_t_left, graph_report, Path("/tmp/wrong.csv")
        )


def _minimal_left_report(tmp_path: Path) -> Path:
    path = tmp_path / "left_report.json"
    path.write_text(
        json.dumps(
            {
                "result": "PASS",
                "observation_frame": "infrared_left_camera_i",
                "db3": str(tmp_path / "input.db3"),
                "factory_stereo_calibration": {"baseline_m": 0.018},
                "observations": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _patch_main_dependencies(monkeypatch, *, continuity, robust_error=None):
    monkeypatch.setattr(
        right_stereo,
        "load_stereo_calibration",
        lambda _path: {
            "baseline_m": 0.018,
            "right_rotation_from_left": np.eye(3),
            "right_translation_from_left_m": np.array([-0.018, 0.0, 0.0]),
        },
    )
    monkeypatch.setattr(
        right_stereo,
        "load_trajectory",
        lambda _path: (
            np.array([1.0, 2.0]),
            np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
            np.array([[0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, 1.0]]),
        ),
    )
    if robust_error is None:
        monkeypatch.setattr(right_stereo, "robust_scale", lambda *_args, **_kwargs: (0.25, {"ok": True}))
    else:
        def fail_scale(*_args, **_kwargs):
            raise ValueError(robust_error)
        monkeypatch.setattr(right_stereo, "robust_scale", fail_scale)
    monkeypatch.setattr(right_stereo, "trajectory_step_continuity", lambda *_args, **_kwargs: continuity)


def _run_main(monkeypatch, tmp_path: Path) -> tuple[int, dict]:
    left = _minimal_left_report(tmp_path)
    output = tmp_path / "right_report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "derive_right_ir_stereo_scale.py",
            "--left-stereo-report",
            str(left),
            "--right-trajectory",
            str(tmp_path / "right.csv"),
            "--output",
            str(output),
        ],
    )
    rc = right_stereo.main()
    return rc, json.loads(output.read_text(encoding="utf-8"))


def test_main_fails_when_right_trajectory_continuity_fails(monkeypatch, tmp_path):
    _patch_main_dependencies(
        monkeypatch,
        continuity={
            "result": "FAIL",
            "reason": "isolated_position_step_jump",
            "jump_count": 3,
            "max_step_m": 0.0637092,
        },
    )

    rc, report = _run_main(monkeypatch, tmp_path)

    assert rc == 2
    assert report["result"] == "FAIL"
    assert report["failures"] == ["isolated_position_step_jump"]
    assert report["scale_m_per_mast3r_unit"] == pytest.approx(0.25)
    assert report["trajectory_continuity"]["jump_count"] == 3


def test_main_passes_when_scale_and_continuity_pass(monkeypatch, tmp_path):
    _patch_main_dependencies(monkeypatch, continuity={"result": "PASS", "reason": None})

    rc, report = _run_main(monkeypatch, tmp_path)

    assert rc == 0
    assert report["result"] == "PASS"
    assert report["failures"] == []
    assert report["trajectory_continuity"] == {"result": "PASS", "reason": None}


def test_main_scale_unobservable_remains_fail_without_continuity(monkeypatch, tmp_path):
    _patch_main_dependencies(monkeypatch, continuity=None, robust_error="not enough observations")

    rc, report = _run_main(monkeypatch, tmp_path)

    assert rc == 2
    assert report["result"] == "FAIL"
    assert report["failures"] == ["right_stereo_scale_unobservable"]
    assert report["trajectory_continuity"] is None
