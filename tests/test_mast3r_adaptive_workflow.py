from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_adaptive_workflow_is_gt_independent_and_uses_two_onboard_candidates():
    script = (
        ROOT / "scripts" / "mast3r_slam_adaptive_precision_workflow.sh"
    ).read_text(encoding="utf-8")

    assert "mast3r_slam_d405_offline.yaml" in script
    assert "mast3r_slam_d405_offline_motion_kf_tight.yaml" in script
    assert "select_mast3r_fusion_candidate.py" in script
    assert "assess_mast3r_fusion_input_quality.py" in script
    assert "lighthouse" not in script.lower()
    assert "--ground-truth" not in script.lower()
    assert "ground_truth.csv" not in script.lower()
    assert "--gaussian-sigma-s 0.025" in script
    assert "--docker2-local-weight 0.35" not in script
    assert "--min-tight-stereo-improvement 0" in script
    assert "tight_candidate_status" in script
    assert "sparse_candidate_status" in script
    assert "both_candidates_failed_internal_quality" in script
    assert "tight_candidate_skipped_unobservable_metric_scale" in script
    assert "branch_p95" not in script
    assert '"selected_candidate": "sparse"' in script
    assert "tight_candidate_failed_internal_quality" in script
    base_workflow = (
        ROOT / "scripts" / "mast3r_slam_precision_workflow.sh"
    ).read_text(encoding="utf-8")
    assert "0.009109323" in base_workflow
    assert "--joint-metric-scale-optimization" in base_workflow
