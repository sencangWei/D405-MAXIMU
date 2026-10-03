import importlib.util
import json
import sys
from pathlib import Path

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "experimental_mast3r_metric_joint_adapter", ROOT / "scripts/experimental_mast3r_metric_joint_adapter.py"
)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def context_files(tmp_path, monkeypatch, **overrides):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = {"stereo_depth_source": {"right_directory": "right"}}
    (dataset / "dataset_manifest.json").write_text(json.dumps(manifest))
    (dataset / "calibration.yaml").write_text("{}")
    (dataset / "frames.csv").write_text("input_index,image\n0,0.png\n1,1.png\n2,2.png\n")
    (dataset / "right").mkdir()
    for fid in range(3):
        (dataset / f"{fid}.png").write_bytes(b"left")
        (dataset / "right" / f"{fid}.png").write_bytes(b"right")
    code = tmp_path / "factor.py"
    code.write_text("frozen objective")
    monkeypatch.setattr(adapter, "CODE_PATHS", {"metric_factor": code})
    context = {
        "schema": adapter.SCHEMA, "external_ground_truth_used": False,
        "dataset": str(dataset), "paired_left_dataset": str(dataset), "eye": "left",
        "input_sha256": {
            prefix + key: adapter.sha(dataset / filename)
            for prefix in ("native_", "paired_")
            for key, filename in (("manifest", "dataset_manifest.json"), ("frames", "frames.csv"), ("calibration", "calibration.yaml"))
        },
        "code_sha256": {"metric_factor": adapter.sha(code)},
    }
    context.update(overrides)
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    return path, dataset, code


def graph_args():
    poses = torch.zeros((2, 8))
    poses[:, 6:] = 1
    return (
        poses, torch.ones((2, 4, 3)), torch.ones((2, 4, 1)), torch.eye(3),
        torch.tensor([0, 1]), torch.tensor([1, 0]),
        torch.arange(4).repeat(2, 1), torch.ones((2, 4), dtype=torch.bool), torch.ones((2, 4)),
        2, 2, 0, .001, 1., .1, .5, .5, 2, .001,
    )


@pytest.mark.parametrize("override", [
    {"external_ground_truth_used": True}, {"eye": "center"},
    {"gt_path": "forbidden.csv"}, {"code_sha256": {}}, {"input_sha256": {}},
])
def test_context_fails_closed(tmp_path, monkeypatch, override):
    context, _, _ = context_files(tmp_path, monkeypatch, **override)
    with pytest.raises(ValueError):
        adapter.Runtime(context, tmp_path / "solve.jsonl")
    assert not (tmp_path / "solve.jsonl").exists()


def test_context_code_and_dataset_mutation_rejected(tmp_path, monkeypatch):
    context, dataset, code = context_files(tmp_path, monkeypatch)
    runtime = adapter.Runtime(context, tmp_path / "solve.jsonl")
    code.write_text("changed objective")
    with pytest.raises(ValueError, match="code changed"):
        runtime.verify_bindings()
    code.write_text("frozen objective")
    (dataset / "frames.csv").write_text("changed timeline")
    with pytest.raises(ValueError, match="source changed"):
        runtime.verify_bindings()
    runtime.log.close()


def test_context_mutation_rejected(tmp_path, monkeypatch):
    context, _, _ = context_files(tmp_path, monkeypatch)
    runtime = adapter.Runtime(context, tmp_path / "solve.jsonl")
    context.write_text(context.read_text() + " ")
    with pytest.raises(ValueError, match="context changed"):
        runtime.verify_bindings()
    runtime.log.close()


def stub_depths(monkeypatch):
    calls = []

    def load(source, graph):
        ids = graph["frame_ids"]
        calls.append(list(ids))
        return torch.tensor([[float(fid)] * 4 for fid in ids]), [], {}

    monkeypatch.setattr(adapter, "load_depths", load)
    return calls


def test_depth_cache_reuses_only_raw_depth_preserves_order_and_rejects_k_change(tmp_path, monkeypatch):
    context, _, _ = context_files(tmp_path, monkeypatch)
    runtime = adapter.Runtime(context, tmp_path / "solve.jsonl")
    calls = stub_depths(monkeypatch)
    args = graph_args()
    runtime.depths([0, 2], args)
    values = runtime.depths([1, 2], args)
    assert calls == [[0, 2], [1]]
    assert values[:, 0].tolist() == [1, 2]
    changed = list(args)
    changed[3] = args[3] * 2
    with pytest.raises(ValueError, match="shape/K changed"):
        runtime.depths([1, 2], tuple(changed))
    runtime.log.close()


def test_cached_depth_source_image_change_rejected(tmp_path, monkeypatch):
    context, dataset, _ = context_files(tmp_path, monkeypatch)
    runtime = adapter.Runtime(context, tmp_path / "solve.jsonl")
    stub_depths(monkeypatch)
    runtime.depths([0, 1], graph_args())
    (dataset / "right/1.png").write_bytes(b"changed right image")
    with pytest.raises(ValueError, match="image changed"):
        runtime.depths([0, 1], graph_args())
    runtime.log.close()


def test_no_metric_factor_is_visible_failure_not_native_baseline_success(tmp_path, monkeypatch):
    context, _, _ = context_files(tmp_path, monkeypatch)
    runtime = adapter.Runtime(context, tmp_path / "solve.jsonl")
    stub_depths(monkeypatch)
    monkeypatch.setattr(adapter, "prepare_factors", lambda *_: ([], {"accepted_pair_count": 0, "rejected_pair_count": 1}))
    before = graph_args()
    with pytest.raises(ValueError, match="no accepted metric pairs"):
        runtime.solve([0, 1], before)
    assert before[0][1, 0] == 0
    runtime.log.close()


def test_each_solve_rebuilds_current_pointmap_factors_and_logs_without_gt(tmp_path, monkeypatch):
    context, _, _ = context_files(tmp_path, monkeypatch)
    log = tmp_path / "solve.jsonl"
    runtime = adapter.Runtime(context, log)
    depth_calls = stub_depths(monkeypatch)
    pointmap_calls = []

    def prepare(graph, depth):
        pointmap_calls.append(graph["args"][1].clone())
        assert depth.shape == (2, 4)
        return [{"test": "factor"}], {"accepted_pair_count": 1, "rejected_pair_count": 0}

    def solve(args, factors, backend):
        assert factors and backend == "inspection backend"
        out = args[0].clone()
        out[1, 0] = 3
        return out, [{"iteration": 0}]

    monkeypatch.setattr(adapter, "prepare_factors", prepare)
    monkeypatch.setattr(adapter, "solve_joint", solve)
    monkeypatch.setattr(runtime, "inspection_backend", lambda: "inspection backend")
    args = graph_args()
    assert runtime.solve([0, 1], args)[1, 0] == 3
    args[1].add_(1)
    runtime.solve([0, 1], args)
    assert depth_calls == [[0, 1]]
    assert not torch.equal(pointmap_calls[0], pointmap_calls[1])
    assert args[0][1, 0] == 0  # adapter never modifies the caller's pose tensor
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) == 2
    assert all(r["mode"] == "JOINT_METRIC_NATIVE" and r["factor_count"] == 1 for r in records)
    assert all(r["external_ground_truth_used"] is False and r["precision_pass"] is False for r in records)
    assert records[0]["code_sha256"] == runtime.context["code_sha256"]
    assert torch.equal(args[0][0], runtime.solve([0, 1], args)[0])
    runtime.log.close()


def test_environment_must_explicitly_name_context_and_new_log(monkeypatch):
    monkeypatch.setattr(adapter, "_runtime", None)
    for name in ("MAST3R_METRIC_RELATIVE_JOINT_CONTEXT", "MAST3R_METRIC_RELATIVE_JOINT_LOG"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="explicit source context"):
        adapter.solve_calibrated([0, 1], graph_args())


def test_new_log_must_not_overwrite_an_existing_experiment(tmp_path, monkeypatch):
    context, _, _ = context_files(tmp_path, monkeypatch)
    log = tmp_path / "solve.jsonl"
    log.write_text("old evidence")
    with pytest.raises(FileExistsError):
        adapter.Runtime(context, log)
    assert log.read_text() == "old evidence"
