import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "assess_mast3r_fusion_input_quality.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


def reports(
    tmp_path,
    *,
    stereo_rmse_m,
    disagreement_p95_mm,
    metric_scale_disagreement=0.02,
    full_rate_correction_m=0.005,
):
    graph_output = tmp_path / "graph.csv"
    graph = {
        "schema": "umi_mast3r_stereo_imu_fusion_v2",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "output": str(graph_output),
        "stereo_translation_fusion": {
            "stereo_edge_rmse_after_m": stereo_rmse_m
        },
        "metric_scale_consistency": {
            "relative_difference": metric_scale_disagreement
        },
        "full_rate_imu_position_refinement": {
            "correction_requested_max_m": full_rate_correction_m
        },
    }
    fusion = {
        "schema": "umi_docker2_mast3r_complementary_v1",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "inputs": {"mast3r_camera_trajectory": str(graph_output)},
        "fusion": {"input_disagreement_p95_mm": disagreement_p95_mm},
    }
    return graph, fusion


def test_rejects_only_when_both_independent_checks_fail(tmp_path):
    graph, fusion = reports(
        tmp_path, stereo_rmse_m=0.0042, disagreement_p95_mm=28.0
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "REJECT"


def test_rejects_unobservable_onboard_branch_even_when_stereo_is_consistent(tmp_path):
    graph, fusion = reports(
        tmp_path, stereo_rmse_m=0.0028, disagreement_p95_mm=112.0
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "REJECT"
    assert report["reason"] == "independent_onboard_trajectory_branch_unobservable"


def test_keeps_primary_branch_when_disagreeing_position_branch_is_not_used(tmp_path):
    graph, fusion = reports(
        tmp_path, stereo_rmse_m=0.0028, disagreement_p95_mm=52.6
    )
    fusion["fusion"].update(
        {
            "local_weight": 0.0,
            "effective_local_weight_max": 0.0,
        }
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "PASS"
    assert report["position_branch_used"] is False


def test_rejects_unsupported_primary_shape_when_all_fallback_checks_are_weak(
    tmp_path,
):
    graph, fusion = reports(
        tmp_path,
        stereo_rmse_m=0.00393,
        disagreement_p95_mm=80.5,
        metric_scale_disagreement=0.043,
        full_rate_correction_m=0.005,
    )
    fusion["fusion"].update(
        {
            "local_weight": 0.0,
            "effective_local_weight_max": 0.0,
        }
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "REJECT"
    assert report["reason"] == "primary_shape_not_independently_supported"


def test_keeps_moderate_shape_disagreement_when_stereo_is_consistent(tmp_path):
    graph, fusion = reports(
        tmp_path, stereo_rmse_m=0.0028, disagreement_p95_mm=40.0
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "PASS"


def test_keeps_stereo_outlier_when_independent_trajectories_agree(tmp_path):
    graph, fusion = reports(
        tmp_path, stereo_rmse_m=0.0045, disagreement_p95_mm=12.0
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "PASS"


def test_rejects_when_scale_and_local_inertial_shape_conflict(tmp_path):
    graph, fusion = reports(
        tmp_path,
        stereo_rmse_m=0.0025,
        disagreement_p95_mm=14.0,
        metric_scale_disagreement=0.13,
        full_rate_correction_m=0.011,
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "REJECT"
    assert report["reason"] == (
        "stereo_imu_scale_and_local_inertial_shape_inconsistent"
    )


def test_keeps_large_scale_disagreement_without_local_shape_conflict(tmp_path):
    graph, fusion = reports(
        tmp_path,
        stereo_rmse_m=0.0025,
        disagreement_p95_mm=14.0,
        metric_scale_disagreement=0.13,
        full_rate_correction_m=0.006,
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "PASS"


def test_keeps_large_inertial_correction_when_metric_scale_agrees(tmp_path):
    graph, fusion = reports(
        tmp_path,
        stereo_rmse_m=0.0025,
        disagreement_p95_mm=14.0,
        metric_scale_disagreement=0.05,
        full_rate_correction_m=0.014,
    )

    report = quality.assess(graph, fusion)

    assert report["result"] == "PASS"
