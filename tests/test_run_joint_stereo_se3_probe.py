import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_joint_stereo_se3_probe as probe


def state(times=(0.02, 0.086666667, 0.12)):
    imu = np.arange(0., .151, .0025)
    return SimpleNamespace(times=np.array(times), mono=np.array(times),
        imu_times=imu, gyro=np.zeros((len(imu), 3)), config={"td_s": -.009109323})


def test_camera_gap_keeps_verified_gyro_with_calibrated_sigma(tmp_path):
    raw = tmp_path / "imu.bin"
    raw.write_bytes(b"raw imu")
    s = state()
    factors, diag = probe.gyro_factors(s, .00103, raw)
    assert len(factors) == 2
    assert diag["camera_only_gap_count"] == 1
    for index, factor in enumerate(factors):
        assert factor["imu_coverage_verified"] is True
        assert factor["imu_sample_count"] >= 2
        assert factor["imu_source_sha256"] == probe.base.file_hash(raw)
        assert factor["max_imu_sample_gap_s"] < .003
        assert factor["rotation_sigma_rad"] == pytest.approx(
            .00103 * np.sqrt(s.times[index + 1] - s.times[index]))
        np.testing.assert_allclose(factor["delta_rotation_body_i_to_body_j"], np.eye(3))


@pytest.mark.parametrize("failure", ["missing_samples", "unordered", "outside", "noise"])
def test_gyro_refuses_invalid_coverage(tmp_path, failure):
    raw = tmp_path / "imu.bin"
    raw.write_bytes(b"imu")
    s = state()
    density = .00103
    if failure == "missing_samples":
        keep = (s.imu_times < .045) | (s.imu_times > .080)
        s.imu_times, s.gyro = s.imu_times[keep], s.gyro[keep]
    elif failure == "unordered":
        s.imu_times[5] = s.imu_times[4]
    elif failure == "outside":
        s.mono[0] = 0.
    else:
        density = 0.
    with pytest.raises(ValueError):
        probe.gyro_factors(s, density, raw)


def proof_fixture(tmp_path):
    artifact = tmp_path / "baseline/case/both"
    artifact.mkdir(parents=True)
    learned = artifact / "local_motion_factors.json"
    stereo = artifact / "shared_stereo_observations.json"
    probe.base.write_json(learned, [{"dummy": "learned"}])
    probe.base.write_json(stereo, [])
    record = {"id": "case", "session": str(tmp_path / "session")}
    proof_root = tmp_path / "proof"
    proof_path = proof_root / "case/selected/candidate_manifest.json"
    value = {"external_ground_truth_used": False, "slam_supervision": False,
        "session": record["session"],
        "input_sha256": probe.base.snapshot_hashes([learned, stereo]),
        "source_factor_reconstruction": {"factor_count": 1,
            "max_metric_displacement_abs_error_m": 0.}}
    probe.base.write_json(proof_path, value)
    return record, artifact, proof_root, proof_path, value


def test_factor_reconstruction_proof_binds_exact_original_files(tmp_path):
    record, artifact, proof_root, _, _ = proof_fixture(tmp_path)
    factors, _ = probe.verify_factor_proof(record, artifact, proof_root)
    assert factors == [{"dummy": "learned"}]
    probe.base.write_json(artifact / "local_motion_factors.json", [])
    with pytest.raises(ValueError, match="changed since reconstruction"):
        probe.verify_factor_proof(record, artifact, proof_root)


@pytest.mark.parametrize("field,value", [
    ("external_ground_truth_used", True),
    ("session", "/wrong/session"),
    ("source_factor_reconstruction", {"factor_count": 1,
        "max_metric_displacement_abs_error_m": .001}),
])
def test_reject_untrusted_or_inexact_factor_proof(tmp_path, field, value):
    record, artifact, proof_root, path, proof = proof_fixture(tmp_path)
    proof[field] = value
    probe.base.write_json(path, proof)
    with pytest.raises(ValueError):
        probe.verify_factor_proof(record, artifact, proof_root)


def test_existing_output_never_overwritten(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    with pytest.raises(SystemExit):
        probe.main(["--manifest", "missing", "--baseline", "missing",
            "--factor-proof", "missing", "--output", str(output)])


def test_code_change_stops_without_scoring(monkeypatch):
    monkeypatch.setattr(probe.base, "code_changed", lambda _: True)
    with pytest.raises(probe.base.StopCodeChanged):
        probe.check_frozen({})


def orchestration_state():
    times = np.asarray([1.0, 1.02, 1.04])
    return SimpleNamespace(
        times=times,
        mono=times.copy(),
        positions=np.asarray([[0.0, 0.0, 0.0], [0.02, 0.0, 0.0], [0.04, 0.0, 0.0]]),
        rotations=probe.Rotation.identity(3),
        rows=[
            {"t_sec": f"{timestamp:.9f}", "x": "0", "y": "0", "z": "0",
             "qw": "1", "qx": "0", "qy": "0", "qz": "0"}
            for timestamp in times
        ],
        imu_times=np.asarray([0.99, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05]),
        gyro=np.zeros((7, 3)),
        config={"td_s": -0.009109323},
    )


def patch_run_record_orchestration(
    monkeypatch,
    tmp_path,
    *,
    solver_success=True,
    solution_mutation=None,
    score_mutates_estimate=False,
):
    state = orchestration_state()
    candidate = {
        "input_sha256": {str((tmp_path / "input.txt").resolve()): "hash"},
        "eye_reports": {},
    }
    graph = {"graph": "ok"}
    learned = [{"first_index": 0, "second_index": 1, "confidence": 1.0}]
    original_stereo = [{"first_index": 0, "second_index": 1, "pnp_inlier_ratio": 0.8}]
    eye_candidates = [{"eye": "left", "reference_first_index": 0, "reference_second_index": 1}]
    stereo = [{"first_index": 0, "second_index": 1, "confidence": 0.8}]
    gyro = [{"first_index": 0, "second_index": 1, "confidence": 1.0}]
    proof_path = tmp_path / "proof" / "case" / "selected" / "candidate_manifest.json"
    source_path = tmp_path / "source.txt"
    source_path.write_text("source\n", encoding="utf-8")
    (tmp_path / "input.txt").write_text("input\n", encoding="utf-8")
    (tmp_path / "case" / "session" / "external_imu").mkdir(parents=True)
    (tmp_path / "case" / "session" / "external_imu" / "imu.bin").write_bytes(b"imu")

    monkeypatch.setattr(probe.base, "validate_record_sources", lambda record: None)
    monkeypatch.setattr(probe.base, "validate_baseline_artifact", lambda record, artifact: (candidate, graph))
    monkeypatch.setattr(probe.base, "load_bound_reference", lambda record: state)
    monkeypatch.setattr(probe.base, "validate_baseline_trajectory_identity", lambda artifact, st, gr: None)
    monkeypatch.setattr(probe, "verify_factor_proof", lambda record, artifact, proof_root: (learned, proof_path))
    monkeypatch.setattr(probe.base, "read_json", lambda path: original_stereo if Path(path).name == "shared_stereo_observations.json" else [])
    monkeypatch.setattr(probe.physical, "load_all_eye_candidates", lambda record, cand, times: (eye_candidates, {"eye": "ok"}, [source_path]))
    monkeypatch.setattr(probe.physical, "transform_shared_rows", lambda st, eyes, rows: (rows, {"identity": "ok"}))
    monkeypatch.setattr(probe, "solver_stereo_factors", lambda times, eyes, rows: (stereo, {"se3": "ok"}))
    monkeypatch.setattr(probe, "configured_gyro_noise", lambda: 0.001)
    monkeypatch.setattr(probe, "gyro_factors", lambda st, noise, imu_path: (gyro, {"gyro": "ok"}))
    monkeypatch.setattr(probe.base, "snapshot_hashes", lambda paths: {str(Path(path).resolve()): f"h{index}" for index, path in enumerate(paths)})
    monkeypatch.setattr(probe.base, "file_hash", lambda path: Path(path).read_text(encoding="utf-8") if Path(path).is_file() else "hash")
    monkeypatch.setattr(probe.base, "code_changed", lambda hashes: False)

    solve_calls = []

    def fake_solve(times, positions, rotations, stereo_arg, gyro_arg, learned_arg, *, optimize_rotations):
        solve_calls.append({
            "times": times.copy(),
            "stereo": deepcopy_jsonable(stereo_arg),
            "gyro": deepcopy_jsonable(gyro_arg),
            "learned": deepcopy_jsonable(learned_arg),
            "optimize_rotations": optimize_rotations,
        })
        output_times = times.copy()
        output_positions = positions.copy()
        output_rotations = rotations.copy()
        if solution_mutation == "times":
            output_times = times + 0.001
        if solution_mutation == "positions_shape":
            output_positions = positions[:2].copy()
        if solution_mutation == "first_position":
            output_positions[0, 0] += .001
        if solution_mutation == "non_so3":
            output_rotations[1, 0, 0] = 2.
        return {
            "times": output_times,
            "positions": output_positions,
            "rotations": output_rotations,
            "diagnostic": {
                "schema": "umi_joint_stereo_se3_pose_only_diagnostic_v1",
                "least_squares_success": solver_success,
            },
        }

    monkeypatch.setattr(probe, "solve_joint_stereo_se3", fake_solve)

    def write_trajectory(path, rows, positions, rotations):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            "estimate\n" + json.dumps({"rows": len(rows), "positions": np.asarray(positions).tolist()}) + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(probe.fusion, "write_trajectory", write_trajectory)
    score_calls = []

    def fake_score(record, estimate, output, work, stage):
        score_calls.append({"estimate": Path(estimate), "stage": stage})
        if score_mutates_estimate:
            Path(estimate).write_text("mutated\n", encoding="utf-8")
        return {"result": "PASS", "ate_translation_max_m": 0.001}

    monkeypatch.setattr(probe.corpus, "score_frozen", fake_score)
    return state, solve_calls, score_calls


def deepcopy_jsonable(value):
    return json.loads(json.dumps(value))


def test_run_record_variants_use_same_factors_and_distinct_rotation_flags(tmp_path, monkeypatch):
    _state, solve_calls, score_calls = patch_run_record_orchestration(monkeypatch, tmp_path)
    record = {"id": "case", "session": str(tmp_path / "case" / "session")}

    result = probe.run_record(record, tmp_path / "baseline", tmp_path / "proof", tmp_path / "out", {"code": "hash"})

    assert result["status"] == "COMPLETED"
    assert [call["optimize_rotations"] for call in solve_calls] == [False, True]
    assert solve_calls[0]["stereo"] == solve_calls[1]["stereo"]
    assert solve_calls[0]["gyro"] == solve_calls[1]["gyro"]
    assert solve_calls[0]["learned"] == solve_calls[1]["learned"]
    assert [call["stage"] for call in score_calls] == [
        "score_case_fixed_rotation",
        "score_case_joint_rotation_position",
    ]
    for variant in probe.VARIANTS:
        manifest = json.loads((tmp_path / "out" / "case" / variant / "candidate_manifest.json").read_text())
        assert manifest["variant"] == variant
        assert manifest["input_sha256"]["code"] == "hash"
        assert manifest["external_ground_truth_used"] is False
        assert manifest["accepted"] is False


def test_run_record_solver_convergence_failure_never_scores(tmp_path, monkeypatch):
    _state, _solve_calls, score_calls = patch_run_record_orchestration(
        monkeypatch,
        tmp_path,
        solver_success=False,
    )
    record = {"id": "case", "session": str(tmp_path / "case" / "session")}

    result = probe.run_record(record, tmp_path / "baseline", tmp_path / "proof", tmp_path / "out", {})

    assert result["status"] == "INCOMPLETE_VARIANTS"
    assert score_calls == []
    for variant in probe.VARIANTS:
        assert result["variants"][variant]["error_code"] == "VARIANT_FAILED"
        assert "did not converge" in result["variants"][variant]["error"]
        assert (tmp_path / "out" / "case" / variant / "solver_diagnostic.json").is_file()
        assert not (tmp_path / "out" / "case" / variant / "score").exists()


def test_run_record_estimate_mutation_during_scoring_is_detected(tmp_path, monkeypatch):
    _state, _solve_calls, score_calls = patch_run_record_orchestration(
        monkeypatch,
        tmp_path,
        score_mutates_estimate=True,
    )
    record = {"id": "case", "session": str(tmp_path / "case" / "session")}

    result = probe.run_record(record, tmp_path / "baseline", tmp_path / "proof", tmp_path / "out", {})

    assert len(score_calls) == len(probe.VARIANTS)
    assert result["status"] == "INCOMPLETE_VARIANTS"
    for variant in probe.VARIANTS:
        assert "estimate changed during scoring" in result["variants"][variant]["error"]


@pytest.mark.parametrize("mutation", ["times", "positions_shape"])
def test_run_record_all_frames_and_time_gauge_guard_blocks_scoring(tmp_path, monkeypatch, mutation):
    _state, _solve_calls, score_calls = patch_run_record_orchestration(
        monkeypatch,
        tmp_path,
        solution_mutation=mutation,
    )
    record = {"id": "case", "session": str(tmp_path / "case" / "session")}

    result = probe.run_record(record, tmp_path / "baseline", tmp_path / "proof", tmp_path / "out", {})

    assert score_calls == []
    assert result["status"] == "INCOMPLETE_VARIANTS"
    for variant in probe.VARIANTS:
        assert "solver changed timeline or returned invalid poses" in result["variants"][variant]["error"]


@pytest.mark.parametrize("mutation,message", [
    ("first_position", "fixed first-pose gauge"), ("non_so3", "not SO3")])
def test_pose_integrity_blocks_scoring(tmp_path, monkeypatch, mutation, message):
    _, _, score_calls = patch_run_record_orchestration(
        monkeypatch, tmp_path, solution_mutation=mutation)
    result = probe.run_record({"id": "case", "session": str(tmp_path / "case/session")},
        tmp_path / "baseline", tmp_path / "proof", tmp_path / "out", {})
    assert not score_calls
    assert all(message in value["error"] for value in result["variants"].values())


def test_source_failure_retains_both_unscored_variants(tmp_path, monkeypatch):
    _, _, score_calls = patch_run_record_orchestration(monkeypatch, tmp_path)
    def fail_proof(*args):
        raise ValueError("untrusted factor proof")
    monkeypatch.setattr(probe, "verify_factor_proof", fail_proof)
    result = probe.run_record({"id": "case", "session": str(tmp_path / "case/session")},
        tmp_path / "baseline", tmp_path / "proof", tmp_path / "out", {})
    assert not score_calls
    assert set(result["variants"]) == set(probe.VARIANTS)
    assert all(value["error_code"] == "SOURCE_FAILED" for value in result["variants"].values())
    aggregates = probe.physical.aggregate([result], 1, list(probe.VARIANTS))
    assert all(value["unscored_count"] == 1 for value in aggregates.values())


def patch_main_orchestration(monkeypatch, tmp_path, *, code_changed=False):
    records = [
        {"id": "failed_case", "session": str(tmp_path / "failed_case" / "session")},
        {"id": "ok_case", "session": str(tmp_path / "ok_case" / "session")},
    ]
    baseline_rows = {
        "failed_case": {"id": "failed_case", "status": "SOURCE_FAILED", "error": "baseline failed"},
        "ok_case": {"id": "ok_case", "status": "COMPLETED"},
    }
    manifest = tmp_path / "manifest.json"
    baseline = tmp_path / "baseline"
    proof = tmp_path / "proof"
    baseline.mkdir()
    proof.mkdir()
    manifest.write_text("{}\n", encoding="utf-8")
    (baseline / "summary.json").write_text("{}\n", encoding="utf-8")
    (proof / "summary.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(probe.base, "read_json", lambda path: {"status": "COMPLETED"})
    monkeypatch.setattr(probe.base, "validate_records", lambda data, dataset: records)
    monkeypatch.setattr(probe.base, "baseline_results", lambda data: baseline_rows)
    monkeypatch.setattr(probe.physical, "frozen_code_paths", lambda: [tmp_path / "code.py"])
    (tmp_path / "code.py").write_text("code\n", encoding="utf-8")
    monkeypatch.setattr(probe.base, "snapshot_hashes", lambda paths: {"code.py": "hash"})
    monkeypatch.setattr(probe.base, "file_hash", lambda path: "manifest-hash")
    monkeypatch.setattr(probe.base, "code_changed", lambda hashes: code_changed)
    monkeypatch.setattr(
        probe.physical,
        "aggregate",
        lambda results, total, variants: {
            "total_records": total,
            "result_count": len(results),
            "variants": variants,
        },
    )
    run_calls = []

    def fake_run_record(record, baseline_arg, proof_arg, output_arg, hashes):
        run_calls.append(record["id"])
        return {
            "id": record["id"],
            "status": "COMPLETED",
            "variants": {
                variant: {"score": {"result": "PASS"}, "artifact_dir": str(output_arg / record["id"] / variant)}
                for variant in probe.VARIANTS
            },
        }

    monkeypatch.setattr(probe, "run_record", fake_run_record)
    return manifest, baseline, proof, run_calls


def test_main_failed_baseline_stays_in_denominator_and_result_persists(tmp_path, monkeypatch):
    manifest, baseline, proof, run_calls = patch_main_orchestration(monkeypatch, tmp_path)
    output = tmp_path / "out"

    assert probe.main([
        "--manifest", str(manifest),
        "--baseline", str(baseline),
        "--factor-proof", str(proof),
        "--output", str(output),
    ]) == 3

    assert run_calls == ["ok_case"]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["dataset_count"] == 2
    assert summary["completed_count"] == 2
    assert summary["status"] == "COMPLETED_WITH_FAILURES"
    assert summary["aggregates"]["total_records"] == 2
    failed = summary["results"][0]
    assert failed["id"] == "failed_case"
    assert failed["status"] == "SOURCE_FAILED"
    assert failed["variants"] == {}
    assert failed["source_baseline_error"] == "baseline failed"
    persisted = json.loads((output / "failed_case" / "result.json").read_text())
    assert persisted["status"] == "SOURCE_FAILED"


def test_main_frozen_change_stops_before_mixing_results(tmp_path, monkeypatch):
    manifest, baseline, proof, run_calls = patch_main_orchestration(
        monkeypatch,
        tmp_path,
        code_changed=True,
    )
    output = tmp_path / "out"

    assert probe.main([
        "--manifest", str(manifest),
        "--baseline", str(baseline),
        "--factor-proof", str(proof),
        "--output", str(output),
    ]) == 2

    assert run_calls == []
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "STOP_CODE_CHANGED"
    assert summary["results"] == []
