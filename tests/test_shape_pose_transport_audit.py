import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".planning/metric_window_bundle_20260928/audit_shape_pose_transport.py"
SPEC = importlib.util.spec_from_file_location("audit_shape_pose_transport", MODULE_PATH)
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def _factor(offset=0.0):
    sensitivity = np.eye(24)
    return {
        "diagnostic_only": True,
        "available": True,
        "frames": 9,
        "point_count": 4,
        "gauge": "all_relative_centers_mapped_through_first_camera_rotation",
        "reference_center_vector_m": (np.arange(24, dtype=float) * 0.01 + offset).tolist(),
        "optimized_centers_m": np.vstack(([0.0, 0.0, 0.0], np.arange(24, dtype=float).reshape(8, 3) * 0.01 + offset)).tolist(),
        "optimized_rotvecs_camera_to_window": np.zeros((9, 3)).tolist(),
        "compact_sqrt_sensitivity": sensitivity.tolist(),
        "affine_offset": np.linspace(0.01, 0.02, 24).tolist(),
        "metadata": {
            "calibrated_covariance": False,
            "statistical_independence_claimed": False,
            "available_for_graph": False,
        },
        "conditional_rank": 24,
        "solver_optimality": 0.5,
        "solver_accepted": True,
        "not_admissible_for_graph": True,
    }


def _endpoint(pair, part, accepted=True, factor=None):
    indices = list(range((pair - 1) * 40 + part * 20, (pair - 1) * 40 + part * 20 + 21, 5))
    row = {
        "window": 2 * (pair - 1) + part + 1,
        "joint_pair": pair,
        "indices": indices,
        "elapsed_s": [i / 30.0 for i in indices],
        "accepted": accepted,
        "reason": "ok" if accepted else "solve_failed",
        "diagnostics": {},
    }
    if factor is not None:
        row["diagnostics"]["stereo_window_shape_factor"] = factor
    return row


def _summary(cases, source_dir=None):
    if source_dir is None:
        sources = {f"source_{i}.py": f"{i:064x}" for i in range(audit.EXPECTED_SOURCE_HASHES)}
    else:
        source_dir.mkdir()
        sources = {}
        for i in range(audit.EXPECTED_SOURCE_HASHES):
            path = source_dir / f"source_{i}.py"
            payload = f"# source {i}\n"
            path.write_text(payload)
            sources[str(path)] = hashlib.sha256(payload.encode()).hexdigest()
    return {
        "cases": cases,
        "source_sha256": sources,
        "external_reference_used": False,
        "production_modified": False,
    }


def _case(name, accepted_pairs=1, input_dir=None):
    windows = []
    pairs = []
    for i in range(audit.EXPECTED_PAIRS_PER_CASE):
        accepted = i < accepted_pairs
        factor = _factor(i * 0.001) if accepted else None
        pair_id = i + 1
        pair_indices = list(range(i * 40, i * 40 + 41, 5))
        pairs.append({"indices": pair_indices, "accepted": accepted, "reason": "ok" if accepted else "solve_failed"})
        windows.append(_endpoint(pair_id, 0, accepted, factor))
        windows.append(_endpoint(pair_id, 1, accepted, factor))
    if input_dir is None:
        input_map = {"input": "0" * 64}
    else:
        input_dir.mkdir(exist_ok=True)
        path = input_dir / f"{name}_input.txt"
        payload = f"{name} input\n"
        path.write_text(payload)
        input_map = {str(path): hashlib.sha256(payload.encode()).hexdigest()}
    return {
        "case": name,
        "pairs": pairs,
        "windows": windows,
        "independent_windows": [dict(row) for row in windows],
        "input_sha256": input_map,
        "decoded_grayscale_frame_sha256": {"left:0": "1" * 64},
    }


def _write_csv(path, rows=1200, yaw_step=0.001):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        writer.writeheader()
        for i in range(rows):
            rot = Rotation.from_rotvec([0.0, 0.0, yaw_step * i])
            x, y, z, w = rot.as_quat()
            writer.writerow(
                {
                    "t_sec": f"{i / 30.0:.12f}",
                    "x": f"{0.01 * i:.12f}",
                    "y": f"{0.002 * i:.12f}",
                    "z": f"{0.001 * i:.12f}",
                    "qw": f"{w:.12f}",
                    "qx": f"{x:.12f}",
                    "qy": f"{y:.12f}",
                    "qz": f"{z:.12f}",
                }
            )


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_report(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _graph_fixture(tmp_path, case="dev1", rows=1199):
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "graphs" / case
    source = tmp_path / "source.csv"
    code_source = tmp_path / "source.py"
    trajectory = target / "trajectory_graph.csv"
    code_source.write_text("# frozen graph source\n")
    _write_csv(source, rows=rows)
    _write_csv(trajectory, rows=rows)
    graph_report = target / "graph_fusion_report.json"
    fusion_report = target / "fusion_report.json"
    graph_command = [
        "python",
        "wrapper.py",
        "--session",
        str(tmp_path / "session"),
        "--trajectory",
        str(source),
        "--stream",
        "infrared_left",
        "--vins-config",
        str(tmp_path / "formal.yaml"),
        "--expected-td-s",
        str(audit.EXPECTED_TD_S),
        "--output",
        str(trajectory),
        "--report",
        str(graph_report),
    ]
    complementary_command = [
        "python",
        "complementary.py",
        "--output",
        str(target / "trajectory_fused_unsmoothed.csv"),
        "--report",
        str(fusion_report),
    ]
    _write_report(
        graph_report,
        {
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "inputs": {
                "external_ground_truth_used": False,
                "session": str(tmp_path / "session"),
                "vins_spatiotemporal_calibration": str(tmp_path / "formal.yaml"),
                "trajectory": str(source),
            },
            "time_alignment": {"estimate_td": 0, "td_s": audit.EXPECTED_TD_S},
            "camera_extrinsics": {"trajectory_observation_frame": audit.EXPECTED_TRAJECTORY_FRAME},
            "output": str(trajectory),
        },
    )
    _write_report(
        fusion_report,
        {
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "output_frame": "body_imu_origin",
            "inputs": {
                "mast3r_camera_trajectory": str(trajectory),
                "body_camera_calibration": str(tmp_path / "formal.yaml"),
            },
        },
    )
    record = {
        "commands": [["graph", graph_command], ["complementary", complementary_command]],
        "input_sha256": {str(source): _hash(source)},
        "source_sha256": {str(code_source): _hash(code_source)},
    }
    (target / "manifest.json").write_text(json.dumps(record))
    return target, trajectory, record


def test_profile_residual_world_rigid_invariance():
    factor = _factor()
    positions = np.arange(27, dtype=float).reshape(9, 3) * 0.01
    rotations = Rotation.from_rotvec(np.column_stack((np.zeros(9), np.zeros(9), np.arange(9) * 0.01)))
    transform = audit.deterministic_world_transform()
    base = audit.profile_residual(factor, positions, rotations)
    moved_positions, moved_rotations = audit.transform_world(positions, rotations, *transform)
    moved = audit.profile_residual(factor, moved_positions, moved_rotations)
    np.testing.assert_allclose(moved, base, atol=1e-12)


def test_write_json_refuses_overwrite(tmp_path):
    out = tmp_path / "out.json"
    out.write_text("{}\n")
    with pytest.raises(FileExistsError):
        audit.write_json_no_overwrite(out, {"x": 1})


def test_audit_pair_rejects_timestamp_mismatch(tmp_path):
    case = _case("dev1")
    case["windows"][0]["elapsed_s"][1] += 0.01
    traj_path = tmp_path / "dev1/trajectory_graph.csv"
    _write_csv(traj_path)
    trajectory = audit.load_trajectory(traj_path)
    pair, first, second = audit.validate_pair_binding(case, 0)
    with pytest.raises(ValueError, match="timestamp binding"):
        audit.audit_pair("dev1", 0, pair, first, second, trajectory, audit.deterministic_world_transform())


def test_audit_counts_and_metrics_with_synthetic_contract(tmp_path, monkeypatch):
    names = sorted(audit.EXPECTED_CASES)
    cases = []
    remaining = audit.EXPECTED_ACCEPTED_GROUPS
    for name in names:
        count = min(audit.EXPECTED_PAIRS_PER_CASE, remaining)
        remaining -= count
        cases.append(_case(name, accepted_pairs=count, input_dir=tmp_path / "inputs"))
    assert remaining == 0
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(_summary(cases, tmp_path / "sources")))
    graph_root = tmp_path / "graphs"
    for name in names:
        _write_csv(graph_root / name / "trajectory_graph.csv", rows=1200 if name == "dev2" else 1199)
    result = audit.audit(summary_path, graph_root)
    assert result["counts"]["pairs_total"] == 290
    assert result["counts"]["accepted_groups"] == 253
    assert result["counts"]["refused_groups"] == 37
    assert result["counts"]["endpoint_rows"] == 580
    assert result["summary"]["rigid_invariance_passed_1e_9"] is True
    assert result["summary"]["native_vs_ba_relative_rotation_deg_max"]["count"] == 253
    assert result["counts"]["actual_case_input_files_verified"] == 10


def test_audit_rejects_wrong_source_hash_count(tmp_path):
    names = sorted(audit.EXPECTED_CASES)
    cases = [_case(name, accepted_pairs=0) for name in names]
    payload = _summary(cases)
    payload["source_sha256"].pop("source_0.py")
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="14"):
        audit.audit(summary_path, tmp_path / "graphs")


def test_verify_hash_map_rejects_mutated_raw_file(tmp_path):
    raw = tmp_path / "raw.txt"
    raw.write_text("before")
    hashes = {str(raw): _hash(raw)}
    raw.write_text("after")
    with pytest.raises(ValueError, match="hash mismatch"):
        audit.verify_hash_map(hashes, label="raw")


def test_pair_binding_rejects_nonbool_and_shifted_schedule():
    case = _case("dev1")
    case["pairs"][0]["accepted"] = 1
    with pytest.raises(ValueError, match="literal bool"):
        audit.validate_pair_binding(case, 0)
    case = _case("dev1")
    case["pairs"][0]["indices"][0] = 1
    with pytest.raises(ValueError, match="literal 0..1120"):
        audit.validate_pair_binding(case, 0)


def test_frozen_graph_binding_rejects_wrong_body_label_and_td(tmp_path):
    target, trajectory_path, record = _graph_fixture(tmp_path)
    trajectory = audit.load_trajectory(trajectory_path)
    report_path = target / "graph_fusion_report.json"
    report = json.loads(report_path.read_text())
    report["camera_extrinsics"]["trajectory_observation_frame"] = "body"
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="trajectory frame"):
        audit.validate_frozen_case_graph("dev1", trajectory_path, trajectory, tmp_path / "graphs", record)
    report["camera_extrinsics"]["trajectory_observation_frame"] = audit.EXPECTED_TRAJECTORY_FRAME
    report["time_alignment"]["td_s"] = 0.0
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="td mismatch"):
        audit.validate_frozen_case_graph("dev1", trajectory_path, trajectory, tmp_path / "graphs", record)


def test_frozen_graph_binding_rejects_wrong_fusion_output_frame(tmp_path):
    target, trajectory_path, record = _graph_fixture(tmp_path)
    trajectory = audit.load_trajectory(trajectory_path)
    report_path = target / "fusion_report.json"
    report = json.loads(report_path.read_text())
    report["output_frame"] = "camera"
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="output frame"):
        audit.validate_frozen_case_graph("dev1", trajectory_path, trajectory, tmp_path / "graphs", record)


def test_frozen_graph_binding_rejects_raw_count_and_timestamp_mismatch(tmp_path):
    target, trajectory_path, record = _graph_fixture(tmp_path, rows=1198)
    trajectory = audit.load_trajectory(trajectory_path)
    with pytest.raises(ValueError, match="raw count"):
        audit.validate_frozen_case_graph("dev1", trajectory_path, trajectory, tmp_path / "graphs", record)
    target, trajectory_path, record = _graph_fixture(tmp_path / "time")
    trajectory = audit.load_trajectory(trajectory_path)
    source = audit.command_value(record["commands"][0][1], "--trajectory")
    rows = list(csv.DictReader(source.open()))
    rows[10]["t_sec"] = f"{float(rows[10]['t_sec']) + 0.25:.12f}"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        writer.writeheader()
        writer.writerows(rows)
    record["input_sha256"][str(source)] = _hash(source)
    with pytest.raises(ValueError, match="timestamps"):
        audit.validate_frozen_case_graph("dev1", trajectory_path, trajectory, tmp_path / "time/graphs", record)


def test_graph_report_change_is_visible_in_binding_hash(tmp_path):
    target, trajectory_path, record = _graph_fixture(tmp_path)
    trajectory = audit.load_trajectory(trajectory_path)
    before = audit.validate_frozen_case_graph("dev1", trajectory_path, trajectory, tmp_path / "graphs", record)
    report_path = target / "graph_fusion_report.json"
    report = json.loads(report_path.read_text())
    report["result"] = "MUTATED"
    report_path.write_text(json.dumps(report))
    after = audit.validate_frozen_case_graph("dev1", trajectory_path, trajectory, tmp_path / "graphs", record)
    assert before["graph_fusion_report_sha256"] != after["graph_fusion_report_sha256"]
