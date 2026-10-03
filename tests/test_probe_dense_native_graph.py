import importlib.util
import json
from pathlib import Path

import pytest
import torch


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "probe_dense_native_graph.py"
)
spec = importlib.util.spec_from_file_location("probe_dense_native_graph", MODULE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def _unit_pose(x=0.0):
    row = torch.zeros(1, 8)
    row[0, 0] = x
    row[0, 6] = 1.0
    row[0, 7] = 1.0
    return row


def _graph_args():
    poses = torch.cat([_unit_pose(0), _unit_pose(1), _unit_pose(2)], dim=0)
    points = torch.ones(3, 4, 3)
    conf = torch.ones(3, 4, 1)
    K = torch.eye(3)
    ii = torch.tensor([0, 1])
    jj = torch.tensor([1, 2])
    idx = torch.zeros(2, 4, dtype=torch.long)
    valid = torch.ones(2, 4, 1, dtype=torch.bool)
    Q = torch.ones(2, 4, 1)
    return [poses, points, conf, K, ii, jj, idx, valid, Q, 2, 2, 0, 1e-6,
            1.0, 1.0, 0.0, 0.0, 1, 1e-5]


def _dense_payload():
    return {
        "schema": "mast3r_dense_frame_snapshot_v1",
        "frame": {
            "frame_id": 808,
            "img": torch.ones(1, 3, 2, 2),
            "img_shape": torch.tensor([2, 2]),
            "img_true_shape": torch.tensor([2, 2]),
            "uimg": torch.ones(2, 2, 3),
            "X_canon": torch.ones(4, 3),
            "C": torch.ones(4, 1),
            "feat": torch.ones(1, 2, 3),
            "pos": torch.ones(1, 2, 2),
            "T_WC_data": _unit_pose(8),
            "N": 2,
            "N_updates": 1,
            "K": torch.eye(3),
        },
        "reference": {"frame_id": 791, "index": 0, "T_WC_data": _unit_pose(7)},
        "track_return": {"add_new_kf": False, "try_reloc": False},
    }


def test_load_snapshots_rejects_wrong_dense_schema(tmp_path):
    dense = tmp_path / "dense.pt"
    graph = tmp_path / "graph.pt"
    torch.save({"schema": "wrong"}, dense)
    torch.save({"frame_ids": [791, 809, 877], "args": [torch.zeros(1)] * 19}, graph)
    with pytest.raises(ValueError, match="dense snapshot schema"):
        probe.load_snapshots(torch, dense, graph)


def test_load_snapshots_rejects_keyframe_capture(tmp_path):
    dense = tmp_path / "dense.pt"
    graph = tmp_path / "graph.pt"
    payload = _dense_payload()
    payload["track_return"]["add_new_kf"] = True
    torch.save(payload, dense)
    torch.save({"frame_ids": [791, 809, 877], "args": _graph_args()}, graph)
    with pytest.raises(ValueError, match="ordinary non-keyframe"):
        probe.load_snapshots(torch, dense, graph)


def test_load_snapshots_rejects_bad_graph_index_and_scale(tmp_path):
    dense = tmp_path / "dense.pt"
    graph = tmp_path / "graph.pt"
    torch.save(_dense_payload(), dense)
    args = _graph_args()
    args[0][1, 7] = -1.0
    torch.save({"frame_ids": [791, 809, 877], "args": args}, graph)
    with pytest.raises(ValueError, match="scales"):
        probe.load_snapshots(torch, dense, graph)
    args = _graph_args()
    args[6][0, 0] = 99
    torch.save({"frame_ids": [791, 809, 877], "args": args}, graph)
    with pytest.raises(ValueError, match="match index"):
        probe.load_snapshots(torch, dense, graph)


def test_pose_row_requires_unit_quaternion():
    bad = torch.zeros(1, 8)
    bad[0, 7] = 1.0
    with pytest.raises(ValueError, match="quaternion"):
        probe._pose_row(torch, "pose", bad)


def test_append_dense_edges_preserves_original_prefix_and_adds_two_directions():
    args = [None] * 19
    args[4] = torch.tensor([0, 1])
    args[5] = torch.tensor([1, 2])
    args[6] = torch.zeros(2, 4, dtype=torch.long)
    args[7] = torch.ones(2, 4, 1, dtype=torch.bool)
    args[8] = torch.ones(2, 4, 1)
    match = {
        "accepted": [True, True],
        "idx_i2j": torch.full((2, 4), 3, dtype=torch.long),
        "idx_j2i": torch.full((2, 4), 2, dtype=torch.long),
        "valid_j": torch.ones(2, 4, 1, dtype=torch.bool),
        "valid_i": torch.zeros(2, 4, 1, dtype=torch.bool),
        "Qj": torch.ones(2, 4, 1) * 0.5,
        "Qi": torch.ones(2, 4, 1) * 0.25,
    }
    out, added = probe._append_dense_edges(torch, args, [791, 809, 877, 808], match)
    assert added
    assert torch.equal(out[4][:2], args[4])
    assert torch.equal(out[5][:2], args[5])
    assert out[4].tolist() == [0, 1, 0, 3, 3, 1]
    assert out[5].tolist() == [1, 2, 3, 1, 0, 3]
    assert out[6].shape[0] == 6
    assert out[7].shape[0] == 6
    assert out[8].shape[0] == 6
    assert out[6].data_ptr() != match["idx_i2j"].data_ptr()


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available")
def test_append_dense_edges_accepts_cuda_match_tensors_for_cpu_graph_args():
    args = _graph_args()
    match = {
        "accepted": [True, True],
        "idx_i2j": torch.full((2, 4), 3, dtype=torch.long, device="cuda"),
        "idx_j2i": torch.full((2, 4), 2, dtype=torch.long, device="cuda"),
        "valid_j": torch.ones(2, 4, 1, dtype=torch.bool, device="cuda"),
        "valid_i": torch.zeros(2, 4, 1, dtype=torch.bool, device="cuda"),
        "Qj": torch.ones(2, 4, 1, device="cuda") * 0.5,
        "Qi": torch.ones(2, 4, 1, device="cuda") * 0.25,
    }
    out, added = probe._append_dense_edges(torch, args, [791, 809, 877, 808], match)
    assert added
    assert out[6].device.type == "cpu"


def test_append_dense_edges_rejects_if_either_pair_fails():
    args = [None] * 19
    args[4] = torch.tensor([0])
    args[5] = torch.tensor([1])
    args[6] = torch.zeros(1, 4, dtype=torch.long)
    args[7] = torch.ones(1, 4, 1, dtype=torch.bool)
    args[8] = torch.ones(1, 4, 1)
    out, added = probe._append_dense_edges(
        torch, args, [791, 809, 877, 808], {"accepted": [True, False]}
    )
    assert not added
    assert out == args


def test_public_match_report_is_json_serializable_without_tensors():
    match = {
        "accepted": [True, False],
        "threshold": 0.2,
        "match_fraction_i_to_j": [0.3, 0.1],
        "match_fraction_j_to_i": [0.31, 0.09],
        "idx_i2j": torch.zeros(1),
    }
    public = probe._public_match_report(match)
    assert "idx_i2j" not in public
    json.dumps(public, allow_nan=False)


def test_source_run_binding_checks_dataset_config_checkpoint(tmp_path):
    source = tmp_path / "run"
    dataset = source / "dataset"
    dataset.mkdir(parents=True)
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.pth"
    config.write_text("cfg", encoding="utf-8")
    checkpoint.write_bytes(b"ckpt")
    manifest = {
        "schema": "umi_mast3r_run_v1",
        "config_sha256": probe._file_sha256(config),
        "checkpoint_sha256": probe._file_sha256(checkpoint),
    }
    (source / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _, loaded = probe.load_source_run(source, dataset, config, checkpoint)
    assert loaded == manifest
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("bad", encoding="utf-8")
    with pytest.raises(ValueError, match="config SHA"):
        probe.load_source_run(source, dataset, bad_config, checkpoint)


def test_transport_dense_pose_uses_parent_and_reference(monkeypatch):
    class FakeSim3:
        def __init__(self, row):
            self.data = row.clone()

        def inv(self):
            out = FakeSim3(self.data)
            out.data[:, 0] *= -1
            return out

        def __mul__(self, other):
            out = FakeSim3(self.data)
            out.data[:, 0] = self.data[:, 0] + other.data[:, 0]
            out.data[:, 6] = 1.0
            out.data[:, 7] = 1.0
            return out

    class FakeLietorch:
        Sim3 = FakeSim3

    parent = _unit_pose(10)[0]
    reference = _unit_pose(7)[0]
    dense = _unit_pose(9)[0]
    transported = probe._transport_dense_pose(torch, FakeLietorch, parent, reference, dense)
    assert transported.shape == (1, 8)
    assert transported[0, 0].item() == 12.0
