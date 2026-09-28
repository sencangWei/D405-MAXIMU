import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from test_fuse_mast3r_metric_windows import (
    accepted_window,
    sha256,
    write_graph,
    write_report,
    write_trajectory,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fuse_mast3r_seam_pair_windows.py"
spec = importlib.util.spec_from_file_location("fuse_mast3r_seam_pair_windows", SCRIPT)
wrapper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = wrapper
spec.loader.exec_module(wrapper)

CASE_NAMES = ["fresh1", "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh2", "fresh3", "fresh4"]


def make_case(case: str, graph_report: Path, source_report: Path, trajectory: Path, *, partial=False):
    rows = []
    for pair in range(29):
        for part in range(2):
            row = accepted_window(window=2 * pair + part + 1)
            start = 40 * pair + 20 * part
            row.update(
                joint_pair=pair + 1,
                indices=[start, start + 5, start + 10, start + 15, start + 20],
                elapsed_s=[0.1 * (start + offset) for offset in (0, 5, 10, 15, 20)],
                endpoint_m=[0.01 + pair, 0.02 + part, 0.03],
            )
            rows.append(row)
    if partial:
        rows[1]["accepted"] = False
        rows[1]["reason"] = "model_consistency_failed"
        rows[1].pop("endpoint_m", None)
    return {
        "case": case,
        "input_sha256": {
            str(graph_report.resolve()): sha256(graph_report),
            str(source_report.resolve()): sha256(source_report),
            str(trajectory.resolve()): sha256(trajectory),
        },
        "windows": rows,
    }


def write_controls(path: Path, case: str, graph_report: Path, source_report: Path, trajectory: Path, *, independent=False, partial=False):
    cases = []
    for name in CASE_NAMES:
        cases.append(make_case(name, graph_report, source_report, trajectory, partial=partial and name == case))
    data = {
        "external_reference_used": False,
        "source_sha256": {str(SCRIPT.resolve()): sha256(SCRIPT)},
        "full_seam_adapter": {
            "raw_interval_frames": 40,
            "independent_summary": independent,
            "correlated_paired_endpoints": True,
            "calibrated_covariance": False,
            "case_counts": [
                {
                    "case": name,
                    "pair_count": 29,
                    "window_count": 58,
                    "recording_raw_frame_count": 1200 if name == "dev2" else 1199,
                    "last_endpoint_index": 1160,
                    "uncovered_tail_frames_after_last_endpoint": 39 if name == "dev2" else 38,
                }
                for name in CASE_NAMES
            ],
        },
        "cases": cases,
    }
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_build_pair_factors_adds_group_metadata_and_rejects_partial_joint(tmp_path, monkeypatch):
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.1 * i for i in range(1200)])
    source = tmp_path / "source.json"
    session = tmp_path / "session"
    session.mkdir()
    write_report(source, trajectory, session)
    graph = tmp_path / "graph.json"
    write_graph(graph, source)
    case = make_case("fresh2", graph, source, trajectory)
    times = wrapper.metric.trajectory_times(trajectory)

    factors, stats = wrapper.build_pair_factors(case, times, "joint")

    assert len(factors) == 58
    assert stats["accepted_pair_groups"] == 29
    assert stats["pair_level_group_count"] == 29
    assert factors[0]["metric_source"] == wrapper.METRIC_SOURCE
    assert factors[0]["correlated_factor_group_id"] == "fresh2:pair:1"
    assert factors[0]["correlated_factor_group_size"] == 2
    assert factors[0]["correlated_factor_group_part"] == 0
    assert factors[0]["fixed_penalty_m"] == 0.004
    assert factors[0]["fixed_penalty_role"] == "regularization_not_stochastic_sigma"
    assert factors[0]["calibrated_covariance"] is False
    assert factors[0]["statistical_independence_claimed"] is False

    partial = make_case("fresh2", graph, source, trajectory, partial=True)
    with pytest.raises(ValueError, match="partial joint"):
        wrapper.build_pair_factors(partial, times, "joint")
    _, independent_stats = wrapper.build_pair_factors(partial, times, "independent")
    assert independent_stats["independent_control_partial_pairs"] == 1
    independent_factors, _ = wrapper.build_pair_factors(partial, times, "independent")
    assert independent_factors[0]["pair_schedule_group_id"] == "fresh2:pair:1"
    assert independent_factors[0]["separate_independent_solves"] is True
    assert independent_factors[0]["independent_solve_group_size"] == 1
    assert "correlated_factor_group_size" not in independent_factors[0]


def test_strict_summary_and_refused_schedule_guards(tmp_path):
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.1 * i for i in range(1200)])
    session = tmp_path / "session"
    session.mkdir()
    source = tmp_path / "source.json"
    write_report(source, trajectory, session)
    graph = tmp_path / "graph.json"
    write_graph(graph, source)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph, source, trajectory)
    data = json.loads(controls.read_text())
    data["source_sha256"] = {}
    with pytest.raises(ValueError, match="hash map must be nonempty"):
        wrapper.case_by_name(data, "fresh2", independent=False)
    data = json.loads(controls.read_text())
    data["cases"][0]["case"] = "unknown"
    with pytest.raises(ValueError, match="known cases"):
        wrapper.case_by_name(data, "fresh2", independent=False)
    data = json.loads(controls.read_text())
    data["full_seam_adapter"].pop("case_counts")
    with pytest.raises(ValueError, match="case_counts exact ten required"):
        wrapper.case_by_name(data, "fresh2", independent=False)
    data = json.loads(controls.read_text())
    data["full_seam_adapter"]["case_counts"][0]["recording_raw_frame_count"] = 1200
    with pytest.raises(ValueError, match="raw frame count"):
        wrapper.case_by_name(data, "fresh2", independent=False)
    data = json.loads(controls.read_text())
    data["full_seam_adapter"]["case_counts"][0]["uncovered_tail_frames_after_last_endpoint"] = 39
    with pytest.raises(ValueError, match="tail frame"):
        wrapper.case_by_name(data, "fresh2", independent=False)
    data = json.loads(controls.read_text())
    data["cases"][CASE_NAMES.index("fresh2")]["windows"][2]["accepted"] = False
    data["cases"][CASE_NAMES.index("fresh2")]["windows"][2]["indices"] = [41, 45, 50, 55, 60]
    with pytest.raises(ValueError, match="exact 40/20/5"):
        wrapper.full_schedule_rows(data["cases"][CASE_NAMES.index("fresh2")])


def test_wrapper_injects_factors_confidence_and_manifest_restores_native(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.1 * i for i in range(1200)])
    stereo_report = tmp_path / "stereo.json"
    write_report(stereo_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph_report, stereo_report, trajectory)
    output = tmp_path / "out.csv"
    report_path = tmp_path / "fusion_report.json"
    captured = {}
    original_merge = wrapper.native.merge_stereo_reports
    original_conf = wrapper.native.stereo_observation_confidence
    original_run = wrapper.native.run

    def fake_main():
        argv = sys.argv[1:]
        args = Namespace(
            trajectory=Path(argv[argv.index("--trajectory") + 1]),
            stereo_report=Path(argv[argv.index("--stereo-report") + 1]),
            output=Path(argv[argv.index("--output") + 1]),
            report=Path(argv[argv.index("--report") + 1]),
        )
        wrapper.native.run(args)
        return 0

    def fake_run(args):
        primary = json.loads(args.stereo_report.read_text())
        merged = wrapper.native.merge_stereo_reports(primary, [])
        captured["observations"] = merged["observations"]
        captured["confidence"] = wrapper.native.stereo_observation_confidence(merged["observations"][-1], 1.0)
        args.output.write_text("trajectory\n")
        report = {"result": "PASS"}
        args.report.write_text(json.dumps(report) + "\n")
        return report

    monkeypatch.setattr(wrapper.native, "main", fake_main)
    monkeypatch.setattr(wrapper.native, "run", fake_run)

    assert wrapper.main([
        "--seam-window-controls", str(controls),
        "--seam-window-case", "fresh2",
        "--seam-window-mode", "joint",
        "--trajectory", str(trajectory),
        "--stereo-report", str(stereo_report),
        "--stream", "infrared_left",
        "--output", str(output),
        "--report", str(report_path),
    ]) == 0

    assert len(captured["observations"]) == 59
    assert captured["observations"][-1]["metric_source"] == wrapper.METRIC_SOURCE
    assert captured["confidence"] == 1.0
    saved = json.loads(report_path.read_text())
    integration = saved["full_seam_pair_window_integration"]
    assert integration["factor_count"] == 58
    assert integration["pair_level_group_count"] == 29
    assert integration["experiment_status"] == "research_fixedcost_not_promoted"
    manifest = json.loads(Path(integration["manifest"]).read_text())
    assert manifest["fixed_penalty_role"] == "regularization_not_stochastic_sigma"
    assert wrapper.native.merge_stereo_reports is original_merge
    assert wrapper.native.stereo_observation_confidence is original_conf
    assert wrapper.native.run is fake_run


def test_wrapper_restores_on_exception_and_native_scale_ignores_no_scale():
    original_conf = wrapper.native.stereo_observation_confidence
    original_merge = wrapper.native.merge_stereo_reports
    original_run = wrapper.native.run

    scales, weights, info = wrapper.native.local_stereo_scale_state(
        [{"accepted": True, "metric_source": wrapper.METRIC_SOURCE, "first_index": 0, "second_index": 1}],
        wrapper.native.np.asarray([0.0, 1.0]),
    )

    assert scales.tolist() == [1.0, 1.0]
    assert weights.tolist() == [1.0, 1.0]
    assert info["observations"] == 0
    assert wrapper.native.stereo_observation_confidence is original_conf
    assert wrapper.native.merge_stereo_reports is original_merge
    assert wrapper.native.run is original_run


def test_wrapper_restores_native_after_exception(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.1 * i for i in range(1200)])
    stereo_report = tmp_path / "stereo.json"
    write_report(stereo_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph_report, stereo_report, trajectory)
    output = tmp_path / "out.csv"
    report_path = tmp_path / "fusion_report.json"

    original_merge = wrapper.native.merge_stereo_reports
    original_conf = wrapper.native.stereo_observation_confidence
    original_run = wrapper.native.run

    def fake_main():
        assert wrapper.native.merge_stereo_reports is not original_merge
        assert wrapper.native.stereo_observation_confidence is not original_conf
        raise RuntimeError("native failed")

    monkeypatch.setattr(wrapper.native, "main", fake_main)
    with pytest.raises(RuntimeError, match="native failed"):
        wrapper.main([
            "--seam-window-controls", str(controls),
            "--seam-window-case", "fresh2",
            "--seam-window-mode", "joint",
            "--trajectory", str(trajectory),
            "--stereo-report", str(stereo_report),
            "--stream", "infrared_left",
            "--output", str(output),
            "--report", str(report_path),
        ])

    assert wrapper.native.merge_stereo_reports is original_merge
    assert wrapper.native.stereo_observation_confidence is original_conf
    assert wrapper.native.run is original_run
