from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_bidirectional_se3_probe as probe


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _row(first: int = 0, second: int = 1) -> dict:
    return {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": first * 0.01,
        "second_t_sec": second * 0.01,
        "metric_displacement_camera_i_m": [0.01, 0.0, 0.0],
        "metric_displacement_frame": "infrared_left_camera_i",
        "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
        "scale": 1.0,
        "pnp_inlier_ratio": 0.8,
    }


def _candidate(eye: str, first: int, second: int, confidence: float, vector=None) -> dict:
    vector = [0.01, 0.0, 0.0] if vector is None else vector
    return {
        "eye": eye,
        "reference_first_index": first,
        "reference_second_index": second,
        "first_index": first,
        "second_index": second,
        "first_t_sec": first * 0.01,
        "second_t_sec": second * 0.01,
        "metric_displacement_camera_i_m": vector,
        "metric_displacement_frame": f"infrared_{eye}_camera_i",
        "observation_confidence": confidence,
        "body_t_camera": np.eye(4).tolist(),
    }


def test_load_source_stage_rejects_wrong_schema(tmp_path):
    stage = tmp_path / "stage"
    _write_json(stage / "preflight_report.json", {
        "schema": "wrong",
        "status": "PREFLIGHT_COMPLETE",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "ready_record_count": 0,
        "refined_sources": [],
    })

    with pytest.raises(ValueError, match="schema mismatch"):
        probe.load_source_stage(stage)


def test_load_source_stage_validates_override_hashes(tmp_path):
    left = _write_json(tmp_path / "left.json", {"ok": 1})
    right = _write_json(tmp_path / "right.json", {"ok": 2})
    appendix = _write_json(tmp_path / "appendix.json", {
        "schema": probe.APPENDIX_SCHEMA,
        "external_ground_truth_used": False,
        "slam_supervision": False,
    })
    stage = tmp_path / "stage"
    _write_json(stage / "preflight_report.json", {
        "schema": probe.SOURCE_SCHEMA,
        "status": "PREFLIGHT_COMPLETE",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "ready_record_count": 1,
        "refined_sources": [{
            "id": "rec",
            "status": probe.SOURCE_RECORD_STATUS,
            "refined_left_sources": [str(left)],
            "refined_right_sources": [str(right)],
            "source_override_sha256": {
                str(left.resolve()): "bad",
                str(right.resolve()): probe._path_hash(right),
            },
            "recovery_appendix_path": str(appendix),
            "recovery_appendix_sha256": probe._path_hash(appendix),
        }],
    })

    with pytest.raises(ValueError, match="override hash changed"):
        probe.load_source_stage(stage)


def test_load_source_stage_rejects_non_contract_record_status(tmp_path):
    left = _write_json(tmp_path / "left.json", {"ok": 1})
    right = _write_json(tmp_path / "right.json", {"ok": 2})
    appendix = _write_json(tmp_path / "appendix.json", {"ok": 3})
    stage = tmp_path / "stage"
    _write_json(stage / "preflight_report.json", {
        "schema": probe.SOURCE_SCHEMA,
        "status": "PREFLIGHT_COMPLETE",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "ready_record_count": 1,
        "refined_sources": [{
            "id": "rec",
            "status": "READY",
            "refined_left_sources": [str(left)],
            "refined_right_sources": [str(right)],
            "source_override_sha256": {
                str(left.resolve()): probe._path_hash(left),
                str(right.resolve()): probe._path_hash(right),
            },
            "recovery_appendix_path": str(appendix),
            "recovery_appendix_sha256": probe._path_hash(appendix),
        }],
    })

    with pytest.raises(ValueError, match="not ready"):
        probe.load_source_stage(stage)


def _appendix_fixture(tmp_path, monkeypatch, *, native_accepted=True, external_gt=False,
                     bind_reason=None, include_optional_failed_report=False):
    session = tmp_path / "session"
    (session / "external_imu").mkdir(parents=True)
    (session / "external_imu" / "imu.bin").write_bytes(b"imu")
    left_report = _write_json(tmp_path / "left_report.json", {
        "result": "PASS",
        "trajectory": str(tmp_path / "left_raw.csv"),
        "observations": [_row()],
    })
    right_report = _write_json(tmp_path / "right_report.json", {
        "result": "PASS",
        "trajectory": str(tmp_path / "right_raw.csv"),
        "observations": [{**_row(), "metric_displacement_frame": "infrared_right_camera_i"}],
    })
    failed_report = _write_json(tmp_path / "failed_report.json", {
        "result": "FAIL",
        "trajectory": str(tmp_path / "left_raw.csv"),
        "observations": [],
    })
    left_raw = _write_json(tmp_path / "left_raw.csv", {"fake": "left"})
    right_raw = _write_json(tmp_path / "right_raw.csv", {"fake": "right"})
    left_metric = _write_json(tmp_path / "left_metric.csv", {"metric": "left"})
    right_metric = _write_json(tmp_path / "right_metric.csv", {"metric": "right"})
    guard = {
        "left_report": {"path": str(left_report), "sha256": probe._path_hash(left_report)},
        "right_report": {"path": str(right_report), "sha256": probe._path_hash(right_report)},
        "left_raw": {"path": str(left_raw), "sha256": probe._path_hash(left_raw)},
        "right_raw": {"path": str(right_raw), "sha256": probe._path_hash(right_raw)},
    }
    if include_optional_failed_report:
        guard["failed_report"] = {"path": str(failed_report), "sha256": probe._path_hash(failed_report)}
    forward = {**_row(), "metric_distance_m": 0.01, "mast3r_distance": 1.0, "rotation_error_deg": 0.1}
    reverse = {**_row(), "metric_displacement_camera_i_m": [-0.01, 0.0, 0.0],
               "metric_distance_m": 0.01, "scale": 1.0,
               "pnp_inlier_ratio": 0.8, "rotation_error_deg": 0.1, "mast3r_distance": 1.0}
    native = probe.bidirectional_source.combine_native_geometry(
        forward,
        reverse,
        probe.stereo_scale.combine_bidirectional_scale,
    )
    if not native_accepted:
        native = {
            **native,
            "accepted": False,
            "reason": "bidirectional_se3_rotation_ambiguous",
        }
    appendix = _write_json(tmp_path / "appendix.json", {
        "schema": probe.APPENDIX_SCHEMA,
        "id": "rec",
        "session": str(session),
        "external_ground_truth_used": external_gt,
        "slam_supervision": False,
        "consumed_source_guard": {
            "guarded_after_verified": True,
            "guarded_before_sha256": guard,
        },
        "eye_contexts": {
            "left": {
                "reference_scale": 1.0,
                "merged_report_paths": [str(left_report.resolve())],
                "reference_trajectory_path": str(left_raw.resolve()),
                "reference_trajectory_sha256": probe._path_hash(left_raw),
                "report_sha256": {
                    str(left_report.resolve()): probe._path_hash(left_report),
                    **({str(failed_report.resolve()): probe._path_hash(failed_report)} if include_optional_failed_report else {}),
                },
            },
            "right": {
                "reference_scale": 1.0,
                "merged_report_paths": [str(right_report.resolve())],
                "reference_trajectory_path": str(right_raw.resolve()),
                "reference_trajectory_sha256": probe._path_hash(right_raw),
                "report_sha256": {str(right_report.resolve()): probe._path_hash(right_report)},
            },
        },
        "observations": [{
            "eye": "left",
            "source_report_path": str(left_report.resolve()),
            "source_report_sha256": probe._path_hash(left_report),
            "source_report_result": "PASS",
            "source_observation_index": 0,
            "original_observation": _row(),
            "native_observation": native,
            "raw_forward_summary": {"accepted": True},
            "raw_forward_summary": forward,
            "raw_reverse_summary": reverse,
        }],
    })
    monkeypatch.setattr(probe.paired.fusion, "load_trajectory", lambda path: (np.array([0.0, 0.01, 0.02]), None, None, None))
    monkeypatch.setattr(probe.paired.physical, "eye_trajectory_path_from_baseline", lambda _candidate, eye: left_metric if eye == "left" else right_metric)
    monkeypatch.setattr(probe.paired.source_eval, "validate_candidate_hashes", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(probe.paired.physical, "validate_eye_metadata", lambda *_args, **_kwargs: {"effective_body_T_camera": np.eye(4).tolist()})
    monkeypatch.setattr(probe.paired.physical, "validate_raw_observation_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(probe.paired.fusion, "stereo_observation_confidence", lambda native, scale: native.get("pnp_inlier_ratio", 0.5))

    def bind_candidate(reference_times, _metric_times, eye, native_row, confidence, body_t_camera):
        if bind_reason is not None:
            return None, bind_reason
        return _candidate(eye, native_row["first_index"], native_row["second_index"], confidence,
                          native_row["metric_displacement_camera_i_m"]), None

    monkeypatch.setattr(probe.paired.physical, "reference_bound_eye_candidate", bind_candidate)
    return {
        "record": {"id": "rec", "session": str(session)},
        "stage_record": {
            "recovery_appendix_path": str(appendix),
            "recovery_appendix_sha256": probe._path_hash(appendix),
        },
        "baseline_candidate": {},
        "reference_times": np.array([0.0, 0.01, 0.02]),
        "eye_reports": {
            "left": {"scale_m_per_mast3r_unit": 1.0, "merged_report_paths": [str(left_report.resolve())]},
            "right": {"scale_m_per_mast3r_unit": 1.0, "merged_report_paths": [str(right_report.resolve())]},
        },
    }


def test_appendix_invalid_math_is_frozen_fallback_not_scalar_salvage(tmp_path, monkeypatch):
    fixture = _appendix_fixture(tmp_path, monkeypatch, native_accepted=False)
    appendix = json.loads(Path(fixture["stage_record"]["recovery_appendix_path"]).read_text())
    rejected_native = appendix["observations"][0]["native_observation"]
    monkeypatch.setattr(
        probe.bidirectional_source,
        "combine_native_geometry",
        lambda _forward, _reverse, _combine: rejected_native,
    )

    scalar, se3, proof, _paths = probe.validate_bidirectional_appendix(**fixture)

    assert scalar == []
    assert se3 == []
    assert proof["native_rejected_rows_retained_as_frozen_fallback"] == 1


def test_appendix_rejects_external_gt(tmp_path, monkeypatch):
    fixture = _appendix_fixture(tmp_path, monkeypatch, external_gt=True)

    with pytest.raises(ValueError, match="ground truth|external supervision"):
        probe.validate_bidirectional_appendix(**fixture)


def test_appendix_hashguards_optional_failed_report_without_admitting_it(tmp_path, monkeypatch):
    fixture = _appendix_fixture(tmp_path, monkeypatch, include_optional_failed_report=True)

    scalar, se3, proof, paths = probe.validate_bidirectional_appendix(**fixture)

    assert len(scalar) == len(se3) == 1
    assert proof["scalar_candidate_count"] == 1
    assert any(Path(path).name == "failed_report.json" for path in paths)


def test_appendix_reference_binding_skip_does_not_fail_record(tmp_path, monkeypatch):
    fixture = _appendix_fixture(tmp_path, monkeypatch, bind_reason="missing_reference_endpoint")

    scalar, se3, proof, _paths = probe.validate_bidirectional_appendix(**fixture)

    assert scalar == []
    assert se3 == []
    assert proof["skipped_reference_binding_candidates"] == {"missing_reference_endpoint": 1}


def test_appendix_recomputes_scalar_baseline_provenance(tmp_path, monkeypatch):
    fixture = _appendix_fixture(tmp_path, monkeypatch)
    appendix_path = Path(fixture["stage_record"]["recovery_appendix_path"])
    appendix = json.loads(appendix_path.read_text())
    appendix["observations"][0]["native_observation"]["scalar_bidirectional_baseline"]["scale"] = 1.25
    appendix_path.write_text(json.dumps(appendix), encoding="utf-8")
    fixture["stage_record"]["recovery_appendix_sha256"] = probe._path_hash(appendix_path)

    with pytest.raises(ValueError, match="saved bidirectional native geometry mismatch|saved scalar bidirectional baseline mismatch"):
        probe.validate_bidirectional_appendix(**fixture)


def test_appendix_builds_matched_scalar_and_se3_candidates(tmp_path, monkeypatch):
    fixture = _appendix_fixture(tmp_path, monkeypatch)

    scalar, se3, proof, _paths = probe.validate_bidirectional_appendix(**fixture)

    assert len(scalar) == len(se3) == 1
    assert scalar[0]["bidirectional_geometry_source"] == "scalar_bidirectional_baseline"
    assert se3[0]["bidirectional_geometry_source"] == "bidirectional_se3_midpoint"
    assert proof["external_ground_truth_used"] is False


def test_build_matched_shared_rows_skips_native_pairs_outside_fixed_layout():
    times = np.array([0.0, 0.01, 0.02])
    original = [{
        "accepted": True,
        "first_index": 0,
        "second_index": 1,
        "first_t_sec": 0.0,
        "second_t_sec": 0.01,
        "metric_displacement_camera_i_m": [0.0, 0.0, 0.0],
        "metric_displacement_frame": "body_i",
        "pnp_inlier_ratio": 0.5,
    }]
    existing = [_candidate("left", 0, 1, 0.5)]
    scalar = [_candidate("left", 1, 2, 0.7)]
    se3 = []

    scalar_rows, scalar_all, scalar_diag, se3_rows, se3_all, se3_diag, scalar_fallback, se3_fallback = (
        probe.build_matched_shared_rows(times, original, existing, scalar, se3)
    )

    assert probe.shared_row_keys(scalar_rows) == probe.shared_row_keys(se3_rows)
    assert len(scalar_all) == len(se3_all) == 1
    assert scalar_diag["native_pairs_outside_fixed_layout_skipped"] == 1
    assert se3_diag["native_pairs_outside_fixed_layout_skipped"] == 0
    assert scalar_fallback == se3_fallback == set()


def test_fixed_layout_native_replaces_same_eye_even_with_lower_confidence():
    rows = [{"first_index": 0, "second_index": 1, "first_t_sec": 0.0, "second_t_sec": 0.01}]
    existing = [_candidate("left", 0, 1, 0.9, vector=[1.0, 0.0, 0.0])]
    native = [_candidate("left", 0, 1, 0.5, vector=[2.0, 0.0, 0.0])]

    selected, fallback, diag = probe.select_candidates_for_fixed_layout(
        rows, existing, native, label="scalar",
    )

    assert fallback == set()
    assert diag["native_same_eye_replacement_count"] == 1
    assert selected[0]["metric_displacement_camera_i_m"] == [2.0, 0.0, 0.0]


def test_native_confidence_refresh_allows_real_physical_transform():
    class _Rotations:
        def as_matrix(self):
            return np.repeat(np.eye(3)[None, :, :], 2, axis=0)

    class _State:
        times = np.array([0.0, 0.01])
        rotations = _Rotations()

    fixed_rows = [{
        "accepted": True,
        "first_index": 0,
        "second_index": 1,
        "first_t_sec": 0.0,
        "second_t_sec": 0.01,
        "metric_displacement_camera_i_m": [0.0, 0.0, 0.0],
        "metric_displacement_frame": "body_i",
        "scale": 1.0,
        "pnp_inlier_ratio": 0.1,
        "rotation_error_deg": 0.0,
    }]
    scalar_native = [_candidate("left", 0, 1, 0.8, vector=[0.02, 0.0, 0.0])]
    se3_native = [_candidate("left", 0, 1, 0.8, vector=[0.03, 0.0, 0.0])]

    scalar_rows, scalar_all, _scalar_diag, se3_rows, se3_all, _se3_diag, scalar_fallback, se3_fallback = (
        probe.build_matched_shared_rows(_State.times, fixed_rows, [], scalar_native, se3_native)
    )
    scalar_out, _ = probe.transform_with_frozen_fallback(_State, scalar_all, scalar_rows, scalar_fallback)
    se3_out, _ = probe.transform_with_frozen_fallback(_State, se3_all, se3_rows, se3_fallback)

    assert scalar_rows[0]["pnp_inlier_ratio"] == 0.8
    assert se3_rows[0]["pnp_inlier_ratio"] == 0.8
    assert scalar_out[0]["metric_displacement_camera_i_m"] == [0.02, 0.0, 0.0]
    assert se3_out[0]["metric_displacement_camera_i_m"] == [0.03, 0.0, 0.0]


def test_assert_same_layout_rejects_confidence_change():
    scalar = [{
        "first_index": 0,
        "second_index": 1,
        "first_t_sec": 0.0,
        "second_t_sec": 0.01,
        "pnp_inlier_ratio": 0.5,
    }]
    se3 = [{**scalar[0], "pnp_inlier_ratio": 0.500000000002}]

    with pytest.raises(ValueError, match="changed shared confidence"):
        probe.assert_same_layout_and_confidence(scalar, se3)


def test_validate_recovery_reference_binds_manifest_graph_output_and_rows(tmp_path):
    root = tmp_path / "recovery"
    artifact = root / "rec" / probe.RECOVERY_REFERENCE_VARIANT
    source_stage = tmp_path / "source_stage"
    preflight = _write_json(source_stage / "preflight_report.json", {"schema": "source"})
    trajectory = artifact / "body_trajectory_fused.csv"
    trajectory.parent.mkdir(parents=True, exist_ok=True)
    trajectory.write_text("t,x,y,z\n", encoding="utf-8")
    rows = [{"accepted": True, "first_index": 0, "second_index": 1}]
    shared = _write_json(artifact / "shared_stereo_observations.json", rows)
    candidate = _write_json(artifact / "candidate_manifest.json", {
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": str(tmp_path / "session"),
        "output_estimate_sha256": probe._path_hash(trajectory),
    })
    graph = _write_json(artifact / "graph_report.json", {
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "output_frame": "body_imu_origin",
    })
    summary = _write_json(root / "summary.json", {
        "schema": "umi_independent_ir_recovery_paired_probe_v1",
        "status": "COMPLETED",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "source_stage": str(source_stage),
        "source_stage_preflight_sha256": probe._path_hash(preflight),
    })

    _artifact, fixed_rows, proof, paths = probe.validate_recovery_reference(
        {"id": "rec", "session": str(tmp_path / "session")},
        root,
        {"id": "rec"},
    )

    assert fixed_rows == rows
    assert proof["fixed_shared_row_count"] == 1
    assert set(paths) >= {summary, preflight, candidate, graph, shared, trajectory}


def test_run_record_passes_recovery_reference_paths_to_run_arm(monkeypatch, tmp_path):
    class _Rotations:
        def as_matrix(self):
            return np.repeat(np.eye(3)[None, :, :], 2, axis=0)

    class _State:
        times = np.array([0.0, 0.01])
        rotations = _Rotations()

    fixed_rows = [{
        "accepted": True,
        "first_index": 0,
        "second_index": 1,
        "first_t_sec": 0.0,
        "second_t_sec": 0.01,
        "metric_displacement_camera_i_m": [0.0, 0.0, 0.0],
        "metric_displacement_frame": "body_i",
        "scale": 1.0,
        "pnp_inlier_ratio": 0.1,
        "rotation_error_deg": 0.0,
    }]
    existing_path = tmp_path / "existing_report.json"
    appendix_path = tmp_path / "appendix.json"
    recovery_path = tmp_path / "recovery_shared.json"
    constant_path = tmp_path / "constant.json"
    combined_path = tmp_path / "combined.json"
    for path in (existing_path, appendix_path, recovery_path, constant_path, combined_path):
        path.write_text("{}", encoding="utf-8")
    constant_artifact = tmp_path / "constant_artifact"
    combined_artifact = tmp_path / "combined_artifact"
    for name in ("candidate_manifest.json", "graph_report.json", "body_trajectory_fused.csv"):
        _write_json(combined_artifact / name, {"name": name})
    captured = []
    monkeypatch.setattr(probe, "validate_source_stage_record", lambda record_id, source_stage: {"id": record_id})
    monkeypatch.setattr(probe.paired.base, "validate_record_sources", lambda record: None)
    monkeypatch.setattr(probe.paired.base, "validate_baseline_artifact", lambda record, artifact: ({}, {}))
    monkeypatch.setattr(probe.paired.base, "load_bound_reference", lambda record: _State)
    monkeypatch.setattr(probe.paired.base, "validate_baseline_trajectory_identity", lambda *args: None)
    monkeypatch.setattr(
        probe,
        "validate_recovery_reference",
        lambda record, recovery_reference, stage_record: (
            tmp_path / "recovery_artifact",
            fixed_rows,
            {"proof": "recovery"},
            [recovery_path],
        ),
    )
    monkeypatch.setattr(
        probe.paired,
        "load_refined_all_eye_candidates",
        lambda *args: ([], {"left": {}, "right": {}}, [existing_path], {"override": "sha"}),
    )
    monkeypatch.setattr(
        probe,
        "validate_bidirectional_appendix",
        lambda *args: (
            [_candidate("left", 0, 1, 0.8, vector=[0.02, 0.0, 0.0])],
            [_candidate("left", 0, 1, 0.8, vector=[0.03, 0.0, 0.0])],
            {"proof": "appendix"},
            [appendix_path],
        ),
    )
    monkeypatch.setattr(
        probe.paired.physical,
        "validate_constant_artifact",
        lambda *args: (constant_artifact, {}, {}, [constant_path]),
    )
    monkeypatch.setattr(
        probe.paired,
        "validate_combined_reference",
        lambda *args: (combined_artifact, {}, [combined_path]),
    )
    monkeypatch.setattr(probe.paired, "snapshot_paths", lambda paths: {str(path): "sha" for path in paths})
    monkeypatch.setattr(probe.paired, "assert_hashes_unchanged", lambda hashes: None)

    def capture_run_arm(**kwargs):
        captured.append(kwargs["raw_paths"])
        return {"score": {"result": "PASS"}}

    monkeypatch.setattr(probe.paired, "run_arm", capture_run_arm)

    result = probe.run_record(
        {"id": "rec", "session": str(tmp_path / "session"), "vins_dir": str(tmp_path / "vins")},
        tmp_path / "baseline",
        tmp_path / "constant",
        tmp_path / "combined",
        tmp_path / "recovery",
        tmp_path / "out",
        {"path": str(tmp_path / "source_preflight.json"), "refined_by_id": {"rec": {"id": "rec"}}},
        {},
    )

    assert result["status"] == "COMPLETED"
    assert len(captured) == 2
    for raw_paths in captured:
        assert existing_path in raw_paths
        assert appendix_path in raw_paths
        assert recovery_path in raw_paths


def test_aggregate_reports_bidirectional_variants_not_paired_globals():
    result = {
        "variants": {
            probe.SCALAR_VARIANT: {"score": {"result": "PASS", "ate_translation_max_m": 0.006}},
            probe.SE3_VARIANT: {"score": {"result": "FAIL", "ate_translation_max_m": 0.014}},
        }
    }

    aggregates = probe.aggregate([result])

    assert set(aggregates) == {probe.SCALAR_VARIANT, probe.SE3_VARIANT}
    assert aggregates[probe.SCALAR_VARIANT]["scored_count"] == 1
    assert aggregates[probe.SE3_VARIANT]["worst_max_m"] == 0.014


def test_paired_run_arm_does_not_replay_old_combined_for_bidirectional_scalar(monkeypatch, tmp_path):
    baseline_artifact = tmp_path / "baseline"
    constant_artifact = tmp_path / "constant"
    variant_dir = tmp_path / "variant"
    combined_artifact = tmp_path / "combined"
    for directory in (baseline_artifact, constant_artifact, variant_dir, combined_artifact):
        directory.mkdir(parents=True, exist_ok=True)
    (constant_artifact / "local_motion_factors.json").write_text("[]", encoding="utf-8")
    estimate = variant_dir / "body_trajectory_fused.csv"
    estimate.write_text("t,x,y,z\n", encoding="utf-8")
    for name in ("candidate_manifest.json", "graph_report.json"):
        _write_json(variant_dir / name, {
            "external_ground_truth_used": False,
            "slam_supervision": False,
        })
    called = {"replay": 0, "metadata": 0}

    def fake_solver(*args):
        probe.paired.physical.corpus.score_frozen({}, estimate)
        return {"score": {"result": "PASS"}}

    monkeypatch.setattr(probe.paired.physical, "run_solver_variant", fake_solver)
    monkeypatch.setattr(probe.paired, "read_json", lambda path: [])
    monkeypatch.setattr(probe.paired, "file_hash", lambda path: "sha")
    monkeypatch.setattr(probe.paired, "snapshot_paths", lambda paths: {str(path): "sha" for path in paths})
    monkeypatch.setattr(probe.paired, "assert_hashes_unchanged", lambda hashes: None)
    monkeypatch.setattr(probe.paired.physical.corpus, "score_frozen", lambda *args, **kwargs: {"result": "PASS"})

    def forbid_replay(*args, **kwargs):
        called["replay"] += 1
        raise AssertionError("old combined replay must not run for bidirectional scalar")

    def metadata(*args, **kwargs):
        called["metadata"] += 1

    monkeypatch.setattr(probe.paired, "validate_combined_replay", forbid_replay)
    monkeypatch.setattr(probe.paired, "postprocess_candidate_metadata", metadata)

    result = probe.paired.run_arm(
        record={"id": "rec"},
        baseline_artifact=baseline_artifact,
        variant=probe.SCALAR_VARIANT,
        variant_dir=variant_dir,
        state=object(),
        baseline_candidate={},
        stereo_rows=[],
        stereo_report={},
        raw_paths=[],
        constant_artifact=constant_artifact,
        constant_paths=[],
        frozen_hashes={},
        combined_reference_artifact=combined_artifact,
        source_override_sha256={},
        arm_role="native_scalar_bidirectional_baseline",
        control_replay=None,
    )

    assert result["score"]["result"] == "PASS"
    assert called == {"replay": 0, "metadata": 1}


def test_frozen_code_paths_uses_captured_original_not_patched_recursion(monkeypatch, tmp_path):
    original = probe.ORIGINAL_PAIRED_FROZEN_CODE_PATHS
    monkeypatch.setattr(probe.paired, "frozen_code_paths", probe.frozen_code_paths)

    paths = probe.frozen_code_paths(tmp_path)

    assert paths[: len(original(tmp_path))] == original(tmp_path)
