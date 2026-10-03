import importlib.util
import json
from pathlib import Path

import pytest
import torch


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "probe_dense_window_native_graph.py"
)
spec = importlib.util.spec_from_file_location("probe_dense_window_native_graph", MODULE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def _args():
    def pose(tx):
        row = torch.zeros(1, 8)
        row[0, 0] = tx
        row[0, 6] = 1.0
        row[0, 7] = 1.0
        return row
    points = torch.ones(3, 4, 3)
    conf = torch.ones(3, 4, 1)
    return [
        torch.cat([pose(0.0), pose(1.0), pose(2.0)]),
        points,
        conf,
        torch.eye(3),
        torch.tensor([0]),
        torch.tensor([1]),
        torch.zeros(1, 4, dtype=torch.long),
        torch.ones(1, 4, 1, dtype=torch.bool),
        torch.ones(1, 4, 1),
        2, 2, 0, 1e-6, 1.0, 1.0, 0.0, 0.0, 1, 1e-5,
    ]


def _dense_payload(frame_id=792, parent=791):
    pose = torch.zeros(1, 8)
    pose[0, 6] = 1.0
    pose[0, 7] = 1.0
    return {
        "schema": "mast3r_dense_frame_snapshot_v1",
        "requested_frame_id": frame_id,
        "frame": {
            "frame_id": frame_id,
            "img": torch.ones(1, 3, 2, 2),
            "X_canon": torch.ones(4, 3),
            "C": torch.ones(4, 1),
            "feat": torch.ones(1, 2, 3),
            "pos": torch.ones(1, 2, 2),
            "K": torch.eye(3),
            "T_WC_data": pose.clone(),
            "N": 1,
        },
        "reference": {"frame_id": parent, "T_WC_data": pose.clone()},
        "track_return": {"add_new_kf": False, "try_reloc": False},
    }


def _reference_transition_payload(frame_id=588, reference=576, *, success=True):
    pose = torch.zeros(1, 8)
    pose[0, 6] = 1.0
    pose[0, 7] = 1.0
    return {
        "schema": "mast3r_reference_transition_snapshot_v1",
        "requested_frame_id": frame_id,
        "before": {
            "resolved_reference": {
                "frame_id": reference,
                "T_WC_data": pose.clone(),
            }
        },
        "after": {
            "track_return": {
                "add_new_kf": False,
                "try_reloc": not success,
            }
        },
        "success_fields": ({
            "frame_after": {
                "frame_id": frame_id,
                "img": torch.ones(1, 3, 2, 2),
                "img_true_shape": torch.ones(1, 2),
                "X_canon": torch.ones(4, 3),
                "C": torch.ones(4, 1),
                "feat": torch.ones(1, 2, 3),
                "pos": torch.ones(1, 2, 2),
                "K": torch.eye(3),
                "T_WC_data": pose.clone(),
                "N": 1,
            }
        } if success else None),
    }


def test_interval_requires_continuous_parent_and_next():
    assert probe.interval_frame_ids(792, 794, 791, 795) == [791, 792, 793, 794, 795]
    with pytest.raises(ValueError, match="parent\\+1"):
        probe.interval_frame_ids(793, 794, 791, 795)


def test_sim3_array_validator_rejects_bad_shape_scale_quat_and_nonfinite():
    good = _args()[0]
    assert probe._validate_sim3_array(torch, "poses", good, 3) is good
    with pytest.raises(ValueError, match="shape"):
        probe._validate_sim3_array(torch, "poses", good[:2], 3)
    bad = good.clone()
    bad[0, 7] = 0.0
    with pytest.raises(ValueError, match="scales"):
        probe._validate_sim3_array(torch, "poses", bad, 3)
    bad = good.clone()
    bad[0, 6] = 2.0
    with pytest.raises(ValueError, match="quaternion"):
        probe._validate_sim3_array(torch, "poses", bad, 3)
    bad = good.clone()
    bad[0, 0] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        probe._validate_sim3_array(torch, "poses", bad, 3)


def test_append_chain_edges_adds_forward_and_reverse_without_mutating_prefix():
    args = _args()
    match = {
        "accepted": [True],
        "idx_i2j": torch.ones(1, 4, dtype=torch.long),
        "idx_j2i": torch.zeros(1, 4, dtype=torch.long),
        "valid_j": torch.ones(1, 4, 1, dtype=torch.bool),
        "valid_i": torch.zeros(1, 4, 1, dtype=torch.bool),
        "Qj": torch.ones(1, 4, 1) * 0.5,
        "Qi": torch.ones(1, 4, 1) * 0.25,
    }
    out, added = probe._append_chain_edges(torch, args, [791, 809, 877, 792], [(791, 792, match)])
    assert added
    assert torch.equal(out[4][:1], args[4])
    assert out[4].tolist() == [0, 0, 3]
    assert out[5].tolist() == [1, 3, 0]
    assert out[6].shape[0] == 3


def test_append_chain_edges_rejects_any_failed_edge():
    out, added = probe._append_chain_edges(
        torch, _args(), [791, 809, 877, 792],
        [(791, 792, {"accepted": [False]})],
    )
    assert not added
    assert torch.equal(out[4], _args()[4])


def test_public_edges_are_json_serializable():
    report = probe._public_edges([
        (791, 792, {
            "accepted": [True],
            "threshold": 0.2,
            "match_fraction_i_to_j": [0.3],
            "match_fraction_j_to_i": [0.31],
            "idx_i2j": torch.zeros(1),
        })
    ])
    assert report == [{
        "first": 791,
        "second": 792,
        "accepted": [True],
        "threshold": 0.2,
        "match_fraction_i_to_j": [0.3],
        "match_fraction_j_to_i": [0.31],
    }]
    json.dumps(report, allow_nan=False)


def test_load_graph_rejects_dense_overlap(tmp_path):
    graph = tmp_path / "graph.pt"
    torch.save({"frame_ids": [791, 792, 809, 877], "args": _args()}, graph)
    with pytest.raises(ValueError, match="already contains"):
        probe._load_graph(torch, graph, solve=877, parent=791, next_frame=809, dense_ids=[792])


def test_reference_transition_window_dense_ids_exclude_existing_graph_keyframe(tmp_path):
    graph = tmp_path / "graph.pt"
    args = _args()
    args[0] = torch.cat([args[0], args[0][2:3].clone()])
    args[1] = torch.cat([args[1], args[1][2:3].clone()])
    args[2] = torch.cat([args[2], args[2][2:3].clone()])
    torch.save({"frame_ids": [576, 587, 592, 613], "args": args}, graph)
    # Existing default behavior still rejects an interior graph keyframe if caller
    # tries to add it as a dense state.
    with pytest.raises(ValueError, match="already contains"):
        probe._load_graph(torch, graph, solve=613, parent=576, next_frame=592, dense_ids=[577, 587])
    loaded = probe._load_graph(
        torch, graph, solve=613, parent=576, next_frame=592,
        dense_ids=[577, 578, 588, 591],
    )
    assert loaded["frame_ids"] == [576, 587, 592, 613]


def test_load_graph_rejects_bad_posture_in_graph(tmp_path):
    graph = tmp_path / "graph.pt"
    args = _args()
    args[0][0, 7] = -1.0
    torch.save({"frame_ids": [791, 809, 877], "args": args}, graph)
    with pytest.raises(ValueError, match="scales"):
        probe._load_graph(torch, graph, solve=877, parent=791, next_frame=809, dense_ids=[792])


def test_load_dense_rejects_zero_or_negative_sim3_scale(tmp_path):
    dense = tmp_path / "dense.pt"
    payload = _dense_payload()
    payload["frame"]["T_WC_data"][0, 7] = 0.0
    torch.save(payload, dense)
    with pytest.raises(ValueError, match="scales"):
        probe._load_dense(torch, dense, 792, 791, torch.eye(3))
    payload = _dense_payload()
    payload["reference"]["T_WC_data"][0, 7] = -1.0
    torch.save(payload, dense)
    with pytest.raises(ValueError, match="scales"):
        probe._load_dense(torch, dense, 792, 791, torch.eye(3))


def test_reference_transition_capture_selects_one_success_and_preserves_actual_reference(tmp_path):
    template = str(tmp_path / "ref_{frame_id}_{attempt}.pt")
    torch.save(_reference_transition_payload(588, 576, success=False), tmp_path / "ref_588_0.pt")
    torch.save(_reference_transition_payload(588, 576, success=True), tmp_path / "ref_588_1.pt")

    dense = probe.load_dense_window(
        torch, template, [588], parent=587, K=torch.eye(3),
        reference_transition_window=True, graph_ids=[576, 587, 592],
    )

    assert dense[588]["reference"]["frame_id"] == 576
    assert dense[588]["frame"]["frame_id"] == 588
    assert dense[588]["_source_path"].endswith("ref_588_1.pt")


def test_reference_transition_capture_rejects_ambiguous_missing_and_bad_reference(tmp_path):
    template = str(tmp_path / "ref_{frame_id}_{attempt}.pt")
    with pytest.raises(ValueError, match="exactly one"):
        probe.load_dense_window(
            torch, template, [588], parent=587, K=torch.eye(3),
            reference_transition_window=True, graph_ids=[576, 587, 592],
        )
    torch.save(_reference_transition_payload(588, 576, success=True), tmp_path / "ref_588_0.pt")
    torch.save(_reference_transition_payload(588, 576, success=True), tmp_path / "ref_588_1.pt")
    with pytest.raises(ValueError, match="exactly one"):
        probe.load_dense_window(
            torch, template, [588], parent=587, K=torch.eye(3),
            reference_transition_window=True, graph_ids=[576, 587, 592],
        )
    for path in tmp_path.glob("ref_588_*.pt"):
        path.unlink()
    torch.save(_reference_transition_payload(588, 575, success=True), tmp_path / "ref_588_0.pt")
    with pytest.raises(ValueError, match="existing prior graph"):
        probe.load_dense_window(
            torch, template, [588], parent=587, K=torch.eye(3),
            reference_transition_window=True, graph_ids=[576, 587, 592],
        )


def test_pose_states_uses_cli_parent_not_first_graph_node():
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

    args = _args()
    args[0][0, 0] = 100.0  # non-parent graph node
    args[0][1, 0] = 10.0   # CLI parent 791
    dense = {
        792: {
            "reference": {"frame_id": 791, "T_WC_data": args[0][1:2].clone()},
            "frame": {"T_WC_data": args[0][1:2].clone()},
        }
    }
    states = probe._pose_states(
        torch, FakeLietorch, 791, [700, 791, 809], [792], [700, 791, 809, 792],
        args, dense, args[0].clone(), torch.cat([args[0].clone(), args[0][1:2].clone()]),
    )
    assert states["792"]["initial"][0] == 10.0
