import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_mast3r_d405_window_finetune.py"
SPEC = importlib.util.spec_from_file_location(SCRIPT.stem, SCRIPT)
window = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(window)


def identity_priors(count):
    return [
        {"qx": "0", "qy": "0", "qz": "0", "qw": "1"}
        for _ in range(count)
    ]


def temporal_edge(first, second, duration, translation=0.01):
    pose = np.eye(4)
    pose[0, 3] = translation
    return {
        "session_id": "session",
        "split": "train",
        "first_input_index": first,
        "second_input_index": second,
        "first_image": f"{first}.png",
        "second_image": f"{second}.png",
        "depth_first": f"{first}_depth.png",
        "depth_second": f"{second}_depth.png",
        "intrinsics": np.eye(3).tolist(),
        "camera_pose_second": pose.tolist(),
        "duration_s": duration,
        "frame_gap": second - first,
        "tracked_depth_points": 200,
        "pnp_inlier_ratio": 0.8,
        "reprojection_p95_px": 1.5,
    }


def test_window_translation_graph_recovers_metric_chain():
    samples = [
        temporal_edge(0, 3, 0.1),
        temporal_edge(3, 6, 0.1),
        temporal_edge(6, 9, 0.1),
        temporal_edge(0, 6, 0.2, translation=0.02),
        temporal_edge(3, 9, 0.2, translation=0.02),
    ]

    positions, report = window.solve_component_positions(
        samples, identity_priors(10)
    )

    assert positions[9] == pytest.approx([0.03, 0.0, 0.0], abs=1e-8)
    assert report["edge_residual_p95_m"] < 1e-8


def test_long_window_samples_compose_short_stereo_imu_edges():
    samples = [
        temporal_edge(first, first + 3, 0.1)
        for first in range(0, 36, 3)
    ]

    result, reports = window.build_long_window_samples(
        samples,
        identity_priors(37),
        minimum_duration_s=0.8,
        maximum_duration_s=1.3,
        target_durations_s=(0.9, 1.2),
        maximum_pairs=20,
    )

    assert reports
    assert result
    assert all(0.8 <= sample["duration_s"] <= 1.3 for sample in result)
    assert all(
        sample["label_source"] == "robust_short_edge_translation_graph"
        for sample in result
    )
    assert all("ground_truth" not in sample for sample in result)
    assert result[0]["translation_m"] == pytest.approx(
        0.01 * result[0]["supporting_edges"], rel=0.2
    )


def test_long_window_pair_limit_samples_whole_session():
    samples = [
        temporal_edge(first, first + 3, 0.1)
        for first in range(0, 60, 3)
    ]

    result, _ = window.build_long_window_samples(
        samples,
        identity_priors(61),
        minimum_duration_s=0.8,
        maximum_duration_s=1.3,
        target_durations_s=(0.9, 1.2),
        maximum_pairs=3,
    )

    assert len(result) == 3
    assert result[0]["first_input_index"] == 0
    assert result[-1]["second_input_index"] == 60


def test_rejected_component_filter_only_excludes_reported_bad_edges():
    good = [temporal_edge(0, 3, 0.1), temporal_edge(3, 6, 0.1)]
    bad = [temporal_edge(20, 23, 0.1), temporal_edge(23, 26, 0.1)]
    isolated = [temporal_edge(40, 43, 0.1)]
    samples = good + bad + isolated
    reports = [
        {
            "session_id": "session",
            "first_input_index": 20,
            "last_input_index": 26,
            "accepted_for_long_windows": False,
        }
    ]

    rejected = window.rejected_source_sample_ids({"session": samples}, reports)

    assert rejected == {id(sample) for sample in bad}
