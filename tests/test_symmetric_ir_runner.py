import importlib.util
import argparse
from pathlib import Path
import sys
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("symmetric_runner", ROOT / "scripts/fuse_mast3r_dual_ir_symmetric.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_factory_schema_addition_is_not_a_physical_calibration_change():
    legacy = {"baseline_m": 0.018083254, "left_intrinsics": {"fx": 649.207}}
    current = {**legacy, "right_rotation_from_left": np.eye(3).tolist()}
    runner.validate_factory_calibration(legacy, current)
    with pytest.raises(ValueError, match="different factory calibration"):
        runner.validate_factory_calibration(legacy, {**current, "baseline_m": 0.018})
    with pytest.raises(ValueError, match="rotation is invalid"):
        runner.validate_factory_calibration(legacy, {**current, "right_rotation_from_left": np.zeros((3, 3)).tolist()})


def test_factory_rotation_mismatch_is_rejected_when_both_sources_have_it():
    left = {"baseline_m": 0.018083254, "right_rotation_from_left": np.eye(3).tolist()}
    right = {**left, "right_rotation_from_left": np.diag([-1, -1, 1]).tolist()}
    with pytest.raises(ValueError, match="different factory rotation"):
        runner.validate_factory_calibration(left, right)


def test_reference_uses_recorded_camera_times_not_extrapolated_vins_tail(tmp_path):
    path = tmp_path / "frames.csv"
    path.write_text("infrared_left_device_ms,infrared_left_mono\n"
                    "1000000,20\n1000033,20.033\n1000066,20.066\n1000099,20.099\n")
    times = np.array([1000, 1000.033, 1000.066, 1000.0990002, 1000.132])
    rows = [{"t_sec": str(t)} for t in times]
    bound_times, _, _, _, mono, report = runner.bind_body_reference(
        path, times, np.zeros((5, 3)), Rotation.identity(5), rows
    )
    np.testing.assert_allclose(bound_times, [1000, 1000.033, 1000.066, 1000.099])
    np.testing.assert_allclose(mono, [20, 20.033, 20.066, 20.099])
    assert report["unbound_timestamps"] == [1000.132]
    assert report["unbound_samples"] == 1


def test_ablation_policy_defaults_and_validation():
    defaults = runner.symmetric_policy(SimpleNamespace())
    assert defaults == {
        "max_correction_m": 1.0,
        "max_correction_mm": 1000.0,
        "correction_cap_mode": "global",
        "eyes": "both",
        "stereo_weight_policy": "observation",
        "disable_learned_motion": False,
        "learned_motion_consistency_limit_m": None,
    }
    assert runner.parse_optional_correction_mm("none") is None
    assert runner.parse_optional_correction_mm("25") == pytest.approx(0.025)
    with pytest.raises(argparse.ArgumentTypeError):
        runner.parse_optional_correction_mm("nan")
    with pytest.raises(ValueError, match="unsupported correction cap mode"):
        runner.symmetric_policy(SimpleNamespace(correction_cap_mode="bad"))
    with pytest.raises(ValueError, match="unsupported eye policy"):
        runner.symmetric_policy(SimpleNamespace(eyes="middle"))
    with pytest.raises(ValueError, match="unsupported stereo weight policy"):
        runner.symmetric_policy(SimpleNamespace(stereo_weight_policy="bad"))


def test_wrapper_outputs_body_poses_and_never_marks_experiment_accepted(tmp_path, monkeypatch):
    session, vins_dir = tmp_path / "session", tmp_path / "vins"
    (session / "external_imu").mkdir(parents=True)
    vins_dir.mkdir()
    times = 1000 + np.arange(21) / 30
    mono = times - 1000
    truth = np.column_stack((0.1 * mono, np.zeros((21, 2))))
    pose_rows = [{"t_sec": str(t), **dict(zip(("x", "y", "z"), p)),
                  "qw": "1", "qx": "0", "qy": "0", "qz": "0"}
                 for t, p in zip(times, truth)]
    runner.fusion.write_trajectory(vins_dir / "vio_corrected_stream.csv", pose_rows, truth, Rotation.identity(21))
    (session / "d405_frames.csv").write_text(
        "infrared_left_device_ms,infrared_left_mono\n" + "".join(
            f"{t * 1000:.6f},{m:.9f}\n" for t, m in zip(times, mono)
        )
    )
    (session / "external_imu/imu.bin").write_bytes(b"fixture")
    source = {"schema": "umi_docker2_run_acceptance_v1", "result": "PASS",
              "slam_supervision": False, "external_ground_truth_used": False,
              "session": str(session), "corrected_trajectory": str(vins_dir / "vio_corrected_stream.csv"),
              "corrected_odometry_samples": 21}
    (vins_dir / "run_acceptance.json").write_text(json.dumps(source))
    for name in ("vins.yaml", "imu.yaml", "scripts/fuse_mast3r_stereo_imu.py",
                 "ego_vio/vio/symmetric_ir_factors.py", "ego_vio/vio/dual_ir_factors.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "VINS_CONFIG", tmp_path / "vins.yaml")
    monkeypatch.setattr(runner, "IMU_CONFIG", tmp_path / "imu.yaml")
    monkeypatch.setattr(runner.fusion, "load_vins_config", lambda *args: {"body_T_camera": np.eye(4), "td_s": -0.009109323})
    imu_times = np.linspace(-0.1, 1, 441)
    monkeypatch.setattr(runner.fusion, "load_calibrated_imu", lambda *args: (
        imu_times, np.zeros((441, 3)), np.tile([0, 0, 9.80665], (441, 1)), {}
    ))

    def load_eye(directory, eye, *args):
        observations = [
            {"accepted": True, "first_index": i, "second_index": i + 1,
             "first_t_sec": times[i], "second_t_sec": times[i + 1],
             "metric_displacement_frame": f"infrared_{eye}_camera_i",
             "metric_displacement_camera_i_m": (truth[i + 1] - truth[i]).tolist()}
            for i in range(20)
        ]
        track = {"eye": eye, "times": times, "metric_camera_positions": truth,
                 "camera_rotations": Rotation.identity(21).as_matrix(),
                 "body_t_camera": np.eye(4), "observations": observations,
                 "observation_confidences": np.ones(20)}
        metadata = {"factory_stereo_calibration": {"baseline_m": 0.018083254,
                                                   "right_rotation_from_left": np.eye(3).tolist()}}
        return track, metadata, []

    monkeypatch.setattr(runner, "load_eye", load_eye)
    output = tmp_path / "experiment"
    runner.run(SimpleNamespace(
        session=session, vins_dir=vins_dir,
        left_dir=tmp_path / "left", right_dir=tmp_path / "right", output_dir=output,
        max_correction_m=None,
        correction_cap_mode="per-node",
        eyes="left",
        stereo_weight_policy="residual-aware",
        disable_learned_motion=True,
    ))
    report = json.loads((output / "graph_report.json").read_text())
    manifest = json.loads((output / "candidate_manifest.json").read_text())
    assert report["output_frame"] == "body_imu_origin"
    assert report["output_samples"] == 21
    assert manifest["primary_eye"] is None
    assert manifest["policy_arguments"] == {
        "max_correction_mm": None,
        "correction_cap_mode": "per-node",
        "eyes": "left",
        "stereo_weight_policy": "residual-aware",
        "disable_learned_motion": True,
        "learned_motion_consistency_limit_m": None,
    }
    assert report["policy_arguments"] == manifest["policy_arguments"]
    assert report["joint_position_solver"]["position_correction_limit_m"] is None
    assert "secondary_visual_motion" not in report["joint_position_solver"]
    assert report["accepted"] is manifest["accepted"] is False
    assert report["external_ground_truth_used"] is False
    assert (output / "body_trajectory_fused.csv").exists()
    assert not (output / "trajectory_fused.csv").exists()
