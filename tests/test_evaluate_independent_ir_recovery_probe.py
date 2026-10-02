from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import evaluate_independent_ir_recovery_probe as probe


def write(path, data):
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    times = np.array([0., .02, .04])
    session = tmp_path / "session"
    session.mkdir()
    record = {"id": "take", "session": str(session)}
    parent = write(tmp_path / "parent.json", {})
    parent_row = {"refined_left_sources": [], "refined_right_sources": [],
                  "source_override_sha256": {}, "independent_right_geometry_refresh": {}}
    monkeypatch.setattr(probe.paired, "load_source_stage", lambda _: {"refined_by_id": {"take": parent_row}})
    monkeypatch.setattr(probe.paired.physical, "validate_eye_metadata", lambda *_: {"effective_body_T_camera": np.eye(4).tolist()})
    contexts, reports, trajectories, entries = {}, {}, {}, []
    for eye in ("left", "right"):
        trajectory = write(tmp_path / f"{eye}.csv", {})
        trajectories[eye] = trajectory
        original = {"accepted": False, "reason": "pnp_failed", "first_index": 1,
                    "second_index": 2, "first_t_sec": .02, "second_t_sec": .04}
        source = write(tmp_path / f"{eye}.json", {"result": "PASS", "trajectory": str(trajectory), "observations": [original]})
        scale = .4
        reports[eye] = {"merged_report_paths": [str(source)], "scale_m_per_mast3r_unit": scale}
        contexts[eye] = {"reference_scale": scale, "reference_trajectory_path": str(trajectory),
                         "reference_trajectory_sha256": probe.paired.file_hash(trajectory),
                         "merged_report_paths": [str(source)],
                         "report_sha256": {str(source): probe.paired.file_hash(source)}}
        native = {**original, "accepted": True, "method": "sift", "scale": .4,
                  "rotation_error_deg": 0., "pnp_inlier_ratio": .8,
                  "metric_displacement_camera_i_m": [.01, 0., 0.],
                  "metric_displacement_frame": f"infrared_{eye}_camera_i",
                  "pnp_rotation_quaternion_xyzw": [0., 0., 0., 1.]}
        entries.append({"eye": eye, "source_report_path": str(source),
                        "source_report_sha256": probe.paired.file_hash(source),
                        "source_report_result": "PASS", "source_observation_index": 0,
                        "original_observation": original, "native_observation": native,
                        "raw_forward_summary": {"accepted": True}, "raw_reverse_summary": {"accepted": True}})
    monkeypatch.setattr(probe.paired.physical, "eye_trajectory_path_from_baseline", lambda _, eye: trajectories[eye])
    monkeypatch.setattr(probe.paired.source_eval, "validate_candidate_hashes", lambda *_: None)
    monkeypatch.setattr(probe.paired.fusion, "load_trajectory", lambda _: (times, None, None, None))
    appendix = {"schema": probe.APPENDIX_SCHEMA, "external_ground_truth_used": False,
                "slam_supervision": False, "id": "take", "session": str(session),
                "source_stage_preflight": str(parent), "source_stage_preflight_sha256": probe.paired.file_hash(parent),
                "eye_contexts": contexts, "observations": entries,
                "consumed_source_guard": {"guarded_after_verified": True, "guarded_before_sha256": {
                    str(p): {"path": str(p), "sha256": probe.paired.file_hash(p)}
                    for p in [parent, *trajectories.values(), *(Path(x["source_report_path"]) for x in entries)]}}}
    path = tmp_path / "appendix.json"
    stage = deepcopy(parent_row)

    def run():
        write(path, appendix)
        stage.update(recovery_appendix_path=str(path), recovery_appendix_sha256=probe.paired.file_hash(path))
        return probe.validate_recovery_appendix(record, stage, {}, times, reports)

    return appendix, stage, reports, run


def test_symmetric_native_rows_admitted_without_altering_report(fixture):
    appendix, stage, reports, run = fixture
    before = deepcopy(reports)
    candidates, diagnostic, paths = run()
    assert {c["eye"] for c in candidates} == {"left", "right"}
    assert len(candidates) == 2
    assert diagnostic["recovered_eye_candidate_count"] == 2
    assert reports == before
    assert len(paths) >= 5


def test_native_failure_retained_without_factor(fixture):
    appendix, _, _, run = fixture
    appendix["observations"][0]["native_observation"]["accepted"] = False
    candidates, diagnostic, _ = run()
    assert len(candidates) == 1 and candidates[0]["eye"] == "right"
    assert diagnostic["native_rejected_rows_retained"] == 1


def test_raw_camera_and_body_metric_paths_are_distinct(fixture, tmp_path, monkeypatch):
    appendix, _, _, run = fixture
    metric_paths = {eye: write(tmp_path / f"{eye}_body_metric.csv", {"metric": True})
                    for eye in ("left", "right")}
    monkeypatch.setattr(probe.paired.physical, "eye_trajectory_path_from_baseline", lambda _, eye: metric_paths[eye])
    monkeypatch.setattr(probe.paired.source_eval, "validate_candidate_hashes", lambda *_: None)
    candidates, _, consumed = run()
    assert len(candidates) == 2
    assert set(metric_paths.values()) <= set(consumed)
    assert all(Path(appendix["eye_contexts"][eye]["reference_trajectory_path"]) != metric_paths[eye]
               for eye in ("left", "right"))


def test_raw_camera_metric_timeline_mismatch_fails(fixture, tmp_path, monkeypatch):
    _, _, _, run = fixture
    metric_paths = {eye: write(tmp_path / f"{eye}_body_metric.csv", {}) for eye in ("left", "right")}
    monkeypatch.setattr(probe.paired.physical, "eye_trajectory_path_from_baseline", lambda _, eye: metric_paths[eye])
    monkeypatch.setattr(probe.paired.source_eval, "validate_candidate_hashes", lambda *_: None)
    monkeypatch.setattr(probe.paired.fusion, "load_trajectory", lambda path: (
        np.array([0., .02, .05 if Path(path) in metric_paths.values() else .04]), None, None, None))
    with pytest.raises(ValueError, match="timeline"):
        run()


def test_raw_camera_path_must_match_admitted_source_report(fixture):
    appendix, _, _, run = fixture
    entry = appendix["observations"][0]
    source = Path(entry["source_report_path"])
    report = json.loads(source.read_text())
    report["trajectory"] = str(source.parent / "wrong_camera.csv")
    write(source, report)
    digest = probe.paired.file_hash(source)
    entry["source_report_sha256"] = digest
    appendix["eye_contexts"]["left"]["report_sha256"][str(source)] = digest
    appendix["consumed_source_guard"]["guarded_before_sha256"][str(source)]["sha256"] = digest
    with pytest.raises(ValueError, match="source report trajectory"):
        run()


def test_raw_camera_path_must_have_consumed_source_guard(fixture):
    appendix, _, _, run = fixture
    trajectory = appendix["eye_contexts"]["left"]["reference_trajectory_path"]
    appendix["consumed_source_guard"]["guarded_before_sha256"].pop(trajectory)
    with pytest.raises(ValueError, match="trajectory.*guard"):
        run()


def test_metric_trajectory_must_remain_baseline_bound(fixture, monkeypatch):
    _, _, _, run = fixture
    def reject(*_):
        raise ValueError("metric trajectory not baseline-bound")
    monkeypatch.setattr(probe.paired.source_eval, "validate_candidate_hashes", reject)
    with pytest.raises(ValueError, match="baseline-bound"):
        run()


def test_valid_hash_appendix_cannot_admit_original_low_excitation(fixture):
    appendix, _, _, run = fixture
    entry = appendix["observations"][0]
    source = Path(entry["source_report_path"])
    original_report = json.loads(source.read_text())
    original_report["observations"][0]["reason"] = "translation_excitation_low"
    write(source, original_report)
    digest = probe.paired.file_hash(source)
    entry["original_observation"] = original_report["observations"][0]
    entry["source_report_sha256"] = digest
    appendix["eye_contexts"]["left"]["report_sha256"][str(source)] = digest
    appendix["consumed_source_guard"]["guarded_before_sha256"][str(source)]["sha256"] = digest
    with pytest.raises(ValueError, match="low-excitation"):
        run()


@pytest.mark.parametrize("mutation,match", [
    (lambda a,s,r: a.__setitem__("external_ground_truth_used", True), "ground"),
    (lambda a,s,r: a["eye_contexts"]["left"].__setitem__("reference_scale", .5), "scale"),
    (lambda a,s,r: a["eye_contexts"]["left"].__setitem__("merged_report_paths", []), "admission"),
    (lambda a,s,r: a["observations"][0].__setitem__("source_report_result", "FAIL"), "PASS"),
    (lambda a,s,r: a["observations"][0]["native_observation"].__setitem__("second_t_sec", .03), "endpoints"),
    (lambda a,s,r: a["observations"][0]["raw_reverse_summary"].__setitem__("accepted", False), "bidirectional"),
    (lambda a,s,r: a["observations"][0]["native_observation"].__setitem__("metric_displacement_frame", "body_i"), "measurement"),
    (lambda a,s,r: a["observations"].append(deepcopy(a["observations"][0])), "duplicate"),
    (lambda a,s,r: s.__setitem__("source_override_sha256", {"bad": "bad"}), "frozen"),
    (lambda a,s,r: a.pop("consumed_source_guard"), "guard"),
])
def test_invalid_recovery_evidence_fails_closed(fixture, mutation, match):
    appendix, stage, reports, run = fixture
    mutation(appendix, stage, reports)
    with pytest.raises(ValueError, match=match):
        run()


def test_process_local_adapters_restore_on_exception(monkeypatch):
    names = ("load_refined_all_eye_candidates", "refresh_shared_row_confidences",
             "run_record", "source_upgrade_scope", "frozen_code_paths", "SCHEMA", "REFINED_VARIANT")
    original = {k: getattr(probe.paired, k) for k in names}
    def fail(_):
        assert probe.paired.REFINED_VARIANT == probe.REFINED_VARIANT
        raise RuntimeError("simulated")
    monkeypatch.setattr(probe.paired, "main", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        probe.main([])
    assert all(getattr(probe.paired, k) is original[k] for k in names)


def test_process_adapters_append_one_resolved_physical_pair(tmp_path, monkeypatch):
    times = np.array([0., .02, .04])
    rows = [{"accepted": True, "first_index": 0, "second_index": 1,
             "first_t_sec": 0., "second_t_sec": .02, "pnp_inlier_ratio": .8,
             "metric_displacement_camera_i_m": [.01, 0., 0.],
             "metric_displacement_frame": "body_i", "scale": 1., "rotation_error_deg": 0.}]
    baseline = tmp_path / "baseline"
    original_path = probe.paired.baseline_artifact_dir(baseline, "take") / "shared_stereo_observations.json"
    original_path.parent.mkdir(parents=True)
    write(original_path, rows)
    def candidate(eye, first, second, delta):
        return {"eye": eye, "first_index": first, "second_index": second,
                "reference_first_index": first, "reference_second_index": second,
                "first_t_sec": float(times[first]), "second_t_sec": float(times[second]),
                "metric_displacement_camera_i_m": delta,
                "metric_displacement_frame": f"infrared_{eye}_camera_i",
                "observation_confidence": .8, "body_t_camera": np.eye(4).tolist()}
    existing = [candidate("left", 0, 1, [.01, 0., 0.])]
    recovered = [candidate("left", 1, 2, [.01, 0., 0.]), candidate("right", 1, 2, [.03, 0., 0.])]
    monkeypatch.setattr(probe.paired, "load_refined_all_eye_candidates", lambda *_: (existing, {}, [], {}))
    monkeypatch.setattr(probe, "validate_recovery_appendix", lambda *_: (recovered, {"verified": True}, []))
    def run(record, baseline, *_args, **_kwargs):
        candidates, reports, _, _ = probe.paired.load_refined_all_eye_candidates(record, {}, times, {})
        templates, diagnostic = probe.paired.refresh_shared_row_confidences(rows, candidates)
        state = SimpleNamespace(times=times, rotations=Rotation.identity(3))
        physical_rows, _ = probe.paired.physical.transform_shared_rows(state, candidates, templates)
        assert len(physical_rows) == 2 and diagnostic["appended_pair_count"] == 1
        np.testing.assert_allclose(physical_rows[1]["metric_displacement_camera_i_m"], [.02, 0., 0.])
        assert reports["recovery_appendix"]["verified"] is True
        assert rows[0]["metric_displacement_camera_i_m"] == [.01, 0., 0.]
        return 0
    monkeypatch.setattr(probe.paired, "run_record", run)
    monkeypatch.setattr(probe.paired, "main", lambda _: probe.paired.run_record({"id": "take"}, baseline))
    assert probe.main([]) == 0
