import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_slam_variant_evidence import build_variant_evidence
from slam_benchmark_environment import evaluate_environment


def write_trajectory(path: Path, positions: np.ndarray) -> None:
    times = np.linspace(100.0, 104.0, len(positions))
    quaternions = np.tile(Rotation.identity().as_quat(), (len(positions), 1))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"))
        for timestamp, position, quaternion in zip(times, positions, quaternions):
            writer.writerow(
                (
                    timestamp,
                    *position,
                    quaternion[3],
                    quaternion[0],
                    quaternion[1],
                    quaternion[2],
                )
            )


def test_build_variant_evidence_creates_hashed_distinct_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sample_count = 121
    ground_truth = np.column_stack(
        (np.linspace(0.0, 0.5, sample_count), np.zeros(sample_count), np.zeros(sample_count))
    )
    raw = ground_truth.copy()
    raw[:, 1] += np.linspace(0.0, 0.02, sample_count)
    auto_loop = ground_truth.copy()
    auto_loop[:, 1] += np.linspace(0.0, 0.003, sample_count)
    depth_plane = auto_loop.copy()
    depth_plane[:, 2] += np.linspace(0.0, 0.001, sample_count)
    write_trajectory(run_dir / "vio_raw.csv", raw)
    write_trajectory(run_dir / "vio_corrected_stream.csv", auto_loop)
    depth_path = tmp_path / "depth_plane.csv"
    truth_path = tmp_path / "truth.csv"
    write_trajectory(depth_path, depth_plane)
    write_trajectory(truth_path, ground_truth)
    run_report = {
        "result": "PASS",
        "failure_scope": "SLAM",
        "session": "/recordings/session",
        "pose_coverage": 1.0,
        "automatic_loop_accepts": 1,
        "loop_input_drop_events": 0,
        "estimator_keyframe_queue_drop_events": 0,
        "benchmark_environment": evaluate_environment(
            {
                "load_average": {"one_minute_per_cpu": 0.1},
                "memory_available_gib": 16.0,
                "pressure": {
                    "cpu": {"some": {"avg10": 0.1}},
                    "memory": {"full": {"avg10": 0.0}},
                    "io": {"full": {"avg10": 0.1}},
                },
                "conflicting_processes": [],
            }
        ),
    }
    (run_dir / "run_acceptance.json").write_text(
        json.dumps(run_report), encoding="utf-8"
    )
    factor_report = tmp_path / "depth_factor.json"
    factor_report.write_text(
        json.dumps(
            {
                "result": "PASS",
                "plane_factor": {
                    "status": "ACTIVE",
                    "causal": True,
                    "uses_absolute_height": False,
                    "uses_endpoint_constraint": False,
                },
            }
        ),
        encoding="utf-8",
    )

    fragment = build_variant_evidence(
        dataset_id="hidden-straight-01",
        run_dir=run_dir,
        depth_trajectory=depth_path,
        depth_factor_report=factor_report,
        ground_truth=truth_path,
        output_dir=tmp_path / "evidence",
        max_interpolation_gap_s=0.1,
        rpe_delta_samples=10,
    )

    assert fragment["dataset_id"] == "hidden-straight-01"
    bundled_truth = Path(fragment["ground_truth"])
    assert bundled_truth.is_file()
    assert bundled_truth.parent == (tmp_path / "evidence").resolve()
    assert bundled_truth != truth_path.resolve()
    assert hashlib.sha256(bundled_truth.read_bytes()).hexdigest() == fragment[
        "ground_truth_sha256"
    ]
    entries = fragment["variant_reports"]
    assert set(entries) == {"raw_vins", "auto_loop", "depth_plane"}
    trajectory_paths = {Path(entry["trajectory"]) for entry in entries.values()}
    assert len(trajectory_paths) == 3
    for variant, entry in entries.items():
        for path_key, hash_key in (
            ("run_report", "run_report_sha256"),
            ("trajectory", "trajectory_sha256"),
            ("ground_truth_report", "ground_truth_report_sha256"),
        ):
            path = Path(entry[path_key])
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry[hash_key]
        assert json.loads(Path(entry["run_report"]).read_text())["variant"] == variant
        metrics = json.loads(Path(entry["ground_truth_report"]).read_text())
        assert metrics["variant"] == variant
        assert Path(metrics["estimate"]).resolve() == Path(entry["trajectory"]).resolve()
        assert Path(metrics["ground_truth"]).resolve() == bundled_truth.resolve()
    depth_entry = entries["depth_plane"]
    assert Path(depth_entry["factor_report"]).is_file()
    assert (
        hashlib.sha256(Path(depth_entry["factor_report"]).read_bytes()).hexdigest()
        == depth_entry["factor_report_sha256"]
    )


def test_build_variant_evidence_rejects_infrastructure_run(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_acceptance.json").write_text(
        json.dumps({"result": "FAIL", "failure_scope": "INFRASTRUCTURE"}),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="valid SLAM evaluation"):
        build_variant_evidence(
            dataset_id="invalid",
            run_dir=run_dir,
            depth_trajectory=tmp_path / "depth.csv",
            depth_factor_report=tmp_path / "factor.json",
            ground_truth=tmp_path / "truth.csv",
            output_dir=tmp_path / "evidence",
        )


def test_build_variant_evidence_rejects_missing_environment_preflight(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_acceptance.json").write_text(
        json.dumps({"result": "PASS", "failure_scope": "SLAM"}),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="benchmark environment preflight"):
        build_variant_evidence(
            dataset_id="invalid-environment",
            run_dir=run_dir,
            depth_trajectory=tmp_path / "depth.csv",
            depth_factor_report=tmp_path / "factor.json",
            ground_truth=tmp_path / "truth.csv",
            output_dir=tmp_path / "evidence",
        )
