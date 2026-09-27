import csv
import hashlib
import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "fuse_mast3r_metric_windows.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
metric_windows = importlib.util.module_from_spec(spec)
sys.modules[path.stem] = metric_windows
spec.loader.exec_module(metric_windows)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_trajectory(path: Path, times: list[float]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["t_sec", "x", "y", "z", "qx", "qy", "qz", "qw"]
        )
        writer.writeheader()
        for time in times:
            writer.writerow(
                {
                    "t_sec": f"{time:.9f}",
                    "x": "0",
                    "y": "0",
                    "z": "0",
                    "qx": "0",
                    "qy": "0",
                    "qz": "0",
                    "qw": "1",
                }
            )


def factory_calibration() -> dict:
    return {
        "baseline_m": 0.018083254,
        "left_intrinsics": {"fx": 400.0, "fy": 401.0, "cx": 320.0, "cy": 240.0},
        "right_intrinsics": {"fx": 400.0, "fy": 401.0, "cx": 320.0, "cy": 240.0},
    }


def write_report(path: Path, trajectory: Path, session: Path) -> dict:
    report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "session": str(session.resolve()),
        "trajectory": str(trajectory.resolve()),
        "observation_frame": "infrared_left_camera_i",
        "factory_stereo_calibration": factory_calibration(),
        "scale_m_per_mast3r_unit": 0.25,
        "observations": [
            {
                "accepted": True,
                "first_index": 0,
                "second_index": 1,
                "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
                "scale": 0.25,
                "pnp_inlier_ratio": 0.8,
            }
        ],
    }
    path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def write_graph(path: Path, stereo_report: Path) -> dict:
    graph = {"inputs": {"stereo_report": str(stereo_report.resolve())}}
    path.write_text(json.dumps(graph, indent=2) + "\n")
    return graph


def accepted_window(window: int = 7) -> dict:
    return {
        "window": window,
        "indices": [1, 2, 3, 4, 5],
        "elapsed_s": [0.1, 0.2, 0.3, 0.4, 0.5],
        "accepted": True,
        "reason": "ok",
        "diagnostics": {
            "diagnostic_only": False,
            "solver_success": True,
            "pixel_p95_px": 0.7,
            "pixel_inlier_limit_px": 2.0,
            "pixel_inlier_fraction": 0.99,
            "minimum_pixel_inlier_fraction": 0.95,
            "gyro_p95_rad": 0.01,
            "gyro_p95_limit_rad": 0.0873,
            "gyro_bias_component_abs_max_rad_s": 0.002,
            "gyro_bias_component_limit_rad_s": 0.01,
            "minimum_depth_m": 0.12,
        },
        "endpoint_m": [0.01, -0.02, 0.03],
    }


def write_controls(
    path: Path,
    case: str,
    graph_report: Path,
    source_report: Path,
    trajectory: Path,
    extra_inputs: list[Path] | None = None,
    row: dict | None = None,
    external_reference_used: bool = False,
) -> None:
    inputs = {
        str(graph_report.resolve()): sha256(graph_report),
        str(source_report.resolve()): sha256(source_report),
        str(trajectory.resolve()): sha256(trajectory),
    }
    for item in extra_inputs or []:
        inputs[str(item.resolve())] = sha256(item)
    data = {
        "external_reference_used": external_reference_used,
        "source_sha256": {str(graph_report.resolve()): sha256(graph_report)},
        "cases": [
            {
                "case": case,
                "input_sha256": inputs,
                "windows": [row if row is not None else accepted_window()],
            }
        ],
    }
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_wrapper_appends_no_scale_metric_window_factor_and_records_manifest(
    tmp_path, monkeypatch
):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    stereo_report = tmp_path / "stereo.json"
    write_report(stereo_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph_report, stereo_report, trajectory)
    output = tmp_path / "out.csv"
    report_path = tmp_path / "fusion_report.json"
    captured = {}

    def fake_main():
        argv = sys.argv[1:]
        args = Namespace(
            trajectory=Path(argv[argv.index("--trajectory") + 1]),
            stereo_report=Path(argv[argv.index("--stereo-report") + 1]),
            output=Path(argv[argv.index("--output") + 1]),
            report=Path(argv[argv.index("--report") + 1]),
        )
        captured["code"] = metric_windows.native.run(args)
        return 0

    def fake_run(args):
        primary = json.loads(args.stereo_report.read_text())
        primary["report_path"] = str(args.stereo_report.resolve())
        merged = metric_windows.native.merge_stereo_reports(primary, [])
        captured["observations"] = merged["observations"]
        args.output.write_text("trajectory\n")
        report = {"result": "PASS", "inputs": {"stereo_report": str(args.stereo_report)}}
        args.report.write_text(json.dumps(report) + "\n")
        return report

    monkeypatch.setattr(metric_windows.native, "main", fake_main)
    monkeypatch.setattr(metric_windows.native, "run", fake_run)

    code = metric_windows.main(
        [
            "--metric-window-controls",
            str(controls),
            "--metric-window-case",
            "fresh2",
            "--trajectory",
            str(trajectory),
            "--stereo-report",
            str(stereo_report),
            "--stream",
            "infrared_left",
            "--output",
            str(output),
            "--report",
            str(report_path),
        ]
    )

    assert code == 0
    new_factor = captured["observations"][-1]
    assert new_factor["metric_source"] == "stereo_window_bundle_v1"
    assert new_factor["sample_hop"] == 4
    assert new_factor["first_index"] == 1
    assert new_factor["second_index"] == 5
    assert new_factor["metric_displacement_camera_i_m"] == [0.01, -0.02, 0.03]
    assert "scale" not in new_factor
    assert "pnp_rotation_quaternion_xyzw" not in new_factor
    saved = json.loads(report_path.read_text())
    assert saved["metric_window_integration"]["case"] == "fresh2"
    manifest_path = Path(saved["metric_window_integration"]["manifest"])
    manifest = json.loads(manifest_path.read_text())
    assert manifest["factor_count"] == 1
    assert manifest["factors_sha256"] == saved["metric_window_integration"]["factors_sha256"]


def test_rejects_changed_provenance_before_native_run(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    stereo_report = tmp_path / "stereo.json"
    write_report(stereo_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph_report, stereo_report, trajectory)
    stereo_report.write_text(stereo_report.read_text().replace("0.25", "0.26", 1))

    called = False

    def fake_main():
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(metric_windows.native, "main", fake_main)

    with pytest.raises(ValueError, match="input hash changed"):
        metric_windows.main(
            [
                "--metric-window-controls",
                str(controls),
                "--metric-window-case",
                "fresh2",
                "--trajectory",
                str(trajectory),
                "--stereo-report",
                str(stereo_report),
                "--stream",
                "infrared_left",
                "--output",
                str(tmp_path / "out.csv"),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )
    assert called is False


def test_rejects_timestamp_mismatch(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    source_trajectory = tmp_path / "source.csv"
    native_trajectory = tmp_path / "native.csv"
    write_trajectory(source_trajectory, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    write_trajectory(native_trajectory, [0.0, 0.1, 0.2, 0.31, 0.4, 0.5])
    stereo_report = tmp_path / "stereo.json"
    write_report(stereo_report, source_trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph_report, stereo_report, source_trajectory)
    monkeypatch.setattr(metric_windows.native, "main", lambda: 0)

    with pytest.raises(ValueError, match="trajectory timestamps"):
        metric_windows.main(
            [
                "--metric-window-controls",
                str(controls),
                "--metric-window-case",
                "fresh2",
                "--trajectory",
                str(native_trajectory),
                "--stereo-report",
                str(stereo_report),
                "--stream",
                "infrared_left",
                "--output",
                str(tmp_path / "out.csv"),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )


def test_refuses_existing_output_and_report(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    stereo_report = tmp_path / "stereo.json"
    write_report(stereo_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph_report, stereo_report, trajectory)
    output = tmp_path / "out.csv"
    output.write_text("already here\n")
    monkeypatch.setattr(metric_windows.native, "main", lambda: 0)

    with pytest.raises(ValueError, match="refuse to overwrite output"):
        metric_windows.main(
            [
                "--metric-window-controls",
                str(controls),
                "--metric-window-case",
                "fresh2",
                "--trajectory",
                str(trajectory),
                "--stereo-report",
                str(stereo_report),
                "--stream",
                "infrared_left",
                "--output",
                str(output),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )


def test_rejected_windows_do_not_change_native_observations(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    stereo_report = tmp_path / "stereo.json"
    source_report = write_report(stereo_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    controls = tmp_path / "summary.json"
    rejected = accepted_window()
    rejected.update({"accepted": False, "reason": "training_only_PnP_failed"})
    write_controls(controls, "fresh2", graph_report, stereo_report, trajectory, row=rejected)
    captured = {}

    def fake_main():
        argv = sys.argv[1:]
        args = Namespace(
            trajectory=Path(argv[argv.index("--trajectory") + 1]),
            stereo_report=Path(argv[argv.index("--stereo-report") + 1]),
            output=Path(argv[argv.index("--output") + 1]),
            report=Path(argv[argv.index("--report") + 1]),
        )
        metric_windows.native.run(args)
        return 0

    def fake_run(args):
        primary = json.loads(args.stereo_report.read_text())
        primary["report_path"] = str(args.stereo_report.resolve())
        merged = metric_windows.native.merge_stereo_reports(primary, [])
        captured["observations"] = merged["observations"]
        args.output.write_text("trajectory\n")
        report = {"result": "PASS"}
        args.report.write_text(json.dumps(report) + "\n")
        return report

    monkeypatch.setattr(metric_windows.native, "main", fake_main)
    monkeypatch.setattr(metric_windows.native, "run", fake_run)

    metric_windows.main(
        [
            "--metric-window-controls",
            str(controls),
            "--metric-window-case",
            "fresh2",
            "--trajectory",
            str(trajectory),
            "--stereo-report",
            str(stereo_report),
            "--stream",
            "infrared_left",
            "--output",
            str(tmp_path / "out.csv"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert captured["observations"] == source_report["observations"]


def test_rewritten_primary_matches_original_controls_provenance(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    original_report = tmp_path / "original_stereo.json"
    source_report = write_report(original_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, original_report)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph_report, original_report, trajectory)
    rewritten_report = tmp_path / "rewritten_sift.json"
    rewritten = dict(source_report)
    rewritten["observations"] = []
    rewritten["report_kind"] = "rewritten_cache"
    rewritten_report.write_text(json.dumps(rewritten, indent=2) + "\n")
    captured = {}

    def fake_main():
        argv = sys.argv[1:]
        args = Namespace(
            trajectory=Path(argv[argv.index("--trajectory") + 1]),
            stereo_report=Path(argv[argv.index("--stereo-report") + 1]),
            output=Path(argv[argv.index("--output") + 1]),
            report=Path(argv[argv.index("--report") + 1]),
        )
        metric_windows.native.run(args)
        return 0

    def fake_run(args):
        primary = json.loads(args.stereo_report.read_text())
        primary["report_path"] = str(args.stereo_report.resolve())
        merged = metric_windows.native.merge_stereo_reports(primary, [])
        captured["observations"] = merged["observations"]
        args.output.write_text("trajectory\n")
        report = {"result": "PASS"}
        args.report.write_text(json.dumps(report) + "\n")
        return report

    monkeypatch.setattr(metric_windows.native, "main", fake_main)
    monkeypatch.setattr(metric_windows.native, "run", fake_run)

    metric_windows.main(
        [
            "--metric-window-controls",
            str(controls),
            "--metric-window-case",
            "fresh2",
            "--trajectory",
            str(trajectory),
            "--stereo-report",
            str(rewritten_report),
            "--stream",
            "infrared_left",
            "--output",
            str(tmp_path / "out.csv"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )

    assert captured["observations"][-1]["metric_source"] == "stereo_window_bundle_v1"


def test_source_report_scan_skips_json_with_string_inputs(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    stereo_report = tmp_path / "stereo.json"
    write_report(stereo_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    odd_json = tmp_path / "odd.json"
    odd_json.write_text(json.dumps({"inputs": "not-a-dict"}) + "\n")
    controls = tmp_path / "summary.json"
    write_controls(
        controls,
        "fresh2",
        graph_report,
        stereo_report,
        trajectory,
        extra_inputs=[odd_json],
    )
    case = json.loads(controls.read_text())["cases"][0]

    source = metric_windows.source_report_from_case(case)

    assert source["trajectory"] == str(trajectory.resolve())


def test_rejects_rgb_stream_before_native_run(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    trajectory = tmp_path / "trajectory.csv"
    write_trajectory(trajectory, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    stereo_report = tmp_path / "stereo.json"
    write_report(stereo_report, trajectory, session)
    graph_report = tmp_path / "graph.json"
    write_graph(graph_report, stereo_report)
    controls = tmp_path / "summary.json"
    write_controls(controls, "fresh2", graph_report, stereo_report, trajectory)
    monkeypatch.setattr(metric_windows.native, "main", lambda: 0)

    with pytest.raises(ValueError, match="infrared_left"):
        metric_windows.main(
            [
                "--metric-window-controls",
                str(controls),
                "--metric-window-case",
                "fresh2",
                "--trajectory",
                str(trajectory),
                "--stereo-report",
                str(stereo_report),
                "--stream",
                "color",
                "--output",
                str(tmp_path / "out.csv"),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )


def test_accepted_row_uses_fixed_guards_and_rejects_bad_numbers():
    times = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    row = accepted_window()
    row["diagnostics"] = {
        **row["diagnostics"],
        "pixel_p95_px": 2.1,
        "pixel_inlier_limit_px": 99.0,
        "pixel_inlier_fraction": 0.94,
        "minimum_pixel_inlier_fraction": 0.0,
    }
    with pytest.raises(ValueError, match="pixel guard"):
        metric_windows.validate_accepted_row(row, times)

    row = accepted_window()
    row["diagnostics"]["gyro_p95_rad"] = float("nan")
    with pytest.raises(ValueError, match="gyro guard"):
        metric_windows.validate_accepted_row(row, times)

    row = accepted_window()
    row["diagnostics"]["gyro_bias_component_abs_max_rad_s"] = float("nan")
    with pytest.raises(ValueError, match="bias guard"):
        metric_windows.validate_accepted_row(row, times)

    row = accepted_window()
    row["diagnostics"]["minimum_depth_m"] = 0.0
    with pytest.raises(ValueError, match="minimum depth"):
        metric_windows.validate_accepted_row(row, times)

    row = accepted_window()
    row["indices"] = [1, 2, 3.5, 4, 5]
    with pytest.raises(ValueError, match="five integers"):
        metric_windows.validate_accepted_row(row, times)


@pytest.mark.parametrize('key', ['pixel_p95_px', 'pixel_inlier_fraction'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1.0])
def test_pixel_or_guard_never_accepts_malformed_statistics(key, value):
    row = accepted_window()
    row['diagnostics'][key] = value
    with pytest.raises(ValueError, match='pixel guard'):
        metric_windows.validate_accepted_row(row, np.arange(6) / 10.)


def test_metric_confidence_dispatch_preserves_native_confidence(monkeypatch):
    monkeypatch.setattr(
        metric_windows.native,
        "stereo_observation_confidence",
        lambda observation, reference_scale: 0.23,
    )
    original = metric_windows.native.stereo_observation_confidence
    dispatch = metric_windows.metric_window_confidence_dispatch(original)

    assert dispatch({"metric_source": "stereo_window_bundle_v1"}, 999.0) == 1.0
    assert dispatch({"scale": 0.5}, 1.0) == pytest.approx(0.23)


def test_no_scale_metric_factor_is_ignored_by_local_scale_state():
    observations = [
        {"accepted": True, "first_index": i, "second_index": i + 1, "scale": 1.0}
        for i in range(6)
    ]
    before = metric_windows.native.local_stereo_scale_state(
        observations, np.asarray([2.0]), reference_scale=1.0
    )
    after = metric_windows.native.local_stereo_scale_state(
        observations
        + [
            {
                "accepted": True,
                "first_index": 2,
                "second_index": 6,
                "metric_source": "stereo_window_bundle_v1",
                "metric_displacement_camera_i_m": [1.0, 0.0, 0.0],
            }
        ],
        np.asarray([2.0]),
        reference_scale=1.0,
    )

    np.testing.assert_allclose(after[0], before[0])
    np.testing.assert_allclose(after[1], before[1])
    assert after[2] == before[2]


def test_tiny_metric_factor_enters_native_full_vector_residual():
    positions = np.zeros((2, 3))
    rotations = Rotation.identity(2)
    edges = [
        {
            "accepted": True,
            "first_index": 0,
            "second_index": 1,
            "metric_source": "stereo_window_bundle_v1",
            "metric_displacement_camera_i_m": [0.004, 0.0, 0.0],
        }
    ]

    refined, quality = metric_windows.native.refine_positions(
        positions, rotations, edges
    )

    assert quality["stereo_edge_rmse_before_m"] == pytest.approx(0.004)
    assert refined[1, 0] > 0.0
