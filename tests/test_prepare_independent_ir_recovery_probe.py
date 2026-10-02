import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "prepare_independent_ir_recovery_probe",
    ROOT / "scripts/prepare_independent_ir_recovery_probe.py",
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


def observations():
    return [
        {"accepted": True, "first_index": 0, "second_index": 1, "first_t_sec": 0.0, "second_t_sec": 1.0, "scale": 0.42},
        {"accepted": False, "reason": "pnp_failed", "first_index": 1, "second_index": 2, "first_t_sec": 1.0, "second_t_sec": 2.0},
        {
            "accepted": False,
            "reason": "translation_excitation_low",
            "first_index": 0,
            "second_index": 2,
            "first_t_sec": 0.0,
            "second_t_sec": 2.0,
        },
    ]


def report(session: Path, traj: Path, db3: Path, frame: str, *, result="PASS"):
    return {
        "schema": "umi_mast3r_stereo_scale_v2",
        "result": result,
        "failures": ["optional_failed"] if result == "FAIL" else [],
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": str(session.resolve()),
        "trajectory": str(traj.resolve()),
        "db3": str(db3.resolve()),
        "observation_frame": frame,
        "correspondence_estimator": "classical",
        "scale_m_per_mast3r_unit": 0.42,
        "factory_stereo_calibration": {"baseline_m": 0.02},
        "observations": observations(),
    }


def make_fixture(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("frames\n", encoding="utf-8")
    db3 = session / "d405_720p_rgb_stereo_ir.db3"
    db3.write_bytes(b"db3")
    left_traj, right_traj = tmp_path / "left.csv", tmp_path / "right.csv"
    write_traj(left_traj)
    write_traj(right_traj)
    left_dir, right_dir = tmp_path / "left_reports", tmp_path / "right_reports"
    left_paths, right_paths = [], []
    for index, (left_name, right_name) in enumerate(zip(prep.physical.eye_report_names("left"), prep.physical.eye_report_names("right"))):
        result = "FAIL" if index == 2 else "PASS"
        left_path, right_path = left_dir / left_name, right_dir / right_name
        write_json(left_path, report(session, left_traj, db3, "infrared_left_camera_i", result=result))
        right = report(session, right_traj, db3, "infrared_right_camera_i", result=result)
        right["derived_from_left_stereo_report"] = str(left_path.resolve())
        write_json(right_path, right)
        left_paths.append(left_path)
        right_paths.append(right_path)
    parent = tmp_path / "parent"
    overrides = {str(path.resolve()): prep.file_hash(path) for path in [*left_paths, *right_paths]}
    write_json(
        parent / "preflight_report.json",
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
                        str(right_paths[0].resolve()): {
                            "left_source_path": str(left_paths[0].resolve()),
                            "left_source_sha256": prep.file_hash(left_paths[0]),
                            "original_right_source": str(right_paths[0].resolve()),
                            "original_right_sha256": prep.file_hash(right_paths[0]),
                        }
                    },
                }
            ],
        },
    )
    source = tmp_path / "source"
    row = {
        "id": "rec",
        "refined_left_sources": [str(path.resolve()) for path in left_paths],
        "refined_right_sources": [str(path.resolve()) for path in right_paths],
        "source_override_sha256": overrides,
        "independent_right_geometry_refresh": {"schema": "umi_independent_right_geometry_refresh_v1"},
    }
    write_json(
        source / "preflight_report.json",
        {
            "schema": "umi_independent_right_geometry_refresh_preflight_v1",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "source_stage": str(parent.resolve()),
            "records": [row],
            "refined_sources": [row],
            "status": "PREFLIGHT_COMPLETE",
            "ready_record_count": 1,
        },
    )
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"records": [{"id": "rec", "session": str(session.resolve()), "capture_dir": str(tmp_path)}]})
    baseline = tmp_path / "baseline"
    candidate = baseline / "rec" / "both" / "candidate_manifest.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("{}\n", encoding="utf-8")
    return manifest, baseline, source, left_paths, right_paths


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
    monkeypatch.setattr(
        prep.CachedIrSiftCorrespondences,
        "correspondences",
        lambda self, *a, **k: (np.ones((8, 2), dtype=np.float32), np.ones((8, 2), dtype=np.float32), np.ones(8, dtype=bool)),
    )

    def left_estimate(*_args, **_kwargs):
        return {
            "accepted": True,
            "scale": 1.0,
            "metric_distance_m": 0.1,
            "pnp_inlier_ratio": 1.0,
            "rotation_error_deg": 0.0,
            "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
            "pnp_rotation_quaternion_xyzw": Rotation.identity().as_quat().tolist(),
        }

    right_scales = [1.0, 2.0] * 16

    def right_estimate(*_args, **_kwargs):
        return {
            "accepted": True,
            "scale": right_scales.pop(0),
            "metric_distance_m": 0.1,
            "pnp_inlier_ratio": 1.0,
            "rotation_error_deg": 0.0,
            "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
            "pnp_rotation_quaternion_xyzw": Rotation.identity().as_quat().tolist(),
        }

    monkeypatch.setattr(prep.diag.stereo, "estimate_motion_from_correspondences", left_estimate)
    monkeypatch.setattr(prep.diag, "estimate_right_motion_from_correspondences", right_estimate)


def args(manifest, baseline, source, output):
    return prep.argument_parser().parse_args(
        ["--manifest", str(manifest), "--baseline", str(baseline), "--source-stage", str(source), "--output", str(output), "--dataset", "rec"]
    )


def test_recovery_appendix_keeps_sources_and_records_both_eye_native_outcomes(tmp_path, monkeypatch):
    manifest, baseline, source, left_paths, right_paths = make_fixture(tmp_path)
    before_hashes = {path: prep.file_hash(path) for path in [*left_paths, *right_paths]}
    patch_runtime(monkeypatch)
    result = prep.run(args(manifest, baseline, source, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_COMPLETE"
    row = result["records"][0]
    assert row["refined_left_sources"] == [str(path.resolve()) for path in left_paths]
    assert row["refined_right_sources"] == [str(path.resolve()) for path in right_paths]
    for path, digest in before_hashes.items():
        assert prep.file_hash(path) == digest
    appendix = json.loads(Path(row["recovery_appendix_path"]).read_text(encoding="utf-8"))
    assert appendix["schema"] == prep.APPENDIX_SCHEMA
    assert appendix["eye_contexts"]["left"]["reference_scale"] == 0.42
    assert str(left_paths[2].resolve()) not in appendix["eye_contexts"]["left"]["merged_report_paths"]
    assert str(right_paths[2].resolve()) not in appendix["eye_contexts"]["right"]["merged_report_paths"]
    assert {obs["eye"] for obs in appendix["observations"]} == {"left", "right"}
    assert all(obs["source_report_result"] == "PASS" for obs in appendix["observations"])
    assert all(obs["original_observation"]["accepted"] is not True for obs in appendix["observations"])
    assert all(obs["original_observation"]["reason"] != prep.LOW_EXCITATION_REASON for obs in appendix["observations"])
    assert any(obs["eye"] == "left" and obs["native_observation"]["accepted"] is True for obs in appendix["observations"])
    assert any(obs["eye"] == "right" and obs["native_observation"]["accepted"] is False for obs in appendix["observations"])
    assert appendix["consumed_source_guard"]["guarded_after_verified"] is True


def test_bad_rejected_index_fails_before_image_load(tmp_path, monkeypatch):
    manifest, baseline, source, left_paths, _right_paths = make_fixture(tmp_path)
    report = json.loads(left_paths[0].read_text(encoding="utf-8"))
    report["observations"][1]["first_index"] = 1.5
    write_json(left_paths[0], report)
    for stage_dir in (source, Path(json.loads((source / "preflight_report.json").read_text(encoding="utf-8"))["source_stage"])):
        stage_report = json.loads((stage_dir / "preflight_report.json").read_text(encoding="utf-8"))
        stage_report["records"][0]["source_override_sha256"][str(left_paths[0].resolve())] = prep.file_hash(left_paths[0])
        mapping = stage_report["records"][0].get("right_derivation_left_source_sha256", {})
        for value in mapping.values():
            if value.get("left_source_path") == str(left_paths[0].resolve()):
                value["left_source_sha256"] = prep.file_hash(left_paths[0])
        write_json(stage_dir / "preflight_report.json", stage_report)
    monkeypatch.setattr(prep.diag.lowdiag, "_load_images", lambda *a, **k: (_ for _ in ()).throw(AssertionError("image load should not run")))
    result = prep.run(args(manifest, baseline, source, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "exact integer" in result["failures"][0]["error"]


def test_bad_right_rejected_index_fails_before_left_image_load(tmp_path, monkeypatch):
    manifest, baseline, source, _left_paths, right_paths = make_fixture(tmp_path)
    report = json.loads(right_paths[0].read_text(encoding="utf-8"))
    report["observations"][1]["second_index"] = 1.5
    write_json(right_paths[0], report)
    for stage_dir in (source, Path(json.loads((source / "preflight_report.json").read_text(encoding="utf-8"))["source_stage"])):
        stage_report = json.loads((stage_dir / "preflight_report.json").read_text(encoding="utf-8"))
        stage_report["records"][0]["source_override_sha256"][str(right_paths[0].resolve())] = prep.file_hash(right_paths[0])
        mapping = stage_report["records"][0].get("right_derivation_left_source_sha256", {})
        if str(right_paths[0].resolve()) in mapping:
            mapping[str(right_paths[0].resolve())]["original_right_sha256"] = prep.file_hash(right_paths[0])
        write_json(stage_dir / "preflight_report.json", stage_report)
    monkeypatch.setattr(prep.diag.lowdiag, "_load_images", lambda *a, **k: (_ for _ in ()).throw(AssertionError("image load should not run")))
    result = prep.run(args(manifest, baseline, source, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "exact integer" in result["failures"][0]["error"]


def test_bad_source_override_fails_before_image_load(tmp_path, monkeypatch):
    manifest, baseline, source, left_paths, _right_paths = make_fixture(tmp_path)
    stage_report = json.loads((source / "preflight_report.json").read_text(encoding="utf-8"))
    stage_report["records"][0]["source_override_sha256"][str(left_paths[0].resolve())] = "bad"
    write_json(source / "preflight_report.json", stage_report)
    monkeypatch.setattr(prep.diag.lowdiag, "_load_images", lambda *a, **k: (_ for _ in ()).throw(AssertionError("image load should not run")))
    result = prep.run(args(manifest, baseline, source, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "source_override_sha256 mismatch" in result["failures"][0]["error"]


def test_native_timestamps_preserve_original_rejected_row_exact_values(tmp_path, monkeypatch):
    manifest, baseline, source, left_paths, _right_paths = make_fixture(tmp_path)
    report = json.loads(left_paths[0].read_text(encoding="utf-8"))
    report["observations"][1]["first_t_sec"] = 1.004
    report["observations"][1]["second_t_sec"] = 2.004
    write_json(left_paths[0], report)
    for stage_dir in (source, Path(json.loads((source / "preflight_report.json").read_text(encoding="utf-8"))["source_stage"])):
        stage_report = json.loads((stage_dir / "preflight_report.json").read_text(encoding="utf-8"))
        stage_report["records"][0]["source_override_sha256"][str(left_paths[0].resolve())] = prep.file_hash(left_paths[0])
        mapping = stage_report["records"][0].get("right_derivation_left_source_sha256", {})
        for value in mapping.values():
            if value.get("left_source_path") == str(left_paths[0].resolve()):
                value["left_source_sha256"] = prep.file_hash(left_paths[0])
        write_json(stage_dir / "preflight_report.json", stage_report)
    patch_runtime(monkeypatch)
    result = prep.run(args(manifest, baseline, source, tmp_path / "out"))
    appendix = json.loads(Path(result["records"][0]["recovery_appendix_path"]).read_text(encoding="utf-8"))
    target = next(obs for obs in appendix["observations"] if obs["eye"] == "left" and obs["source_observation_index"] == 1)
    assert target["native_observation"]["first_t_sec"] == 1.004
    assert target["native_observation"]["second_t_sec"] == 2.004


def test_runtime_source_mutation_fails_before_appendix_write(tmp_path, monkeypatch):
    manifest, baseline, source, left_paths, _right_paths = make_fixture(tmp_path)
    patch_runtime(monkeypatch)
    original_build = prep.build_eye_appendix
    mutated = {"done": False}

    def mutating_build(*build_args, **build_kwargs):
        result = original_build(*build_args, **build_kwargs)
        if not mutated["done"]:
            left_paths[0].write_text(left_paths[0].read_text(encoding="utf-8") + "\n", encoding="utf-8")
            mutated["done"] = True
        return result

    monkeypatch.setattr(prep, "build_eye_appendix", mutating_build)
    result = prep.run(args(manifest, baseline, source, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "hash changed" in result["failures"][0]["error"]
    assert not (tmp_path / "out" / "rec" / "independent_ir_recovery_appendix.json").exists()


def test_appendix_consumed_paths_must_be_in_source_guard(tmp_path, monkeypatch):
    manifest, baseline, source, _left_paths, _right_paths = make_fixture(tmp_path)
    patch_runtime(monkeypatch)
    original_build = prep.build_eye_appendix
    unguarded = tmp_path / "unguarded_source.json"
    unguarded.write_text("{}", encoding="utf-8")

    def unguarded_build(*build_args, **build_kwargs):
        context, observations, consumed = original_build(*build_args, **build_kwargs)
        if build_kwargs["eye"] == "left":
            consumed = dict(consumed)
            consumed["unguarded"] = unguarded
        return context, observations, consumed

    monkeypatch.setattr(prep, "build_eye_appendix", unguarded_build)
    result = prep.run(args(manifest, baseline, source, tmp_path / "out"))
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "left consumed paths missing from source guard" in result["failures"][0]["error"]
    assert str(unguarded.resolve()) in result["failures"][0]["error"]
    assert not (tmp_path / "out" / "rec" / "independent_ir_recovery_appendix.json").exists()


def test_source_stage_preflight_must_be_complete(tmp_path):
    manifest, baseline, source, _left_paths, _right_paths = make_fixture(tmp_path)
    stage_report = json.loads((source / "preflight_report.json").read_text(encoding="utf-8"))
    stage_report["ready_record_count"] = 0
    write_json(source / "preflight_report.json", stage_report)
    with pytest.raises(ValueError, match="ready_record_count"):
        prep.run(args(manifest, baseline, source, tmp_path / "out"))


def test_refuses_existing_output(tmp_path):
    manifest, baseline, source, _left_paths, _right_paths = make_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(FileExistsError):
        prep.run(args(manifest, baseline, source, out))
