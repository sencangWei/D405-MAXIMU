import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_prepare_script():
    path = ROOT / "scripts" / "prepare_mast3r_d405_finetune.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load_prepare_script()


def test_materialize_samples_preserves_order_and_records_rejections(
    monkeypatch, tmp_path
):
    samples = [
        {"session_id": "session", "input_index": index}
        for index in (3, 1, 2)
    ]

    def fake_materialize(sample, _output):
        if sample["input_index"] == 1:
            return None, "bad_depth"
        return {**sample, "depth_left": f"{sample['input_index']}.png"}, None

    monkeypatch.setattr(prepare, "materialize_depth", fake_materialize)

    accepted, rejected = prepare.materialize_samples(samples, tmp_path, workers=2)

    assert [sample["input_index"] for sample in accepted] == [3, 2]
    assert rejected == [
        {
            "session_id": "session",
            "input_index": 1,
            "reason": "bad_depth",
        }
    ]


def test_materialize_samples_rejects_nonpositive_worker_count(tmp_path):
    with pytest.raises(ValueError, match="workers must be positive"):
        prepare.materialize_samples([], tmp_path, workers=0)
