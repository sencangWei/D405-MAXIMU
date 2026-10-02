import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "prepare_independent_right_geometry_probe",
    ROOT / "scripts/prepare_independent_right_geometry_probe.py",
)
prep = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prep
spec.loader.exec_module(prep)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_traj(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "t_sec,x,y,z,qw,qx,qy,qz\n"
        "0.0,0,0,0,1,0,0,0\n"
        "1.0,0.1,0,0,1,0,0,0\n"
        "2.0,0.2,0,0,1,0,0,0\n",
        encoding="utf-8",
    )


def base_report(session: Path, traj: Path, db3: Path, frame: str, observations: list[dict]):
    return {
        "schema": "umi_mast3r_stereo_scale_v2",
        "result": "PASS",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": str(session.resolve()),
        "trajectory": str(traj.resolve()),
        "db3": str(db3.resolve()),
        "observation_frame": frame,
        "correspondence_estimator": "classical",
        "scale_m_per_mast3r_unit": 0.42,
        "quality": {"global": "frozen"},
        "factory_stereo_calibration": {"baseline_m": 0.02},
        "observations": observations,
    }


def make_fixture(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("frames\n", encoding="utf-8")
    db3 = session / "d405_720p_rgb_stereo_ir.db3"
    db3.write_bytes(b"db3")
    left_traj = tmp_path / "left_traj.csv"
    right_traj = tmp_path / "right_traj.csv"
    write_traj(left_traj)
    write_traj(right_traj)
    observations = [
        {
            "accepted": True,
            "first_index": 0,
            "second_index": 1,
            "first_t_sec": 0.0,
            "second_t_sec": 1.0,
            "metric_displacement_camera_i_m": [9.0, 0.0, 0.0],
            "pnp_rotation_quaternion_xyzw": Rotation.identity().as_quat().tolist(),
            "scale": 0.42,
            "median_depth_m": 99.0,
            "mast3r_distance": 88.0,
            "pnp_rotation_mode": "stale",
            "pnp_rotation_constrained": True,
            "pnp_free_rotation_delta_deg": 77.0,
            "sample_hop": 1,
        },
        {
            "accepted": True,
            "first_index": 1,
            "second_index": 2,
            "first_t_sec": 1.0,
            "second_t_sec": 2.0,
            "metric_displacement_camera_i_m": [8.0, 0.0, 0.0],
            "pnp_rotation_quaternion_xyzw": Rotation.identity().as_quat().tolist(),
            "scale": 0.42,
            "median_depth_m": 55.0,
            "mast3r_distance": 44.0,
            "pnp_rotation_mode": "stale",
            "pnp_rotation_constrained": True,
            "pnp_free_rotation_delta_deg": 33.0,
            "sample_hop": 1,
        },
        {"accepted": False, "reason": "existing_reject", "first_index": 0, "second_index": 2},
    ]
    left_dir, right_dir = tmp_path / "left_sources", tmp_path / "right_sources"
    left_paths, right_paths = [], []
    for left_name, right_name in zip(prep.physical.eye_report_names("left"), prep.physical.eye_report_names("right")):
        left_path, right_path = left_dir / left_name, right_dir / right_name
        write_json(left_path, base_report(session, left_traj, db3, "infrared_left_camera_i", observations))
        right_report = base_report(session, right_traj, db3, "infrared_right_camera_i", observations)
        right_report["derived_from_left_stereo_report"] = str(left_path.resolve())
        write_json(right_path, right_report)
        left_paths.append(left_path)
        right_paths.append(right_path)
    overrides = {str(path.resolve()): prep.file_hash(path) for path in [*left_paths, *right_paths]}
    primary_right = right_paths[0]
    stage = tmp_path / "stage"
    write_json(
        stage / "preflight_report.json",
        {
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "records": [
                {
                    "id": "rec",
                    "refined_left_sources": [str(path.resolve()) for path in left_paths],
                    "refined_right_sources": [str(path.resolve()) for path in right_paths],
                    "right_raw_geometry_trajectory": str(right_traj.resolve()),
                    "right_raw_geometry_trajectory_sha256": prep.file_hash(right_traj),
                    "source_override_sha256": overrides,
                    "right_derivation_left_source_sha256": {
                        str(primary_right.resolve()): {
                            "left_source_path": str(left_paths[0].resolve()),
                            "left_source_sha256": prep.file_hash(left_paths[0]),
                            "original_right_source": str(primary_right.resolve()),
                            "original_right_sha256": prep.file_hash(primary_right),
                        }
                    },
                }
            ],
        },
    )
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"records": [{"id": "rec", "session": str(session.resolve()), "capture_dir": str(tmp_path)}]})
    baseline = tmp_path / "baseline"
    candidate = baseline / "rec" / "both" / "candidate_manifest.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("{}\n", encoding="utf-8")
    return manifest, baseline, stage, left_paths, right_paths


def patch_runtime(monkeypatch):
    monkeypatch.setattr(
        prep.diag.lowdiag,
        "_load_images",
        lambda *_args, **_kwargs: (
            np.array([10, 11, 12]),
            np.array([20, 21, 22]),
            {"fixture": True},
            {"baseline_m": 0.02, "right_rotation_from_left": np.eye(3), "right_translation_from_left_m": [-0.02, 0.0, 0.0]},
            {10: np.zeros((2, 2), dtype=np.uint8), 11: np.ones((2, 2), dtype=np.uint8), 12: 2 * np.ones((2, 2), dtype=np.uint8)},
            {20: np.zeros((2, 2), dtype=np.uint8), 21: np.ones((2, 2), dtype=np.uint8), 22: 2 * np.ones((2, 2), dtype=np.uint8)},
        ),
    )
    monkeypatch.setattr(prep.diag.stereo, "stereo_disparity", lambda *a, **k: (np.ones((2, 2)), -np.ones((2, 2))))

    def fake_right(_ri, _rj, _dl, _dr, _pos, _rot, pair, _calib):
        unordered = tuple(sorted((pair.first_index, pair.second_index)))
        scale = 1.0 if unordered == (0, 1) else (1.0 if pair.first_index < pair.second_index else 2.0)
        return {
            "accepted": True,
            "scale": scale,
            "scale_estimator": "native_fixture",
            "metric_distance_m": 0.1,
            "pnp_inlier_ratio": 1.0,
            "rotation_error_deg": 0.0,
            "metric_displacement_camera_i_m": [0.123, 0.0, 0.0],
            "metric_displacement_frame": "infrared_right_camera_i",
            "pnp_rotation_quaternion_xyzw": Rotation.identity().as_quat().tolist(),
            "right_centric_motion_source": "independent_right_pixels_negative_disparity",
        }

    monkeypatch.setattr(prep.diag, "estimate_right_motion", fake_right)


def parse_args(manifest, baseline, stage, output):
    return prep.argument_parser().parse_args(
        ["--manifest", str(manifest), "--baseline", str(baseline), "--source-stage", str(stage), "--output", str(output), "--dataset", "rec"]
    )


def test_refresh_replaces_accepts_preserves_native_reject_fallback_and_layout(tmp_path, monkeypatch):
    manifest, baseline, stage, _left_paths, _right_paths = make_fixture(tmp_path)
    patch_runtime(monkeypatch)
    result = prep.run(parse_args(manifest, baseline, stage, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_COMPLETE"
    row = result["records"][0]
    assert len(row["refined_right_sources"]) == 4
    assert len(row["source_override_sha256"]) == 8
    assert row["independent_replaced_rows"] == 4
    assert row["fallback_preserved_rows"] == 4
    assert row["rejected_input_rows_unchanged"] == 4
    assert row["independent_right_geometry_refresh"]["schema"] == "umi_independent_right_geometry_refresh_v1"
    report = json.loads(Path(row["refined_right_sources"][0]).read_text(encoding="utf-8"))
    assert "derived_from_left_stereo_report" not in report
    assert report["scale_m_per_mast3r_unit"] == 0.42
    assert report["quality"] == {"global": "frozen"}
    assert report["observations"][0]["independent_right_geometry_update"] is True
    assert report["observations"][0]["metric_displacement_camera_i_m"] == [0.123, 0.0, 0.0]
    assert "sample_hop" in report["observations"][0]
    for stale in ("median_depth_m", "mast3r_distance", "pnp_rotation_mode", "pnp_rotation_constrained", "pnp_free_rotation_delta_deg"):
        assert stale not in report["observations"][0]
    assert report["observations"][1]["independent_right_geometry_update"] is False
    assert report["observations"][1]["independent_right_geometry_failure_reason"] == "bidirectional_scale_disagrees"
    assert report["observations"][1]["metric_displacement_camera_i_m"] == [8.0, 0.0, 0.0]
    assert report["observations"][2] == {"accepted": False, "reason": "existing_reject", "first_index": 0, "second_index": 2}


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda obs: obs.__setitem__("first_index", 0.5), "exact integer"),
        (lambda obs: obs.__setitem__("second_index", True), "exact integer"),
        (lambda obs: obs.__setitem__("first_t_sec", float("nan")), "non-finite"),
        (lambda obs: obs.__setitem__("second_t_sec", 1.5), "timestamp mismatch"),
        (lambda obs: obs.__setitem__("second_index", 99), "out of range"),
    ],
)
def test_bad_accepted_row_rejected_before_image_load(tmp_path, monkeypatch, mutator, message):
    manifest, baseline, stage, _left_paths, right_paths = make_fixture(tmp_path)
    report = json.loads(right_paths[0].read_text(encoding="utf-8"))
    mutator(report["observations"][0])
    write_json(right_paths[0], report)
    stage_report = json.loads((stage / "preflight_report.json").read_text(encoding="utf-8"))
    stage_report["records"][0]["source_override_sha256"][str(right_paths[0].resolve())] = prep.file_hash(right_paths[0])
    if str(right_paths[0].resolve()) in stage_report["records"][0]["right_derivation_left_source_sha256"]:
        stage_report["records"][0]["right_derivation_left_source_sha256"][str(right_paths[0].resolve())][
            "original_right_sha256"
        ] = prep.file_hash(right_paths[0])
    write_json(stage / "preflight_report.json", stage_report)
    monkeypatch.setattr(prep.diag.lowdiag, "_load_images", lambda *a, **k: (_ for _ in ()).throw(AssertionError("image load should not run")))
    result = prep.run(parse_args(manifest, baseline, stage, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert message in result["failures"][0]["error"]


def test_repeated_pairs_are_memoized_across_four_reports(tmp_path, monkeypatch):
    manifest, baseline, stage, _left_paths, _right_paths = make_fixture(tmp_path)
    calls = {"estimate": 0, "disparity": 0}
    patch_runtime(monkeypatch)

    def counted_disparity(*_args, **_kwargs):
        calls["disparity"] += 1
        return np.ones((2, 2)), -np.ones((2, 2))

    def counted_right(_ri, _rj, _dl, _dr, _pos, _rot, pair, _calib):
        calls["estimate"] += 1
        return {
            "accepted": True,
            "scale": 1.0,
            "scale_estimator": "native_fixture",
            "metric_distance_m": 0.1,
            "pnp_inlier_ratio": 1.0,
            "rotation_error_deg": 0.0,
            "metric_displacement_camera_i_m": [0.123, 0.0, 0.0],
            "metric_displacement_frame": "infrared_right_camera_i",
            "pnp_rotation_quaternion_xyzw": Rotation.identity().as_quat().tolist(),
            "right_centric_motion_source": "independent_right_pixels_negative_disparity",
        }

    monkeypatch.setattr(prep.diag.stereo, "stereo_disparity", counted_disparity)
    monkeypatch.setattr(prep.diag, "estimate_right_motion", counted_right)
    result = prep.run(parse_args(manifest, baseline, stage, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_COMPLETE"
    assert calls["estimate"] == 4  # two unique pairs times forward/reverse
    assert calls["disparity"] == 3  # frames 0,1,2 cached despite four reports


def test_source_override_hash_mismatch_fails_visible(tmp_path, monkeypatch):
    manifest, baseline, stage, _left_paths, right_paths = make_fixture(tmp_path)
    patch_runtime(monkeypatch)
    stage_report = json.loads((stage / "preflight_report.json").read_text(encoding="utf-8"))
    stage_report["records"][0]["source_override_sha256"][str(right_paths[1].resolve())] = "bad"
    write_json(stage / "preflight_report.json", stage_report)
    result = prep.run(parse_args(manifest, baseline, stage, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "source_override_sha256 mismatch" in result["failures"][0]["error"]


def test_refuses_existing_output(tmp_path):
    manifest, baseline, stage, _left_paths, _right_paths = make_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(FileExistsError):
        prep.run(parse_args(manifest, baseline, stage, out))


def test_source_stage_gt_flag_rejected(tmp_path):
    manifest, baseline, stage, _left_paths, _right_paths = make_fixture(tmp_path)
    stage_report = json.loads((stage / "preflight_report.json").read_text(encoding="utf-8"))
    stage_report["external_ground_truth_used"] = True
    write_json(stage / "preflight_report.json", stage_report)
    with pytest.raises(ValueError, match="onboard-only"):
        prep.run(parse_args(manifest, baseline, stage, tmp_path / "out"))
