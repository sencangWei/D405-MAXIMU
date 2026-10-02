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
    "learned_segment_probe", ROOT / "scripts/run_learned_segment_probe.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def make_fixture(tmp_path: Path, *, status: str = "COMPLETED") -> tuple[Path, Path, Path, dict]:
    session = tmp_path / "session"
    capture = tmp_path / "capture"
    vins = tmp_path / "vins"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for directory in (session / "external_imu", capture, vins, left, right):
        directory.mkdir(parents=True, exist_ok=True)
    (session / "d405_frames.csv").write_text("frames\n")
    (session / "external_imu/imu.bin").write_bytes(b"imu")
    (vins / "vio_corrected_stream.csv").write_text("vins\n")
    (vins / "run_acceptance.json").write_text("{}")
    record = {
        "id": "case_01",
        "session": str(session),
        "capture_dir": str(capture),
        "vins_dir": str(vins),
        "left_dir": str(left),
        "right_dir": str(right),
        "reference_manifest": str(tmp_path / "reference_manifest.json"),
    }
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"records": [record]})

    baseline = tmp_path / "baseline"
    artifact = baseline / "case_01" / "both"
    artifact.mkdir(parents=True)
    write_json(
        baseline / "summary.json",
        {
            "schema": "umi_dual_ir_development_regression_v1",
            "status": "COMPLETED",
            "results": [{"id": "case_01", "status": status, "variants": {"both": {}}}],
        },
    )
    if status == "COMPLETED":
        write_json(
            artifact / "candidate_manifest.json",
            {
                "schema": "umi_dual_ir_symmetric_experiment_v1",
                "external_ground_truth_used": False,
                "slam_supervision": False,
                "session": str(session.resolve()),
                "input_sha256": {},
                "shared_gauge": "VINS_body_world_first_node_only",
                "primary_eye": None,
                "shared_scale_state": False,
                "body_t_camera_in_solver": np.eye(4).tolist(),
                "policy_arguments": {
                    "max_correction_mm": None,
                    "correction_cap_mode": "global",
                    "eyes": "both",
                    "stereo_weight_policy": "observation",
                    "disable_learned_motion": False,
                    "learned_motion_consistency_limit_m": None,
                    "optional_stereo_policy": "reject_window",
                },
            },
        )
        write_json(
            artifact / "graph_report.json",
            {
                "schema": "umi_dual_ir_symmetric_graph_diagnostic_v1",
                "external_ground_truth_used": False,
                "slam_supervision": False,
                "output_frame": "body_imu_origin",
                "output_samples": 3,
                "td_s": -0.009109323,
                "policy_arguments": {
                    "max_correction_mm": None,
                    "correction_cap_mode": "global",
                    "eyes": "both",
                    "stereo_weight_policy": "observation",
                    "disable_learned_motion": False,
                    "learned_motion_consistency_limit_m": None,
                    "optional_stereo_policy": "reject_window",
                },
                "joint_position_solver": {"stereo_prior_weight_median": 1.0},
            },
        )
        write_json(artifact / "local_motion_factors.json", [{"first_index": 0, "second_index": 1, "eye": "left"}])
        write_json(
            artifact / "shared_stereo_observations.json",
            [{"accepted": True, "first_index": 0, "second_index": 1, "pnp_inlier_ratio": 0.7}],
        )
        (artifact / "body_trajectory_fused.csv").write_text("baseline body estimate\n")
    return manifest, baseline, artifact, record


def patch_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, gated, diagnostic):
    code_paths = []
    for index in range(3):
        path = tmp_path / "frozen_code" / f"code_{index}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture {index}\n")
        code_paths.append(path)
    monkeypatch.setattr(probe, "frozen_code_paths", lambda: code_paths)
    vins_config = tmp_path / "config" / "vins.yaml"
    imu_config = tmp_path / "config" / "imu.yaml"
    vins_config.parent.mkdir(parents=True, exist_ok=True)
    vins_config.write_text("vins config\n")
    imu_config.write_text("imu config\n")
    monkeypatch.setattr(probe.symmetric, "VINS_CONFIG", vins_config)
    monkeypatch.setattr(probe.symmetric, "IMU_CONFIG", imu_config)
    artifact = tmp_path / "baseline" / "case_01" / "both"
    candidate_path = artifact / "candidate_manifest.json"
    if candidate_path.exists():
        candidate = json.loads(candidate_path.read_text())
        record = json.loads((tmp_path / "manifest.json").read_text())["records"][0]
        required = [
            Path(record["vins_dir"]) / "vio_corrected_stream.csv",
            Path(record["vins_dir"]) / "run_acceptance.json",
            Path(record["session"]) / "d405_frames.csv",
            Path(record["session"]) / "external_imu/imu.bin",
            vins_config,
            imu_config,
        ]
        candidate["input_sha256"] = {
            str(path.resolve()): probe.file_hash(path) for path in required
        }
        write_json(candidate_path, candidate)
    times = np.asarray([10.0, 10.1, 10.2])
    positions = np.asarray([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.2, 0.0, 0.0]])
    rotations = Rotation.identity(3)
    rows = [{"t_sec": f"{t:.9f}", "x": "0", "y": "0", "z": "0", "qw": "1", "qx": "0", "qy": "0", "qz": "0"} for t in times]
    monkeypatch.setattr(probe.fusion, "load_vins_config", lambda *a, **k: {"td_s": -0.009109323})
    monkeypatch.setattr(probe.fusion, "load_calibrated_imu", lambda *a, **k: (times, np.zeros((3, 3)), np.zeros((3, 3)), {"imu": "ok"}))
    monkeypatch.setattr(probe.fusion, "load_trajectory", lambda *a, **k: (times, positions.copy(), rotations, list(rows)))
    monkeypatch.setattr(probe.fusion, "validate_relative_motion_report", lambda *a, **k: {"result": "PASS"})

    def bind(*_args):
        return times, positions.copy(), rotations, list(rows), times - 10.0, {"policy": "bound"}

    monkeypatch.setattr(probe.symmetric, "bind_body_reference", bind)

    def fake_gate(reference_times, reference_rotations, motion_factors, shared_stereo):
        assert len(reference_times) == 3
        assert np.asarray(reference_rotations).shape == (3, 3, 3)
        return gated, diagnostic

    monkeypatch.setattr(probe, "apply_learned_segment_reliability", fake_gate)
    monkeypatch.setattr(probe.corpus, "score_frozen", lambda record, estimate, output, work, stage: {"result": "PASS", "samples": 3, "ate_translation_max_m": 0.001})


def test_failed_baseline_record_is_retained_without_scoring(tmp_path, monkeypatch):
    manifest, baseline, _artifact, _record = make_fixture(tmp_path, status="PREPARATION_OR_INPUT_FAILED")
    called = []
    monkeypatch.setattr(probe.corpus, "score_frozen", lambda *a, **k: called.append(True))
    output = tmp_path / "out"

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(output)]) == 3

    summary = json.loads((output / "summary.json").read_text())
    assert summary["dataset_count"] == 1
    assert summary["results"][0]["status"] == "PREPARATION_OR_INPUT_FAILED"
    assert summary["results"][0]["variants"] == {}
    assert called == []


def test_zero_gate_copies_baseline_estimate_and_scores_selected_variant(tmp_path, monkeypatch):
    manifest, baseline, artifact, _record = make_fixture(tmp_path)
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[{"first_index": 0, "second_index": 1, "eye": "left", "confidence": 1.0}],
        diagnostic={
            "schema": "learned_segment_reliability_diagnostic_v1",
            "zeroed_factor_count": 0,
            "rule": {"duration_min_sec": 1.0},
        },
    )
    output = tmp_path / "out"

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(output)]) == 0

    result_dir = output / "case_01" / "selected"
    assert (result_dir / "body_trajectory_fused.csv").read_bytes() == (artifact / "body_trajectory_fused.csv").read_bytes()
    manifest_out = json.loads((result_dir / "candidate_manifest.json").read_text())
    assert manifest_out["external_ground_truth_used"] is False
    assert manifest_out["policy_arguments"]["learned_segment_gate"] == "duration_segment_reliability_v1"
    assert manifest_out["policy_arguments"]["optional_stereo_policy"] == "reject_window"
    assert manifest_out["gate_rule"] == {"duration_min_sec": 1.0}
    assert manifest_out["bound_samples"] == 3
    assert str((artifact / "candidate_manifest.json").resolve()) in manifest_out["input_sha256"]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "COMPLETED"
    assert "selected" in summary["results"][0]["variants"]


def test_nonzero_gate_refines_with_frozen_baseline_solver_arguments(tmp_path, monkeypatch):
    manifest, baseline, _artifact, _record = make_fixture(tmp_path)
    captured = {}
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[{"first_index": 0, "second_index": 1, "eye": "left", "confidence": 0.0}],
        diagnostic={"schema": "learned_segment_reliability_diagnostic_v1", "zeroed_factor_count": 1},
    )

    def fake_refine(positions, rotations, observations, mono, imu_times, gyro, accel, body_t_camera, td_s, **kwargs):
        captured.update(kwargs)
        return positions + np.asarray([0.01, 0.0, 0.0]), {"node_stride": kwargs["node_stride"], "secondary_visual_motion": {"edges": 1}}

    monkeypatch.setattr(probe.fusion, "refine_positions_visual_inertial", fake_refine)
    writes = []
    monkeypatch.setattr(probe.fusion, "write_trajectory", lambda path, rows, positions, rotations: (writes.append((path, positions.copy())), path.write_text("refined\n")))
    output = tmp_path / "out"

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(output)]) == 0

    assert captured["node_stride"] == 1
    assert captured["max_correction_m"] is None
    assert captured["use_visual_position_prior"] is False
    assert captured["solve_metric_scale"] is False
    assert captured["correction_cap_mode"] == "global"
    assert captured["secondary_visual_factors"][0]["confidence"] == 0.0
    np.testing.assert_allclose(captured["relative_motion_positions_body"], np.asarray([[0, 0, 0], [0.1, 0, 0], [0.2, 0, 0]]))
    assert writes
    graph = json.loads((output / "case_01" / "selected" / "graph_report.json").read_text())
    assert graph["output_frame"] == "body_imu_origin"


def test_baseline_candidate_with_gt_is_rejected_as_input_failure(tmp_path, monkeypatch):
    manifest, baseline, artifact, _record = make_fixture(tmp_path)
    data = json.loads((artifact / "candidate_manifest.json").read_text())
    data["external_ground_truth_used"] = True
    write_json(artifact / "candidate_manifest.json", data)
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[],
        diagnostic={"schema": "learned_segment_reliability_diagnostic_v1", "zeroed_factor_count": 0},
    )

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["selected"]
    assert variant["error_code"] == "VARIANT_FAILED"
    assert variant["stage"] == "run_record"
    assert "ground truth" in variant["error"]


def test_empty_baseline_input_hashes_are_rejected(tmp_path, monkeypatch):
    manifest, baseline, _artifact, _record = make_fixture(tmp_path)
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[],
        diagnostic={"schema": "learned_segment_reliability_diagnostic_v1", "zeroed_factor_count": 0},
    )
    candidate = tmp_path / "baseline" / "case_01" / "both" / "candidate_manifest.json"
    data = json.loads(candidate.read_text())
    data["input_sha256"] = {}
    write_json(candidate, data)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["selected"]
    assert "input_sha256 is missing" in variant["error"]


def test_code_hash_change_stops_before_mixing_results(tmp_path, monkeypatch):
    manifest, baseline, _artifact, _record = make_fixture(tmp_path)
    monkeypatch.setattr(probe, "snapshot_hashes", lambda paths: {"code.py": "old"})
    monkeypatch.setattr(probe, "code_changed", lambda frozen: True)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 2

    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["status"] == "STOP_CODE_CHANGED"
    assert summary["results"] == []


def test_scorer_cannot_mutate_frozen_estimate(tmp_path, monkeypatch):
    manifest, baseline, _artifact, _record = make_fixture(tmp_path)
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[{"first_index": 0, "second_index": 1, "eye": "left", "confidence": 1.0}],
        diagnostic={"schema": "learned_segment_reliability_diagnostic_v1", "zeroed_factor_count": 0, "rule": {"duration_min_sec": 1.0}},
    )

    def mutate_estimate(_record, estimate, _output, _work, _stage):
        Path(estimate).write_text("mutated by scorer\n")
        return {"result": "PASS", "samples": 3, "ate_translation_max_m": 0.001}

    monkeypatch.setattr(probe.corpus, "score_frozen", mutate_estimate)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["selected"]
    assert "estimate changed during scoring" in variant["error"]


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda candidate, graph: candidate.__setitem__("schema", "wrong"), "candidate schema"),
        (lambda candidate, graph: graph.__setitem__("schema", "wrong"), "graph schema"),
        (lambda candidate, graph: candidate.__setitem__("shared_gauge", "camera"), "shared gauge"),
        (lambda candidate, graph: candidate.__setitem__("primary_eye", "left"), "primary eye"),
        (lambda candidate, graph: candidate.__setitem__("shared_scale_state", True), "shared scale"),
        (lambda candidate, graph: candidate.__setitem__("body_t_camera_in_solver", np.diag([1, 1, 1, 2]).tolist()), "body_t_camera"),
    ],
)
def test_baseline_manifest_identity_guards(tmp_path, monkeypatch, mutation, message):
    manifest, baseline, artifact, _record = make_fixture(tmp_path)
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[],
        diagnostic={"schema": "learned_segment_reliability_diagnostic_v1", "zeroed_factor_count": 0},
    )
    candidate_path = artifact / "candidate_manifest.json"
    graph_path = artifact / "graph_report.json"
    candidate = json.loads(candidate_path.read_text())
    graph = json.loads(graph_path.read_text())
    mutation(candidate, graph)
    write_json(candidate_path, candidate)
    write_json(graph_path, graph)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["selected"]
    assert variant["error_code"] == "VARIANT_FAILED"
    assert message in variant["error"]


def test_baseline_first_position_must_match_bound_vins_anchor(tmp_path, monkeypatch):
    manifest, baseline, artifact, _record = make_fixture(tmp_path)
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[],
        diagnostic={"schema": "learned_segment_reliability_diagnostic_v1", "zeroed_factor_count": 0},
    )

    def shifted_load_trajectory(path):
        times = np.asarray([10.0, 10.1, 10.2])
        rotations = Rotation.identity(3)
        rows = [{"t_sec": f"{t:.9f}"} for t in times]
        positions = np.asarray([[0.01, 0.0, 0.0], [0.1, 0.0, 0.0], [0.2, 0.0, 0.0]])
        if Path(path) == artifact / "body_trajectory_fused.csv":
            return times, positions, rotations, rows
        return times, np.asarray([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.2, 0.0, 0.0]]), rotations, rows

    monkeypatch.setattr(probe.fusion, "load_trajectory", shifted_load_trajectory)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]) == 3
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    variant = summary["results"][0]["variants"]["selected"]
    assert "first position" in variant["error"]


def test_rotation_csv_rounding_tolerance_keeps_real_baseline_identity(tmp_path, monkeypatch):
    manifest, baseline, artifact, _record = make_fixture(tmp_path)
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[],
        diagnostic={"schema": "learned_segment_reliability_diagnostic_v1", "zeroed_factor_count": 0},
    )
    times = np.asarray([10.0, 10.1, 10.2])
    positions = np.asarray([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.2, 0.0, 0.0]])
    rows = [{"t_sec": f"{t:.9f}"} for t in times]
    state = probe.SimpleNamespace(
        times=times,
        positions=positions,
        rotations=Rotation.identity(3),
        rows=rows,
    )

    def rounded_load_trajectory(path):
        tiny = Rotation.from_rotvec([[1.9e-9, 0.0, 0.0], [0.0, 1.0e-9, 0.0], [0.0, 0.0, 1.0e-9]])
        return times, positions, tiny, rows

    monkeypatch.setattr(probe.fusion, "load_trajectory", rounded_load_trajectory)
    probe.validate_baseline_trajectory_identity(
        artifact,
        state,
        {"output_samples": 3},
    )


def test_large_rotation_identity_mismatch_is_still_rejected(tmp_path, monkeypatch):
    manifest, baseline, artifact, _record = make_fixture(tmp_path)
    patch_runtime(
        monkeypatch,
        tmp_path,
        gated=[],
        diagnostic={"schema": "learned_segment_reliability_diagnostic_v1", "zeroed_factor_count": 0},
    )
    times = np.asarray([10.0, 10.1, 10.2])
    positions = np.asarray([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.2, 0.0, 0.0]])
    rows = [{"t_sec": f"{t:.9f}"} for t in times]
    state = probe.SimpleNamespace(
        times=times,
        positions=positions,
        rotations=Rotation.identity(3),
        rows=rows,
    )

    def mismatched_load_trajectory(path):
        bad = Rotation.from_rotvec([[1.0e-5, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
        return times, positions, bad, rows

    monkeypatch.setattr(probe.fusion, "load_trajectory", mismatched_load_trajectory)
    with pytest.raises(ValueError, match="max_so3_error_rad"):
        probe.validate_baseline_trajectory_identity(
            artifact,
            state,
            {"output_samples": 3},
        )
