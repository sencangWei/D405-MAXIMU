import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / ".planning/metric_window_bundle_20260928/score_full_shape_geometry.py"
SPEC = importlib.util.spec_from_file_location("score_full_shape_geometry", PATH)
score = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = score
SPEC.loader.exec_module(score)


CASES = ["dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh1", "fresh2", "fresh3", "fresh4"]


def write_pose_csv(path: Path, count: int, *, offset=0.0, rotation_step_deg=0.0):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t_sec", "x", "y", "z", "qx", "qy", "qz", "qw"])
        writer.writeheader()
        for index in range(count):
            quat = Rotation.from_euler("z", rotation_step_deg * index, degrees=True).as_quat()
            writer.writerow(
                {
                    "t_sec": f"{index:.9f}",
                    "x": f"{0.01 * index + offset:.9f}",
                    "y": "0",
                    "z": "0",
                    "qx": f"{quat[0]:.12f}",
                    "qy": f"{quat[1]:.12f}",
                    "qz": f"{quat[2]:.12f}",
                    "qw": f"{quat[3]:.12f}",
                }
            )


def sha(path: Path) -> str:
    return score.geom.digest(path)


def factor(*, centers=None, rotvecs=None):
    if centers is None:
        centers = np.column_stack((np.linspace(0, 0.4, 9), np.zeros((9, 2))))
    if rotvecs is None:
        rotvecs = np.zeros((9, 3))
    return {
        "diagnostic_only": True,
        "available": True,
        "not_admissible_for_graph": True,
        "solver_accepted": True,
        "optimized_centers_m": np.asarray(centers).tolist(),
        "optimized_rotvecs_camera_to_window": np.asarray(rotvecs).tolist(),
        "metadata": {
            "available_for_graph": False,
            "calibrated_covariance": False,
            "statistical_independence_claimed": False,
        },
    }


def case_rows(name: str, root: Path, *, accepted_first=True, bad_factor=False, factor_override=None):
    count = score.full_shape.EXPECTED_RAW_COUNTS[name]
    source = root / f"{name}_bound_input.txt"
    source.write_text(name)
    pairs, windows = [], []
    shape = factor_override if factor_override is not None else factor()
    if bad_factor:
        shape["metadata"]["calibrated_covariance"] = True
    for pair, indices in enumerate(score.full_shape.expected_pair_schedule(), 1):
        accepted = accepted_first and pair == 1
        pairs.append({"pair": pair, "indices": indices, "raw_frame_indices": list(range(indices[0], indices[-1] + 1)), "accepted": accepted, "reason": "ok" if accepted else "refused"})
        for part, half in enumerate((indices[:5], indices[4:])):
            row = {"window": 2 * (pair - 1) + part + 1, "joint_pair": pair, "indices": half, "elapsed_s": [float(v) for v in half], "accepted": accepted, "reason": pairs[-1]["reason"], "diagnostics": {}}
            if accepted:
                row["diagnostics"]["stereo_window_shape_factor"] = shape
            windows.append(row)
    independent = [dict(row) for row in windows]
    for row in independent:
        row.pop("joint_pair", None)
    return {
        "case": name,
        "windows": windows,
        "independent_windows": independent,
        "pairs": pairs,
        "input_sha256": {str(source): sha(source)},
        "decoded_grayscale_frame_sha256": {f"left:{name}:0": "d" * 64},
    }


def write_controls(tmp_path: Path, monkeypatch, *, mutate=None, source_conflict=False, flag_overrides=None, bad_factor=False, factor_override=None, reorder_independent=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sources = [tmp_path / "full_shape.py", tmp_path / "score_shape.py"]
    for path in sources:
        path.write_text(path.name)
    monkeypatch.setattr(score.full_shape, "source_paths", lambda: sources)
    controls = tmp_path / "controls"
    controls.mkdir()
    cases = [case_rows(name, controls, accepted_first=(name == "dev1"), bad_factor=bad_factor, factor_override=factor_override) for name in CASES]
    if mutate:
        mutate(cases)
    source_hashes = {str(path): sha(path) for path in sources}
    if source_conflict:
        source_hashes[str(sources[0])] = "0" * 64
    case_counts = {}
    for name in CASES:
        expected = score.full_shape.EXPECTED_RAW_COUNTS[name]
        case_counts[name] = {
            "recording_raw_frame_count": expected,
            "pair_count": 29,
            "joint_window_count": 58,
            "independent_window_count": 58,
            "last_endpoint_index": 1160,
            "uncovered_tail_frames_after_last_endpoint": expected - 1 - 1160,
        }
    adapter = {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "used_for_graph_or_selection": False,
        "available_for_graph": False,
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "joint_pairs_per_case": 29,
        "endpoint_rows_per_case": 58,
        "independent_summary": False,
        "case_counts": case_counts,
    }
    if flag_overrides:
        adapter.update(flag_overrides)
    summary = {"cases": cases, "source_sha256": source_hashes, "external_reference_used": False, "production_modified": False, "full_shape_window_adapter": adapter}
    independent = [{**case, "windows": case["independent_windows"]} for case in cases]
    if reorder_independent:
        independent = list(reversed(independent))
    (controls / "summary.json").write_text(json.dumps(summary) + "\n")
    (controls / "independent_summary.json").write_text(json.dumps({**summary, "cases": independent, "full_shape_window_adapter": {**adapter, "independent_summary": True}}) + "\n")
    return controls


def write_graph_root(root: Path, *, extrinsic=None, rotation_step_deg=0.0):
    graph_root = root / "graphs"
    for name in CASES:
        folder = graph_root / name
        folder.mkdir(parents=True)
        count = score.full_shape.EXPECTED_RAW_COUNTS[name]
        fused = folder / "trajectory_fused.csv"
        gt = folder / "gt.csv"
        write_pose_csv(fused, count, rotation_step_deg=rotation_step_deg)
        write_pose_csv(folder / "trajectory_graph.csv", count, offset=99.0)
        write_pose_csv(gt, count, rotation_step_deg=rotation_step_deg)
        matrix = np.eye(4) if extrinsic is None else np.asarray(extrinsic, dtype=float)
        extrinsic_payload = matrix.tolist()
        original_dir = folder / "original"
        original_dir.mkdir()
        original_graph = original_dir / "graph_fusion_report.json"
        stereo_report = folder / "stereo_report.json"
        raw_traj = folder / "raw_trajectory.csv"
        write_pose_csv(raw_traj, count, rotation_step_deg=rotation_step_deg)
        stereo_report.write_text(json.dumps({"trajectory": str(raw_traj)}) + "\n")
        original_graph.write_text(json.dumps({"inputs": {"session": f"session:{name}", "stereo_report": str(stereo_report)}, "camera_extrinsics": {"effective_body_T_trajectory_camera": extrinsic_payload}, "time_alignment": {"estimate_td": 0, "td_s": -0.009109323}}) + "\n")
        (folder / "graph_fusion_report.json").write_text(json.dumps({"inputs": {"session": f"session:{name}"}, "camera_extrinsics": {"effective_body_T_trajectory_camera": extrinsic_payload, "trajectory_observation_frame": "infrared_left_camera_i"}, "time_alignment": {"estimate_td": 0, "td_s": -0.009109323}}) + "\n")
        precision = {"alignment": "SE3_estimate_to_external_ground_truth_no_scale", "estimate": str(fused), "ground_truth": str(gt), "max_interpolation_gap_s": 0.05, "estimate_frame": "as_recorded", "ground_truth_frame": "as_recorded"}
        (folder / "official_score").mkdir()
        (folder / "official_score" / "precision.json").write_text(json.dumps(precision) + "\n")
    return graph_root


def bind_original_graphs(controls: Path, graph_root: Path):
    for name in ["summary.json", "independent_summary.json"]:
        data = json.loads((controls / name).read_text())
        for case in data["cases"]:
            original = graph_root / case["case"] / "original" / "graph_fusion_report.json"
            case["input_sha256"][str(original)] = sha(original)
            stereo = graph_root / case["case"] / "stereo_report.json"
            raw = graph_root / case["case"] / "raw_trajectory.csv"
            case["input_sha256"][str(stereo)] = sha(stereo)
            case["input_sha256"][str(raw)] = sha(raw)
        (controls / name).write_text(json.dumps(data) + "\n")


def test_validate_full_controls_fails_before_any_gt_for_incomplete_freeze(tmp_path, monkeypatch):
    controls = tmp_path / "controls"
    controls.mkdir()
    empty = {"cases": [], "external_reference_used": False, "production_modified": False}
    (controls / "summary.json").write_text(json.dumps(empty) + "\n")
    (controls / "independent_summary.json").write_text(json.dumps(empty) + "\n")
    monkeypatch.setattr(score.geom, "pose_snapshot", lambda *args: pytest.fail("GT/pose must not open before freeze validation"))
    with pytest.raises(ValueError, match="exact ten"):
        score.score(controls, tmp_path)


def test_full_schema_flags_sources_and_factor_contracts_fail_closed(tmp_path, monkeypatch):
    controls = write_controls(tmp_path, monkeypatch, flag_overrides={"available_for_graph": True})
    with pytest.raises(ValueError, match="diagnostic-only"):
        score.validate_full_controls(controls)

    controls = write_controls(tmp_path / "conflict", monkeypatch, source_conflict=True)
    with pytest.raises(ValueError, match="source"):
        score.validate_full_controls(controls)

    controls = write_controls(tmp_path / "bad_factor", monkeypatch, bad_factor=True)
    with pytest.raises(ValueError, match="diagnostic-only factor"):
        score.validate_full_controls(controls)

    controls = write_controls(tmp_path / "case_counts", monkeypatch)
    data = json.loads((controls / "summary.json").read_text())
    data["full_shape_window_adapter"]["case_counts"]["dev1"]["recording_raw_frame_count"] = 1185
    (controls / "summary.json").write_text(json.dumps(data) + "\n")
    with pytest.raises(ValueError, match="case_counts"):
        score.validate_full_controls(controls)

    controls = write_controls(tmp_path / "reordered", monkeypatch, reorder_independent=True)
    score.validate_full_controls(controls)


def test_score_uses_official_fused_body_estimate_and_scores_eight_nonanchors(tmp_path, monkeypatch):
    controls = write_controls(tmp_path, monkeypatch)
    graphs = write_graph_root(tmp_path)
    bind_original_graphs(controls, graphs)
    report = score.score(controls, graphs)
    dev1 = next(case for case in report["cases"] if case["case"] == "dev1")
    first = dev1["pairs"][0]
    assert first["scored"] is True
    assert len(first["ba_local_node_errors_mm"]) == 8
    assert len(first["current_official_estimate_local_node_errors_mm"]) == 8
    assert report["ba_local_geometry"]["count"] == 8
    assert report["current_official_estimate_local_geometry"]["count"] == 8
    assert report["external_reference_used_in_estimation"] is False
    assert report["external_reference_used_in_evaluation"] is True
    refused = dev1["pairs"][1]
    assert refused["scored"] is False
    assert refused["score_reason"] == "pair_refused"

    precision = graphs / "dev1" / "official_score" / "precision.json"
    data = json.loads(precision.read_text())
    data["estimate"] = str(graphs / "dev1" / "trajectory_graph.csv")
    precision.write_text(json.dumps(data) + "\n")
    with pytest.raises(ValueError, match="official contract"):
        score.score(controls, graphs)


def test_score_converts_official_body_estimate_to_camera_before_relative_errors(tmp_path, monkeypatch):
    extrinsic = np.eye(4)
    extrinsic[:3, :3] = Rotation.from_euler("x", 25, degrees=True).as_matrix()
    extrinsic[:3, 3] = [0.1, 0.0, 0.0]
    body_positions = np.column_stack((np.arange(1199) * 0.01, np.zeros((1199, 2))))
    body_rotations = Rotation.from_euler("z", np.arange(1199), degrees=True)
    camera_positions, camera_rotations = score.geom.camera_reference(body_positions[:41:5], body_rotations[:41:5], extrinsic)
    local_camera = camera_rotations[0].inv().apply(camera_positions - camera_positions[0])
    controls = write_controls(
        tmp_path,
        monkeypatch,
        factor_override=factor(centers=local_camera, rotvecs=np.zeros((9, 3))),
    )
    graphs = write_graph_root(tmp_path, extrinsic=extrinsic, rotation_step_deg=1.0)
    bind_original_graphs(controls, graphs)

    report = score.score(controls, graphs)
    first = next(case for case in report["cases"] if case["case"] == "dev1")["pairs"][0]
    assert max(first["current_official_estimate_local_node_errors_mm"]) < 1e-6


def test_time_alignment_and_precision_contracts_fail_closed(tmp_path, monkeypatch):
    controls = write_controls(tmp_path, monkeypatch)
    graphs = write_graph_root(tmp_path)
    bind_original_graphs(controls, graphs)
    graph_path = graphs / "dev1" / "graph_fusion_report.json"
    graph = json.loads(graph_path.read_text())
    graph["time_alignment"]["td_s"] = -0.01
    graph_path.write_text(json.dumps(graph) + "\n")
    with pytest.raises(ValueError, match="time alignment"):
        score.score(controls, graphs)

    controls = write_controls(tmp_path / "precision_controls", monkeypatch)
    graphs = write_graph_root(tmp_path / "precision")
    bind_original_graphs(controls, graphs)
    precision = graphs / "dev1" / "official_score" / "precision.json"
    data = json.loads(precision.read_text())
    data["max_interpolation_gap_s"] = 1.0
    precision.write_text(json.dumps(data) + "\n")
    with pytest.raises(ValueError, match="precision"):
        score.score(controls, graphs)


def test_output_refuses_overwrite(tmp_path, monkeypatch):
    controls = write_controls(tmp_path, monkeypatch)
    output = tmp_path / "out.json"
    output.write_text("exists")
    with pytest.raises(ValueError, match="overwrite"):
        score.main(["--controls", str(controls), "--graphs", str(tmp_path), "--output", str(output)])
