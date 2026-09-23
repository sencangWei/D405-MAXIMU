from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_precision_workflow_can_reuse_matching_prepared_dataset():
    workflow = (ROOT / "scripts" / "mast3r_slam_precision_workflow.sh").read_text(
        encoding="utf-8"
    )

    assert "MAST3R_REUSE_DATASET_DIR" in workflow
    assert '"$reuse_source_session" != "$(realpath "$session")"' in workflow
    assert 'ln -s "$reuse_dataset" "$output/dataset"' in workflow
    assert "prepare_mast3r_slam_dataset.py" in workflow
