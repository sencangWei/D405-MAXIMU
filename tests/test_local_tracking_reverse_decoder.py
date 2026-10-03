import importlib.util
import sys
import types
from pathlib import Path

import pytest
import torch


BASE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
MODULE = BASE / "local_tracking_reverse_decoder.py"


def import_module(monkeypatch, fake_inference):
    utils = types.ModuleType("mast3r_slam.mast3r_utils")
    utils.mast3r_asymmetric_inference = fake_inference
    pkg = types.ModuleType("mast3r_slam")
    pkg.mast3r_utils = utils
    monkeypatch.setitem(sys.modules, "mast3r_slam", pkg)
    monkeypatch.setitem(sys.modules, "mast3r_slam.mast3r_utils", utils)
    spec = importlib.util.spec_from_file_location(f"local_tracking_reverse_decoder_{id(object())}", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encoded(frame_id=None, base=0.0):
    data = {
        "feat": torch.full((1, 4, 3), base + 1.0),
        "pos": torch.tensor([[[0, 0], [1, 0], [0, 1], [1, 1]]], dtype=torch.int64),
        "img_true_shape": torch.tensor([[2, 2]], dtype=torch.int64),
    }
    if frame_id is not None:
        data["frame_id"] = frame_id
    return data


def selected():
    return {
        "current_frame_id": 10,
        "keyframe_id": 7,
        "inference": {
            "frame_i": 10,
            "frame_j": 7,
            "encoded_inputs": {
                "frame_i": encoded(base=10.0),
                "frame_j": encoded(base=20.0),
            },
        },
    }


def fake_output():
    X = torch.arange(2 * 2 * 2 * 3, dtype=torch.float32).reshape(2, 2, 2, 3)
    C = torch.arange(8, dtype=torch.float32).reshape(2, 2, 2)
    D = X + 100
    Q = C + 10
    return X, C, D, Q


def test_reverse_uses_reference_first_current_second_calls_once_and_slices(monkeypatch):
    calls = []
    returned = fake_output()
    data = selected()

    def fake(model, frame_i, frame_j):
        calls.append((model, frame_i, frame_j))
        assert frame_i.frame_id == 7
        assert frame_j.frame_id == 10
        assert frame_i.feat is not data["inference"]["encoded_inputs"]["frame_j"]["feat"]
        assert frame_i.img is None and frame_j.img is None
        return returned

    module = import_module(monkeypatch, fake)
    model = object()
    out = module.decode_reverse_pair(model, data, device=torch.device("cpu"))
    assert len(calls) == 1
    assert calls[0][0] is model
    assert out["frame_i"] == 7
    assert out["frame_j"] == 10
    assert out["result"] is returned
    assert torch.equal(out["Xjj"], returned[0][0])
    assert torch.equal(out["Xij"], returned[0][1])
    assert torch.equal(out["Djj"], returned[2][0])
    assert torch.equal(out["Dij"], returned[2][1])
    assert torch.equal(out["Qjj"], returned[3][0])
    assert torch.equal(out["Qij"], returned[3][1])


def test_return_encoded_outer_ids_bind_frame_i_reference_and_frame_j_current(monkeypatch):
    captured = {}

    def fake(_model, frame_i, frame_j):
        captured["frame_i"] = frame_i
        captured["frame_j"] = frame_j
        return fake_output()

    module = import_module(monkeypatch, fake)
    data = selected()
    out = module.decode_reverse_pair(object(), data, device="cpu")
    assert captured["frame_i"].frame_id == 7
    assert captured["frame_j"].frame_id == 10
    assert torch.equal(captured["frame_i"].feat, data["inference"]["encoded_inputs"]["frame_j"]["feat"])
    assert torch.equal(captured["frame_j"].feat, data["inference"]["encoded_inputs"]["frame_i"]["feat"])
    assert captured["frame_i"].feat is not data["inference"]["encoded_inputs"]["frame_j"]["feat"]
    assert out["frame_i"] == 7
    assert out["encoded_inputs"]["frame_i"]["source_outer_key"] == "frame_j"
    assert out["encoded_inputs"]["frame_i"]["feat"][0, 0, 0].item() == 21.0
    assert out["frame_j"] == 10
    assert out["encoded_inputs"]["frame_j"]["source_outer_key"] == "frame_i"
    assert out["encoded_inputs"]["frame_j"]["feat"][0, 0, 0].item() == 11.0


def test_clone_isolation_and_device_move(monkeypatch):
    captured = {}

    def fake(_model, frame_i, frame_j):
        captured["feat"] = frame_i.feat
        frame_i.feat[0, 0, 0] = 999.0
        return fake_output()

    module = import_module(monkeypatch, fake)
    data = selected()
    original = data["inference"]["encoded_inputs"]["frame_j"]["feat"].clone()
    out = module.decode_reverse_pair(object(), data, device="cpu")
    assert out["encoded_inputs"]["frame_i"]["feat"][0, 0, 0].item() == 21.0
    assert out["encoded_inputs"]["frame_j"]["feat"][0, 0, 0].item() == 11.0
    assert data["inference"]["encoded_inputs"]["frame_j"]["feat"].equal(original)
    assert captured["feat"].device.type == "cpu"


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda s: s["inference"].pop("encoded_inputs"), "encoded"),
        (lambda s: s["inference"].update(frame_i=7), "frame_i/current"),
        (lambda s: s["inference"]["encoded_inputs"].pop("frame_i"), "encoded"),
        (lambda s: s["inference"].update(encoded_inputs={"current": encoded(base=10.0), "reference": encoded(base=20.0)}), "encoded"),
        (lambda s: s["inference"]["encoded_inputs"]["frame_i"].update(feat=torch.ones(1, 0, 3)), "positive"),
        (lambda s: s["inference"]["encoded_inputs"]["frame_i"].update(pos=torch.ones(1, 3, 2, dtype=torch.int64)), "pos"),
        (lambda s: s["inference"]["encoded_inputs"]["frame_i"].update(pos=torch.tensor([[[float("nan"), 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]])), "finite"),
        (lambda s: s["inference"]["encoded_inputs"]["frame_i"].update(img_true_shape=torch.ones(1, 3, dtype=torch.int64)), "img_true_shape"),
        (lambda s: s["inference"]["encoded_inputs"]["frame_i"]["feat"].__setitem__((0, 0, 0), float("nan")), "finite"),
    ],
)
def test_rejects_missing_wrong_or_nonfinite_encoded_fields(monkeypatch, mutate, match):
    module = import_module(monkeypatch, lambda *_args: fake_output())
    data = selected()
    mutate(data)
    with pytest.raises(ValueError, match=match):
        module.decode_reverse_pair(object(), data, device="cpu")


def test_rejects_bad_native_output_shape(monkeypatch):
    bad = list(fake_output())
    bad[0] = torch.zeros(1, 2, 2, 3)
    module = import_module(monkeypatch, lambda *_args: tuple(bad))
    with pytest.raises(ValueError, match="native reverse"):
        module.decode_reverse_pair(object(), selected(), device="cpu")


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda xs: xs.__setitem__(2, torch.zeros(2, 2, 2)), "D"),
        (lambda xs: xs.__setitem__(2, torch.zeros(2, 2, 2, 0)), "D"),
        (lambda xs: xs.__setitem__(0, torch.zeros(2, 0, 2, 3)), "positive"),
        (lambda xs: xs.__setitem__(3, torch.full((2, 2, 2), -1.0)), "nonnegative"),
    ],
)
def test_rejects_d_shape_empty_hw_and_negative_q(monkeypatch, mutate, match):
    bad = list(fake_output())
    mutate(bad)
    module = import_module(monkeypatch, lambda *_args: tuple(bad))
    with pytest.raises(ValueError, match=match):
        module.decode_reverse_pair(object(), selected(), device="cpu")


def test_rejects_nonfinite_native_output(monkeypatch):
    bad = list(fake_output())
    bad[3] = bad[3].clone()
    bad[3][0, 0, 0] = float("nan")
    module = import_module(monkeypatch, lambda *_args: tuple(bad))
    with pytest.raises(ValueError, match="finite"):
        module.decode_reverse_pair(object(), selected(), device="cpu")
