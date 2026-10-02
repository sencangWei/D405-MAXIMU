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
    "diagnose_independent_right_stereo",
    ROOT / "scripts/diagnose_independent_right_stereo.py",
)
diag = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diag
spec.loader.exec_module(diag)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_traj(path: Path, times=(0.0, 1.0, 2.0)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["t_sec,x,y,z,qw,qx,qy,qz"]
    for index, t in enumerate(times):
        rows.append(f"{t},{0.1*index},0,0,1,0,0,0")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def calibration():
    intr = {"width": 4, "height": 3, "fx": 10.0, "fy": 10.0, "cx": 2.0, "cy": 1.0, "coeffs": [0, 0, 0, 0]}
    return {
        "left": dict(intr),
        "right": dict(intr),
        "baseline_m": 0.02,
        "right_rotation_from_left": np.eye(3),
        "right_translation_from_left_m": np.array([-0.02, 0.0, 0.0]),
        "factory_topics": ["fixture"],
    }


def test_balanced_selection_uses_accepted_and_rejected():
    observations = [
        {"accepted": True, "first_index": i, "second_index": i + 1}
        for i in range(8)
    ] + [
        {"accepted": False, "reason": "pnp_failed", "first_index": 100 + i, "second_index": 101 + i}
        for i in range(8)
    ]
    pairs, meta = diag.select_balanced_pairs({"observations": observations}, max_pairs=12)
    assert meta["selected_accepted"] == 6
    assert meta["selected_rejected"] == 6
    assert any(pair.source_class == "rejected_left_source" for pair in pairs)


def test_default_pair_sampling_preserves_balanced_metadata():
    observations = [
        {"accepted": True, "first_index": 0, "second_index": 1},
        {"accepted": False, "reason": diag.LOW_EXCITATION_REASON, "first_index": 1, "second_index": 2},
    ]
    _pairs, meta = diag.select_balanced_pairs({"observations": observations}, max_pairs=2)
    assert meta == {
        "rule": "uniform_6_accepted_left_plus_6_rejected_left_fill_missing_uniform_other_class",
        "max_pairs": 2,
        "source_accepted_available": 1,
        "source_rejected_available": 1,
        "selected_accepted": 1,
        "selected_rejected": 1,
        "selected_total": 2,
    }


def test_visual_rejections_excludes_low_excitation_only_from_rejected_sampling():
    observations = [
        {"accepted": True, "reason": diag.LOW_EXCITATION_REASON, "first_index": 0, "second_index": 1},
        {"accepted": True, "first_index": 1, "second_index": 2},
        {"accepted": False, "reason": diag.LOW_EXCITATION_REASON, "first_index": 10, "second_index": 11},
        {"accepted": False, "reason": "pnp_failed", "first_index": 20, "second_index": 21},
        {"accepted": False, "reason": "visibility_low", "first_index": 30, "second_index": 31},
    ]
    pairs, meta = diag.select_balanced_pairs(
        {"observations": observations},
        max_pairs=4,
        pair_sampling=diag.PAIR_SAMPLING_VISUAL_REJECTIONS,
    )
    rejected_reasons = [
        pair.source_observation.get("reason")
        for pair in pairs
        if pair.source_class == "rejected_left_source"
    ]
    assert meta["selected_accepted"] == 2
    assert meta["selected_rejected"] == 2
    assert meta["source_rejected_available"] == 3
    assert meta["source_visual_rejected_available"] == 2
    assert meta["skipped_low_excitation_rejections"] == 1
    assert diag.LOW_EXCITATION_REASON not in rejected_reasons


def test_selection_fills_missing_class():
    pairs, meta = diag.select_balanced_pairs(
        {"observations": [{"accepted": True, "first_index": i, "second_index": i + 1} for i in range(3)]},
        max_pairs=6,
    )
    assert len(pairs) == 3
    assert meta["selected_accepted"] == 3
    assert meta["selected_rejected"] == 0


def test_visual_rejections_reports_expected_counts_when_low_excitation_shortens_class():
    observations = [
        {"accepted": True, "first_index": i, "second_index": i + 1}
        for i in range(5)
    ] + [
        {"accepted": False, "reason": diag.LOW_EXCITATION_REASON, "first_index": 100 + i, "second_index": 101 + i}
        for i in range(3)
    ] + [
        {"accepted": False, "reason": "pnp_failed", "first_index": 200, "second_index": 201}
    ]
    pairs, meta = diag.select_balanced_pairs(
        {"observations": observations},
        max_pairs=6,
        pair_sampling=diag.PAIR_SAMPLING_VISUAL_REJECTIONS,
    )
    assert len(pairs) == 6
    assert meta["selected_accepted"] == 5
    assert meta["selected_rejected"] == 1
    assert meta["skipped_low_excitation_rejections"] == 3
    assert meta["source_visual_rejected_available"] == 1


def test_factory_closure_handles_noncommuting_right_frame():
    right_from_left = Rotation.from_euler("z", 30, degrees=True)
    left_rotation = Rotation.from_euler("x", 20, degrees=True)
    left_displacement = np.array([0.01, -0.02, 0.03])
    left_translation = -left_rotation.apply(left_displacement)
    right_rotation, right_translation = diag.stereo.change_relative_pose_frame(
        left_rotation,
        left_translation,
        right_from_left,
        np.array([-0.02, 0.0, 0.0]),
    )
    right_displacement = -right_rotation.inv().apply(right_translation)
    closure = diag.factory_closure(
        {
            "accepted": True,
            "metric_displacement_camera_i_m": left_displacement.tolist(),
            "pnp_rotation_quaternion_xyzw": left_rotation.as_quat().tolist(),
        },
        {
            "accepted": True,
            "metric_displacement_camera_i_m": right_displacement.tolist(),
            "pnp_rotation_quaternion_xyzw": right_rotation.as_quat().tolist(),
        },
        {
            "right_rotation_from_left": right_from_left.as_matrix(),
            "right_translation_from_left_m": [-0.02, 0.0, 0.0],
        },
    )
    assert closure["vector_closure_m"] < 1e-12
    assert closure["rotation_closure_deg"] < 1e-12


def test_sift_correspondences_zero_lowe_survivors_returns_nx2(monkeypatch):
    class Detector:
        def detectAndCompute(self, *_args):
            kp = [type("KP", (), {"pt": (1.0, 2.0)})()]
            return kp, np.ones((1, 4), dtype=np.float32)

    class Matcher:
        def knnMatch(self, *_args, **_kwargs):
            a = type("M", (), {"distance": 2.0, "queryIdx": 0, "trainIdx": 0})()
            b = type("M", (), {"distance": 1.0, "queryIdx": 0, "trainIdx": 0})()
            return [[a, b]]

    monkeypatch.setattr(diag.cv2, "SIFT_create", lambda **_kwargs: Detector())
    monkeypatch.setattr(diag.cv2, "BFMatcher", lambda *_args, **_kwargs: Matcher())
    pts_i, pts_j, valid = diag.sift_correspondences(
        np.zeros((3, 4), dtype=np.uint8),
        np.zeros((3, 4), dtype=np.uint8),
        np.ones((3, 4), dtype=bool),
    )
    assert pts_i.shape == (0, 2)
    assert pts_j.shape == (0, 2)
    assert valid.shape == (0,)


def test_sift_correspondences_one_neighbor_rows_are_skipped(monkeypatch):
    class Detector:
        def detectAndCompute(self, *_args):
            kp = [type("KP", (), {"pt": (1.0, 2.0)})()]
            return kp, np.ones((1, 4), dtype=np.float32)

    class Matcher:
        def knnMatch(self, *_args, **_kwargs):
            return [[type("M", (), {"distance": 0.1, "queryIdx": 0, "trainIdx": 0})()]]

    monkeypatch.setattr(diag.cv2, "SIFT_create", lambda **_kwargs: Detector())
    monkeypatch.setattr(diag.cv2, "BFMatcher", lambda *_args, **_kwargs: Matcher())
    pts_i, pts_j, valid = diag.sift_correspondences(
        np.zeros((3, 4), dtype=np.uint8),
        np.zeros((3, 4), dtype=np.uint8),
        np.ones((3, 4), dtype=bool),
    )
    assert pts_i.shape == (0, 2)
    assert pts_j.shape == (0, 2)
    assert valid.shape == (0,)


def test_zero_sift_points_reach_native_rejection_not_shape_exception(monkeypatch):
    monkeypatch.setattr(
        diag,
        "sift_correspondences",
        lambda *_args, **_kwargs: (
            np.empty((0, 2), dtype=np.float32),
            np.empty((0, 2), dtype=np.float32),
            np.zeros(0, dtype=bool),
        ),
    )

    def fake_native(points_i, points_j, *_args, **_kwargs):
        assert points_i.shape == (0, 2)
        assert points_j.shape == (0, 2)
        return {"accepted": False, "reason": "insufficient_consistent_points", "method": "sift"}

    monkeypatch.setattr(diag.stereo, "estimate_motion_from_correspondences", fake_native)
    pair = diag.Pair(0, 1, "accepted_left_source", {"accepted": True})
    result = diag.estimate_left_motion(
        np.zeros((3, 4), dtype=np.uint8),
        np.zeros((3, 4), dtype=np.uint8),
        np.ones((3, 4), dtype=float),
        -np.ones((3, 4), dtype=float),
        np.zeros((2, 3)),
        Rotation.from_quat(np.tile([0.0, 0.0, 0.0, 1.0], (2, 1))),
        pair,
        calibration(),
    )
    assert result["accepted"] is False


def make_fixture(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("frames\n", encoding="utf-8")
    db3 = session / "d405_720p_rgb_stereo_ir.db3"
    db3.write_bytes(b"db3")
    left_traj = tmp_path / "left" / "trajectory_frames.csv"
    right_traj = tmp_path / "right" / "trajectory_frames.csv"
    write_traj(left_traj)
    write_traj(right_traj)
    observations = [
        {"accepted": True, "first_index": 0, "second_index": 1, "first_t_sec": 0.0, "second_t_sec": 1.0},
        {"accepted": False, "reason": "source_fail", "first_index": 1, "second_index": 2, "first_t_sec": 1.0, "second_t_sec": 2.0},
    ]
    left_report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": str(session.resolve()),
        "trajectory": str(left_traj.resolve()),
        "db3": str(db3.resolve()),
        "observation_frame": "infrared_left_camera_i",
        "correspondence_estimator": "classical",
        "observations": observations,
    }
    right_report = {
        **left_report,
        "trajectory": str(right_traj.resolve()),
        "observation_frame": "infrared_right_camera_i",
        "derived_from_left_stereo_report": "left",
    }
    left_path = tmp_path / "source" / "rec" / "refined_left_sources" / diag.PRIMARY_LEFT_REPORT
    right_path = tmp_path / "right" / diag.RIGHT_PRIMARY_REPORT
    refined_right_path = tmp_path / "derived" / diag.RIGHT_PRIMARY_REPORT
    write_json(left_path, left_report)
    write_json(right_path, right_report)
    write_json(refined_right_path, right_report)
    stage = tmp_path / "stage"
    left_hash = diag.file_sha256(left_path)
    right_hash = diag.file_sha256(right_path)
    refined_right_hash = diag.file_sha256(refined_right_path)
    raw_right_hash = diag.file_sha256(right_traj)
    write_json(
        stage / "preflight_report.json",
        {
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "records": [
                {
                    "id": "rec",
                    "refined_left_sources": [str(left_path.resolve())],
                    "refined_right_sources": [str(refined_right_path.resolve())],
                    "right_raw_geometry_trajectory": str(right_traj.resolve()),
                    "right_raw_geometry_trajectory_sha256": raw_right_hash,
                    "source_override_sha256": {
                        str(left_path.resolve()): left_hash,
                        str(refined_right_path.resolve()): refined_right_hash,
                    },
                    "right_derivation_left_source_sha256": {
                        str(refined_right_path.resolve()): {
                            "left_source_path": str(left_path.resolve()),
                            "left_source_sha256": left_hash,
                            "original_right_source": str(right_path.resolve()),
                            "original_right_sha256": right_hash,
                        }
                    },
                }
            ],
        },
    )
    manifest = tmp_path / "manifest.json"
    write_json(
        manifest,
        {"records": [{"id": "rec", "session": str(session.resolve()), "capture_dir": str(tmp_path)}]},
    )
    baseline = tmp_path / "baseline"
    candidate = baseline / "rec" / "both" / "candidate_manifest.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("{}\n", encoding="utf-8")
    return manifest, baseline, stage


def patch_runtime(monkeypatch):
    monkeypatch.setattr(
        diag.lowdiag,
        "_load_images",
        lambda *_args, **_kwargs: (
            np.array([10, 11, 12]),
            np.array([20, 21, 22]),
            {"trajectory_frame": "infrared_left"},
            calibration(),
            {10: np.zeros((3, 4), dtype=np.uint8), 11: np.zeros((3, 4), dtype=np.uint8), 12: np.zeros((3, 4), dtype=np.uint8)},
            {20: np.zeros((3, 4), dtype=np.uint8), 21: np.zeros((3, 4), dtype=np.uint8), 22: np.zeros((3, 4), dtype=np.uint8)},
        ),
    )
    monkeypatch.setattr(
        diag.stereo,
        "stereo_disparity",
        lambda *_args, **_kwargs: (np.ones((3, 4), dtype=float), -np.ones((3, 4), dtype=float)),
    )
    monkeypatch.setattr(
        diag,
        "sift_correspondences",
        lambda *_args, **_kwargs: (np.ones((30, 2), dtype=np.float32), np.ones((30, 2), dtype=np.float32), np.ones(30, dtype=bool)),
    )
    result = {
        "accepted": True,
        "method": "sift",
        "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
        "metric_displacement_frame": "infrared_left_camera_i",
        "pnp_rotation_quaternion_xyzw": Rotation.identity().as_quat().tolist(),
        "tracked_points": 30,
        "pnp_inliers": 30,
        "pnp_refined": True,
    }
    monkeypatch.setattr(diag.stereo, "estimate_motion_from_correspondences", lambda *a, **k: dict(result))
    monkeypatch.setattr(
        diag,
        "estimate_right_motion_from_correspondences",
        lambda *a, **k: {**result, "metric_displacement_frame": "infrared_right_camera_i", "right_centric_motion_source": "independent_right_pixels_negative_disparity"},
    )


def test_run_record_outputs_cross_matrix_and_no_gt_flags(tmp_path, monkeypatch):
    manifest, baseline, stage = make_fixture(tmp_path)
    patch_runtime(monkeypatch)
    args = diag.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(stage),
            "--output",
            str(tmp_path / "out"),
            "--dataset",
            "rec",
            "--max-pairs",
            "2",
        ]
    )
    result = diag.run(args)
    assert result["status"] == "PASS"
    assert result["external_ground_truth_used"] is False
    assert result["backend_launched"] is False
    assert "pair_sampling_mode" not in result["policy"]
    row = result["records"][0]
    assert row["cross_matrix"]["both"] == 2
    assert row["selection"]["selected_accepted"] == 1
    assert row["selection"]["selected_rejected"] == 1
    assert row["diagnostics"][0]["factory_frame_closure"]["vector_closure_mm"] == pytest.approx(0.0)


def test_run_visual_rejections_reports_policy_and_selection_counts(tmp_path, monkeypatch):
    manifest, baseline, stage = make_fixture(tmp_path)
    patch_runtime(monkeypatch)
    args = diag.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(stage),
            "--output",
            str(tmp_path / "out"),
            "--dataset",
            "rec",
            "--max-pairs",
            "2",
            "--pair-sampling",
            "visual-rejections",
        ]
    )
    result = diag.run(args)
    assert result["status"] == "PASS"
    assert result["policy"]["pair_sampling_mode"] == diag.PAIR_SAMPLING_VISUAL_REJECTIONS
    selection = result["records"][0]["selection"]
    assert selection["selected_total"] == 2
    assert selection["source_visual_rejected_available"] == 1
    assert selection["skipped_low_excitation_rejections"] == 0


def test_run_refuses_overwrite(tmp_path):
    manifest, baseline, stage = make_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    args = diag.argument_parser().parse_args(
        ["--manifest", str(manifest), "--baseline", str(baseline), "--source-stage", str(stage), "--output", str(out)]
    )
    with pytest.raises(FileExistsError):
        diag.run(args)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda row, paths: row["source_override_sha256"].__setitem__(str(paths["left"].resolve()), "bad"), "LEFT source_override"),
        (lambda row, paths: row.__setitem__("refined_right_sources", []), "RIGHT mapping key"),
        (lambda row, paths: row["right_derivation_left_source_sha256"][str(paths["refined_right"].resolve())].__setitem__("original_right_sha256", "bad"), "original_right_sha256"),
        (lambda row, paths: row.__setitem__("right_raw_geometry_trajectory_sha256", "bad"), "trajectory sha256"),
    ],
)
def test_stage_hash_bindings_fail_closed(tmp_path, mutator, message):
    manifest, baseline, stage = make_fixture(tmp_path)
    report_path = stage / "preflight_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    paths = {
        "left": tmp_path / "source" / "rec" / "refined_left_sources" / diag.PRIMARY_LEFT_REPORT,
        "refined_right": tmp_path / "derived" / diag.RIGHT_PRIMARY_REPORT,
    }
    mutator(report["records"][0], paths)
    write_json(report_path, report)
    args = diag.argument_parser().parse_args(
        [
            "--manifest", str(manifest), "--baseline", str(baseline),
            "--source-stage", str(stage), "--output", str(tmp_path / "out"),
            "--dataset", "rec", "--max-pairs", "2",
        ]
    )
    result = diag.run(args)
    assert result["status"] == "FAIL"
    assert message in result["failures"][0]["error"]


def test_missing_pair_time_is_rejected(tmp_path, monkeypatch):
    manifest, baseline, stage = make_fixture(tmp_path)
    left_path = tmp_path / "source" / "rec" / "refined_left_sources" / diag.PRIMARY_LEFT_REPORT
    report = json.loads(left_path.read_text(encoding="utf-8"))
    del report["observations"][0]["first_t_sec"]
    write_json(left_path, report)
    patch_runtime(monkeypatch)
    args = diag.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--source-stage",
            str(stage),
            "--output",
            str(tmp_path / "out"),
            "--dataset",
            "rec",
            "--max-pairs",
            "2",
        ]
    )
    result = diag.run(args)
    assert result["status"] == "FAIL"
    assert "TypeError" in result["failures"][0]["error"] or "ValueError" in result["failures"][0]["error"]


def test_missing_pair_time_rejected_before_image_load(tmp_path, monkeypatch):
    manifest, baseline, stage = make_fixture(tmp_path)
    left_path = tmp_path / "source" / "rec" / "refined_left_sources" / diag.PRIMARY_LEFT_REPORT
    report = json.loads(left_path.read_text(encoding="utf-8"))
    del report["observations"][0]["first_t_sec"]
    write_json(left_path, report)
    stage_report_path = stage / "preflight_report.json"
    stage_report = json.loads(stage_report_path.read_text(encoding="utf-8"))
    row = stage_report["records"][0]
    left_key = str(left_path.resolve())
    left_hash = diag.file_sha256(left_path)
    row["source_override_sha256"][left_key] = left_hash
    refined_right_key = str((tmp_path / "derived" / diag.RIGHT_PRIMARY_REPORT).resolve())
    row["right_derivation_left_source_sha256"][refined_right_key]["left_source_sha256"] = left_hash
    write_json(stage_report_path, stage_report)
    monkeypatch.setattr(diag.lowdiag, "_load_images", lambda *a, **k: (_ for _ in ()).throw(AssertionError("image load should not run")))
    args = diag.argument_parser().parse_args(
        [
            "--manifest", str(manifest), "--baseline", str(baseline),
            "--source-stage", str(stage), "--output", str(tmp_path / "out"),
            "--dataset", "rec", "--max-pairs", "2",
        ]
    )
    result = diag.run(args)
    assert result["status"] == "FAIL"
    assert "source LEFT observation missing first_t_sec" in result["failures"][0]["error"]


def test_source_stage_gt_flag_rejected(tmp_path):
    manifest, baseline, stage = make_fixture(tmp_path)
    report_path = stage / "preflight_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["external_ground_truth_used"] = True
    write_json(report_path, report)
    args = diag.argument_parser().parse_args(
        ["--manifest", str(manifest), "--baseline", str(baseline), "--source-stage", str(stage), "--output", str(tmp_path / "out")]
    )
    with pytest.raises(ValueError, match="ground_truth|onboard"):
        diag.run(args)
