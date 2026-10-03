import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch


BASE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"


def load():
    spec = importlib.util.spec_from_file_location("probe_local_tracking_metric_test", BASE / "probe_local_tracking_metric.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw_manifest(tmp_path, module):
    native, paired = tmp_path / "native", tmp_path / "paired"
    for directory in (native, paired, paired / "right"):
        directory.mkdir(parents=True, exist_ok=True)
        for fid in range(3):
            (directory / f"{fid}.png").write_bytes(bytes([fid + 1]))
    (native / "frames.csv").write_text("input_index,image\n0,0.png\n1,1.png\n2,2.png\n")
    (paired / "dataset_manifest.json").write_text(json.dumps({"stereo_depth_source": {"right_directory": "right"}}))
    paths = {str((directory / f"{fid}.png").resolve()) for directory in (native, paired, paired / "right") for fid in (0, 2)}
    return {"source_context": {"dataset": str(native), "paired_left_dataset": str(paired)},
            "raw_image_frame_ids": [0, 2], "raw_image_sha256": {path: module.replay.sha256(Path(path)) for path in paths}}


def test_actual_pair_requires_exact_frozen_raw_images(tmp_path):
    module = load()
    manifest = raw_manifest(tmp_path, module)
    assert module.verify_stereo_inputs(manifest, 2, 0) == manifest["raw_image_sha256"]
    path = Path(next(iter(manifest["raw_image_sha256"])))
    path.write_bytes(b"different raw image")
    with pytest.raises(ValueError, match="input changed"):
        module.verify_stereo_inputs(manifest, 2, 0)


@pytest.mark.parametrize("change", ["missing", "extra", "wrong_pair", "bad_ids"])
def test_unbound_or_wrong_raw_pair_fails_closed(tmp_path, change):
    module = load()
    manifest = raw_manifest(tmp_path, module)
    if change == "missing":
        manifest.pop("raw_image_sha256")
    elif change == "extra":
        manifest["raw_image_sha256"]["/unrelated/not-stereo"] = "0" * 64
    elif change == "wrong_pair":
        manifest["raw_image_frame_ids"] = [0, 1]
    else:
        manifest["raw_image_frame_ids"] = [0, True, 2]
    with pytest.raises(ValueError):
        module.verify_stereo_inputs(manifest, 2, 0)


def test_selects_only_current_to_keyframe_factor_without_external_score():
    module = load()
    chosen = {"source_index": 0, "target_index": 1, "source_frame_id": 20, "target_frame_id": 4, "pnp_report": {"accepted": True}}
    reverse = {"source_index": 1, "target_index": 0, "source_frame_id": 4, "target_frame_id": 20}
    assert module.select_local_factor([reverse, chosen], 20, 4) is chosen
    with pytest.raises(ValueError, match="unique accepted"):
        module.select_local_factor([chosen, dict(chosen)], 20, 4)


def test_dry_run_never_calls_gpu_or_creates_output(monkeypatch, tmp_path):
    module = load()
    monkeypatch.setattr(module, "prepare_probe", lambda *_args: ({}, {}, {"current_frame_id": 2, "keyframe_id": 0}, Path("snapshot.pt")))
    monkeypatch.setattr(module, "run_probe", lambda *_args: pytest.fail("dry run must not invoke GPU"))
    output = tmp_path / "output"
    assert module.main(["--capture-run", str(tmp_path), "--frame-id", "2", "--output", str(output)]) == 0
    assert not output.exists()


def test_active_queue_rejected_before_model_loading(monkeypatch, tmp_path):
    module = load()
    import run_mast3r_tracking_input_capture as capture_runner
    monkeypatch.setattr(module, "prepare_probe", lambda *_args: ({}, {}, {}, Path("snapshot.pt")))
    monkeypatch.setattr(capture_runner, "require_idle_gpu", lambda: (_ for _ in ()).throw(RuntimeError("queue is active")))
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="queue is active"):
        module.main(["--capture-run", str(tmp_path), "--frame-id", "2", "--output", str(output), "--run"])
    assert not output.exists()


def test_existing_output_not_overwritten(monkeypatch, tmp_path):
    module = load()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(module, "prepare_probe", lambda *_args: pytest.fail("must reject output first"))
    with pytest.raises(FileExistsError):
        module.main(["--capture-run", str(tmp_path), "--frame-id", "2", "--output", str(output)])


def test_reverse_match_uses_actual_reverse_decoder_tensors_and_native_q(monkeypatch):
    module = load()
    calls = []
    X = torch.arange(24, dtype=torch.float32).reshape(2, 2, 2, 3)
    D = X + 100
    Q = torch.tensor([[[1., 4.], [9., 16.]], [[25., 36.], [49., 64.]]])
    idx = torch.tensor([[2, 0, 3, 1]])
    valid = torch.tensor([[[True], [False], [True], [True]]])
    def match(*args, **kwargs):
        calls.append((args, kwargs))
        return idx, valid
    monkeypatch.setitem(sys.modules, "mast3r_slam", SimpleNamespace(matching=SimpleNamespace(match=match)))
    out = module.native_reverse_match({"frame_i": 7, "frame_j": 10, "result": (X, None, D, Q)}, device="cpu")
    assert len(calls) == 1 and calls[0][1] == {}
    assert all(torch.equal(got, expected) for got, expected in zip(calls[0][0], (X[0:1], X[1:2], D[0:1], D[1:2])))
    assert torch.equal(out["reverse_index"], idx[0])
    assert torch.equal(out["raw_reverse_valid"], valid[0])
    assert torch.equal(out["reverse_Q"], torch.tensor([[15.], [6.], [28.], [16.]]))
    out["raw_reverse_valid"].fill_(False)
    assert bool(valid[0, 0])


@pytest.mark.parametrize("accepted", [False, True])
def test_local_probe_source_change_never_solves_or_retains_pair_artifact(monkeypatch, tmp_path, accepted):
    module = load()
    source = tmp_path / "bound_helper.py"
    source.write_text("unchanged")
    hashes = {str(source): module.replay.sha256(source)}
    selected = {"current_frame_id": 2, "keyframe_id": 0}
    tracker = type("Tracker", (), {})
    monkeypatch.setattr(module, "initialize_native", lambda *_args: (tracker, object, {"local_opt": {}}, {"checkpoint": "model"}))
    monkeypatch.setattr(module.replay, "replay_twice", lambda *_args: {"repeat_exact": True})
    monkeypatch.setattr(module.replay, "validate_source_context", lambda *_args: None)
    monkeypatch.setattr(module, "verify_stereo_inputs", lambda *_args: {})
    monkeypatch.setattr(module, "decode_reverse_pair", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "native_reverse_match", lambda *_args, **_kwargs: {})
    monkeypatch.setitem(sys.modules, "mast3r_slam", SimpleNamespace(mast3r_utils=SimpleNamespace(load_mast3r=lambda *_args, **_kwargs: object())))
    monkeypatch.setitem(sys.modules, "local_tracking_pair_graph", SimpleNamespace(make_tracking_pair_graph=lambda *_args, **_kwargs: {}))
    monkeypatch.setitem(sys.modules, "local_tracking_metric_candidate", SimpleNamespace(opt_pose_calib_sim3_with_metric_factor=lambda *_args, **_kwargs: pytest.fail("changed source must be rejected before ON solve")))
    monkeypatch.setitem(sys.modules, "probe_stereo_depth_shape_native_graph", SimpleNamespace(load_depths=lambda *_args: ({}, [], {})))
    def prepare_factors(*_args):
        source.write_text("changed during factor preparation")
        factor = {"source_index": 0, "target_index": 1, "source_frame_id": 2,
                  "target_frame_id": 0, "pnp_report": {"accepted": True}}
        return ([factor] if accepted else []), {}
    monkeypatch.setitem(sys.modules, "metric_relative_pose_factor", SimpleNamespace(prepare_factors=prepare_factors))
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ValueError, match="input changed"):
        module.run_probe({"input_sha256": {}, "source_context": {}}, {}, selected, output, source_hashes=hashes)
    assert not (output / "pair_inputs.pt").exists()
