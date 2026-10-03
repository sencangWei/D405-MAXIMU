import importlib.util
import sys
import types
from pathlib import Path

import pytest
import torch


BASE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
MODULE = BASE / "local_tracking_pair_graph.py"


class FakePose:
    def __init__(self, data):
        self.data = data

    def inv(self):
        out = self.data.clone()
        out[:, :3] *= -1
        return type(self)(out)

    def __mul__(self, other):
        out = self.data.clone()
        out[:, :3] += other.data[:, :3]
        return type(self)(out)


def install_deps(monkeypatch, selected):
    replay = types.ModuleType("replay_mast3r_tracking_capture")

    def restore_inputs(opt, sim3_cls):
        return {
            "T_WCf": sim3_cls(opt["T_WCf"]["data"].clone()),
            "T_WCk": sim3_cls(opt["T_WCk"]["data"].clone()),
            "metric_translation_target": opt.get("metric_translation_target"),
        }

    replay.restore_inputs = restore_inputs
    pair = types.ModuleType("local_tracking_pair_inputs")
    pair.select_tracking_match = lambda payload: selected
    geom = types.ModuleType("mast3r_slam.geometry")

    def constrain_points_to_ray(img_size, Xs, K):
        out = Xs.clone()
        out[..., 0] = out[..., 2] * 0.1
        out[..., 1] = out[..., 2] * 0.2
        return out

    geom.constrain_points_to_ray = constrain_points_to_ray
    pkg = types.ModuleType("mast3r_slam")
    pkg.geometry = geom
    monkeypatch.setitem(sys.modules, "replay_mast3r_tracking_capture", replay)
    monkeypatch.setitem(sys.modules, "local_tracking_pair_inputs", pair)
    monkeypatch.setitem(sys.modules, "mast3r_slam", pkg)
    monkeypatch.setitem(sys.modules, "mast3r_slam.geometry", geom)


def import_module(monkeypatch, selected):
    install_deps(monkeypatch, selected)
    spec = importlib.util.spec_from_file_location(f"local_tracking_pair_graph_{id(object())}", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def local_cfg():
    return {
        "pixel_border": 1,
        "depth_eps": 1e-4,
        "sigma_pixel": 2.0,
        "sigma_depth": 3.0,
        "C_conf": 0.5,
        "Q_conf": 0.25,
        "max_iters": 4,
        "delta_norm": 1e-6,
    }


def payload():
    idx = torch.tensor([2, 0, 3, 1], dtype=torch.int64)
    z_cur = torch.tensor([[1.0], [2.0], [3.0], [4.0]])
    z_ref = torch.tensor([[5.0], [6.0], [7.0], [8.0]])
    current_full = torch.cat((torch.zeros(4, 2), z_cur), dim=1)
    ref_full = torch.cat((torch.zeros(4, 2), z_ref), dim=1)
    constrained_current = current_full.clone()
    constrained_current[:, 0] = constrained_current[:, 2] * 0.1
    constrained_current[:, 1] = constrained_current[:, 2] * 0.2
    constrained_ref = ref_full.clone()
    constrained_ref[:, 0] = constrained_ref[:, 2] * 0.1
    constrained_ref[:, 1] = constrained_ref[:, 2] * 0.2
    return {
        "track_entry": {"frame_id": 10, "reference_frame_id": 7, "cfg": {"stereo_fix_pose_scale": False}},
        "get_points_poses": {
            "frame_id": 10,
            "keyframe_id": 7,
            "idx_f2k": idx,
            "img_size": (2, 2),
            "use_calib": True,
            "K": torch.eye(3),
            "current": {"X_canon": current_full, "confidence": torch.arange(4, dtype=torch.float32).view(4, 1)},
            "reference": {"X_canon": ref_full, "confidence": torch.arange(10, 14, dtype=torch.float32).view(4, 1)},
            "return": (constrained_current[idx], constrained_ref, "Twcf", "Twck", torch.ones(4, 1), torch.ones(4, 1), None, None),
        },
        "opt_pose_calib_sim3": {
            "T_WCf": {"data": torch.tensor([[1.0, 0, 0, 0, 0, 0, 1, 1]])},
            "T_WCk": {"data": torch.tensor([[0.25, 0, 0, 0, 0, 0, 1, 1]])},
            "metric_translation_target": None,
            "Xf": constrained_current[idx],
            "Xk": constrained_ref,
            "K": torch.eye(3),
            "img_size": (2, 2),
        },
    }


def selected():
    return {
        "current_frame_id": 10,
        "keyframe_id": 7,
        "forward_index": torch.tensor([2, 0, 3, 1], dtype=torch.int64),
        "matching_call": {"idx_1_to_2_init": torch.tensor([[1, 2, 3, 0]])},
        "raw_forward_valid": torch.tensor([[True], [False], [True], [False]]),
        "tracking_valid": torch.tensor([[False], [False], [False], [False]]),
        "forward_Q": torch.tensor([[0.1], [0.2], [0.3], [0.4]]),
    }


def reverse():
    return {
        "frame_i": 7,
        "frame_j": 10,
        "reverse_index": torch.tensor([1, 3, 0, 2], dtype=torch.int64),
        "raw_reverse_valid": torch.tensor([[False], [True], [True], [False]]),
        "reverse_Q": torch.tensor([[0.5], [0.6], [0.7], [0.8]]),
    }


def test_graph_args_nonidentity_mapping_raw_valid_poses_and_ray_constraints(monkeypatch):
    module = import_module(monkeypatch, selected())
    graph = module.make_tracking_pair_graph(payload(), selected(), reverse(), local_cfg=local_cfg(), sim3_cls=FakePose)
    args = graph["args"]
    assert len(args) == 19
    assert graph["frame_ids"] == [10, 7]
    assert torch.equal(args[4], torch.tensor([0, 1]))
    assert torch.equal(args[5], torch.tensor([1, 0]))
    assert torch.equal(args[6], torch.stack((selected()["forward_index"], reverse()["reverse_index"])))
    assert torch.equal(args[7], torch.stack((selected()["raw_forward_valid"], reverse()["raw_reverse_valid"])))
    assert not torch.equal(args[7][0], selected()["tracking_valid"])
    assert torch.equal(args[8], torch.stack((selected()["forward_Q"], reverse()["reverse_Q"])))
    assert args[9:19] == (2, 2, 1, 1e-4, 2.0, 3.0, 0.5, 0.25, 4, 1e-6)
    assert torch.equal(args[0], torch.tensor([[0.75, 0, 0, 0, 0, 0, 1, 1.0], [0, 0, 0, 0, 0, 0, 1, 1.0]]))
    assert torch.equal(args[1][0, selected()["forward_index"]], payload()["opt_pose_calib_sim3"]["Xf"])
    assert torch.equal(args[1][1], payload()["opt_pose_calib_sim3"]["Xk"])


def test_requires_complete_local_cfg_and_rejects_modes_or_bad_reverse(monkeypatch):
    module = import_module(monkeypatch, selected())
    incomplete = dict(local_cfg())
    incomplete.pop("sigma_depth")
    with pytest.raises(ValueError, match="local_cfg"):
        module.make_tracking_pair_graph(payload(), selected(), reverse(), local_cfg=incomplete, sim3_cls=FakePose)
    bad_payload = payload()
    bad_payload["get_points_poses"]["use_calib"] = False
    with pytest.raises(ValueError, match="use_calib"):
        module.make_tracking_pair_graph(bad_payload, selected(), reverse(), local_cfg=local_cfg(), sim3_cls=FakePose)
    bad_payload = payload()
    bad_payload["opt_pose_calib_sim3"]["metric_translation_target"] = torch.ones(3)
    with pytest.raises(ValueError, match="VINS"):
        module.make_tracking_pair_graph(bad_payload, selected(), reverse(), local_cfg=local_cfg(), sim3_cls=FakePose)
    bad_reverse = reverse()
    bad_reverse["frame_i"] = 10
    with pytest.raises(ValueError, match="reverse"):
        module.make_tracking_pair_graph(payload(), selected(), bad_reverse, local_cfg=local_cfg(), sim3_cls=FakePose)
    bad_reverse = reverse()
    bad_reverse["reverse_index"] = torch.tensor([4, 0, 1, 2])
    with pytest.raises(ValueError, match="range"):
        module.make_tracking_pair_graph(payload(), selected(), bad_reverse, local_cfg=local_cfg(), sim3_cls=FakePose)
    bad_reverse = reverse()
    bad_reverse["reverse_Q"][0, 0] = float("nan")
    with pytest.raises(ValueError, match="reverse_Q"):
        module.make_tracking_pair_graph(payload(), selected(), bad_reverse, local_cfg=local_cfg(), sim3_cls=FakePose)


def test_requires_raw_forward_valid_and_strict_local_cfg_values(monkeypatch):
    legacy = selected()
    legacy["forward_valid"] = legacy.pop("raw_forward_valid")
    module = import_module(monkeypatch, legacy)
    with pytest.raises(ValueError, match="raw_forward_valid"):
        module.make_tracking_pair_graph(payload(), legacy, reverse(), local_cfg=local_cfg(), sim3_cls=FakePose)

    module = import_module(monkeypatch, selected())
    invalid_values = {
        "pixel_border": (True, -1, 1.5, "1"),
        "depth_eps": (0.0, -1.0, float("nan"), "1e-4"),
        "sigma_pixel": (0.0, -1.0, float("nan"), "2"),
        "sigma_depth": (0.0, -1.0, float("nan"), "3"),
        "C_conf": (-1.0, float("nan"), "0.5"),
        "Q_conf": (-1.0, float("nan"), "0.25"),
        "max_iters": (True, 0, -1, 4.5, "4"),
        "delta_norm": (0.0, -1.0, float("nan"), "1e-6"),
    }
    for key, values in invalid_values.items():
        for value in values:
            bad_cfg = dict(local_cfg())
            bad_cfg[key] = value
            with pytest.raises(ValueError, match="local_cfg"):
                module.make_tracking_pair_graph(payload(), selected(), reverse(), local_cfg=bad_cfg, sim3_cls=FakePose)


def test_checks_captured_opt_tensors_and_does_not_mutate_inputs(monkeypatch):
    module = import_module(monkeypatch, selected())
    data = payload()
    original_current = data["get_points_poses"]["current"]["X_canon"].clone()
    graph = module.make_tracking_pair_graph(data, selected(), reverse(), local_cfg=local_cfg(), sim3_cls=FakePose)
    graph["args"][1][0, 0, 2] = 999.0
    assert torch.equal(data["get_points_poses"]["current"]["X_canon"], original_current)
    bad = payload()
    bad["opt_pose_calib_sim3"]["Xf"] = torch.zeros_like(bad["opt_pose_calib_sim3"]["Xf"])
    with pytest.raises(ValueError, match="Xf"):
        module.make_tracking_pair_graph(bad, selected(), reverse(), local_cfg=local_cfg(), sim3_cls=FakePose)
    bad = payload()
    bad["opt_pose_calib_sim3"]["K"] = torch.eye(3) * 2
    with pytest.raises(ValueError, match="K"):
        module.make_tracking_pair_graph(bad, selected(), reverse(), local_cfg=local_cfg(), sim3_cls=FakePose)
