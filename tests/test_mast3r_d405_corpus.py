import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "config" / "mast3r_d405_ir_training_corpus_v2.json"


def load_corpus():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def test_v2_corpus_is_lighthouse_free_and_requires_fresh_acceptance_data():
    corpus = load_corpus()

    assert corpus["truth_policy"] == {
        "source": "d405_stereo_ir_and_onboard_imu_only",
        "never_input_to_slam": True,
        "external_ground_truth_for_training": False,
        "final_acceptance_requires_new_unseen_captures": True,
    }


def test_v2_corpus_has_unique_sessions_and_all_five_folds():
    datasets = load_corpus()["datasets"]
    ids = [dataset["id"] for dataset in datasets]
    sessions = [dataset["session"] for dataset in datasets]

    assert len(datasets) == 24
    assert len(ids) == len(set(ids))
    assert len(sessions) == len(set(sessions))
    assert {dataset["fold"] for dataset in datasets} == set(range(5))


def test_v2_validation_fold_is_grouped_by_recording_batch():
    corpus = load_corpus()
    validation_fold = corpus["cross_validation"]["default_validation_fold"]
    validation_ids = {
        dataset["id"]
        for dataset in corpus["datasets"]
        if dataset["fold"] == validation_fold
    }

    assert validation_ids == {
        "d405_20260916_115357",
        "d405_20260916_120107",
        "d405_20260916_120413",
        "d405_20260918_002714",
        "d405_20260918_003403",
    }
