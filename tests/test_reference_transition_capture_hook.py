import importlib.util
import os
import pickle
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest


HOOK = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "reference_transition_capture_hook/sitecustomize.py"
)


class FakeTensor:
    def __init__(self, data):
        self.array = np.asarray(data, dtype=float)
        self.device = "fake"
        self.dtype = "float64"

    @property
    def shape(self):
        return self.array.shape

    def detach(self):
        return self

    def cpu(self):
        return self

    def clone(self):
        return FakeTensor(self.array.copy())

    def __getitem__(self, item):
        return self.array[item]


class FakeFinite:
    def __init__(self, value):
        self.value = bool(value)

    def all(self):
        return self

    def item(self):
        return self.value


def _fake_torch():
    module = ModuleType("torch")
    module.is_tensor = lambda value: isinstance(value, FakeTensor)
    module.isfinite = lambda value: FakeFinite(np.isfinite(value.array).all())
    module.save = lambda payload, stream: pickle.dump(payload, stream)
    module.linalg = SimpleNamespace(vector_norm=lambda value: float(np.linalg.norm(value)))
    return module


class Pose:
    def __init__(self, tx=0.0):
        self.data = FakeTensor([[tx, 0, 0, 0, 0, 0, 1, 1]])


class Frame:
    def __init__(self, frame_id, tx=0.0):
        self.frame_id = frame_id
        self.T_WC = Pose(tx)
        self.N = 5
        self.N_updates = 1
        for name in ("img", "img_shape", "img_true_shape", "uimg", "X_canon", "C", "feat", "pos"):
            setattr(self, name, FakeTensor([[float(frame_id), 1.0]]))
        self.K = FakeTensor([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])


class Keyframes:
    def __init__(self, frames):
        self.frames = list(frames)

    def __len__(self):
        return len(self.frames)

    def __getitem__(self, index):
        return self.frames[index]

    def last_keyframe(self):
        return self.frames[-1]


def _install_fake_modules(monkeypatch, results, *, mutate_reference=False):
    tracker_mod = ModuleType("mast3r_slam.tracker")

    class FrameTracker:
        def __init__(self):
            self.keyframes = Keyframes([Frame(3, 0.3), Frame(5, 0.5)])
            self.idx_f2k = FakeTensor([[1, 2, 3]])
            self.calls = []

        def track(self, frame, diagnostic_depth=None, reference_keyframe_index=None, update_reference=True):
            self.calls.append((frame.frame_id, reference_keyframe_index, update_reference))
            result = results.pop(0)
            if mutate_reference:
                reference = (
                    self.keyframes.last_keyframe()
                    if reference_keyframe_index is None
                    else self.keyframes[int(reference_keyframe_index)]
                )
                reference.X_canon.array[0, 0] = 1234.0
                reference.C.array[0, 0] = 5678.0
                reference.T_WC.data.array[0, 0] += 20.0
            frame.T_WC.data.array[0, 0] += 10.0
            return result

    tracker_mod.FrameTracker = FrameTracker
    mast3r_pkg = ModuleType("mast3r_slam")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "mast3r_slam", mast3r_pkg)
    monkeypatch.setitem(sys.modules, "mast3r_slam.tracker", tracker_mod)
    return FrameTracker


def _load_hook(monkeypatch, name="reference_transition_hook_under_test"):
    spec = importlib.util.spec_from_file_location(name, HOOK)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _read(path):
    with Path(path).open("rb") as stream:
        return pickle.load(stream)


def test_default_off_does_not_import_tracker_or_torch(monkeypatch):
    monkeypatch.delenv("MAST3R_REFERENCE_TRANSITION_FRAMES", raising=False)
    monkeypatch.delenv("MAST3R_REFERENCE_TRANSITION_PATH", raising=False)
    monkeypatch.delenv("MAST3R_EXPORT_SNAPSHOT_PATH", raising=False)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.delitem(sys.modules, "mast3r_slam.tracker", raising=False)
    monkeypatch.delitem(sys.modules, "mast3r_slam.global_opt", raising=False)

    _load_hook(monkeypatch, "reference_transition_default_off")

    assert "torch" not in sys.modules
    assert "mast3r_slam.tracker" not in sys.modules
    assert "mast3r_slam.global_opt" not in sys.modules


def test_export_snapshot_env_composes_sibling_export_hook(monkeypatch, tmp_path):
    calls = []
    monkeypatch.delenv("MAST3R_REFERENCE_TRANSITION_FRAMES", raising=False)
    monkeypatch.delenv("MAST3R_REFERENCE_TRANSITION_PATH", raising=False)
    monkeypatch.setenv("MAST3R_EXPORT_SNAPSHOT_PATH", str(tmp_path / "export.pt"))
    monkeypatch.setattr(runpy, "run_path", lambda path: calls.append(Path(path)))

    _load_hook(monkeypatch, "reference_transition_export_compose")

    assert len(calls) == 1
    assert calls[0].name == "sitecustomize.py"
    assert calls[0].parent.name == "export_capture_hook"


def test_successful_primary_and_retry_same_frame_write_separate_cloned_snapshots(monkeypatch, tmp_path):
    result0 = (False, ["primary"], False)
    result1 = (False, ["retry"], False)
    FrameTracker = _install_fake_modules(monkeypatch, [result0, result1])
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_FRAMES", "7")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_PATH", str(tmp_path / "snap_{frame_id}_{attempt}.pt"))

    _load_hook(monkeypatch, "reference_transition_success")
    tracker = FrameTracker()
    frame = Frame(7, 0.7)

    assert tracker.track(frame) is result0
    assert tracker.track(frame, reference_keyframe_index=0, update_reference=False) is result1

    first = _read(tmp_path / "snap_7_0.pt")
    second = _read(tmp_path / "snap_7_1.pt")
    assert first["schema"] == "mast3r_reference_transition_snapshot_v1"
    assert first["before"]["resolved_reference"]["frame_id"] == 5
    assert second["before"]["resolved_reference"]["frame_id"] == 3
    assert first["after"]["track_return"] == {"add_new_kf": False, "try_reloc": False}
    assert second["after"]["update_reference"] is False
    assert "success_fields" in first
    assert "K" in first["success_fields"]["frame_after"]
    assert "K" in first["success_fields"]["reference_before_fields"]
    assert first["success_fields"]["frame_after"]["T_WC_data"].array[0, 0] == pytest.approx(10.7)
    frame.T_WC.data.array[0, 0] = 999.0
    assert first["success_fields"]["frame_after"]["T_WC_data"].array[0, 0] == pytest.approx(10.7)


def test_success_reference_fields_are_true_pre_track_clones(monkeypatch, tmp_path):
    FrameTracker = _install_fake_modules(monkeypatch, [(False, [], False)], mutate_reference=True)
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_FRAMES", "10")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_PATH", str(tmp_path / "snap_{frame_id}_{attempt}.pt"))

    _load_hook(monkeypatch, "reference_transition_preclone")
    tracker = FrameTracker()
    tracker.track(Frame(10))

    payload = _read(tmp_path / "snap_10_0.pt")
    before_fields = payload["success_fields"]["reference_before_fields"]
    assert before_fields["X_canon"].array[0, 0] == pytest.approx(5.0)
    assert before_fields["C"].array[0, 0] == pytest.approx(5.0)
    assert before_fields["T_WC_data"].array[0, 0] == pytest.approx(0.5)
    assert payload["after"]["resolved_reference"]["T_WC_data"].array[0, 0] == pytest.approx(20.5)
    assert payload["before"]["keyframe_poses"][-1]["T_WC_data"].array[0, 0] == pytest.approx(0.5)


def test_failed_call_captures_pose_only(monkeypatch, tmp_path):
    FrameTracker = _install_fake_modules(monkeypatch, [(False, [], True)])
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_FRAMES", "8")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_PATH", str(tmp_path / "snap_{frame_id}_{attempt}.pt"))

    _load_hook(monkeypatch, "reference_transition_failed")
    tracker = FrameTracker()
    result = tracker.track(Frame(8))

    payload = _read(tmp_path / "snap_8_0.pt")
    assert result == (False, [], True)
    assert payload["after"]["track_return"]["try_reloc"] is True
    assert "success_fields" not in payload
    assert payload["before"]["idx_f2k"]["present"] is True


def test_malformed_env_fails_before_wrapping(monkeypatch, tmp_path):
    FrameTracker = _install_fake_modules(monkeypatch, [(False, [], False)])
    original = FrameTracker.track
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_FRAMES", "1,2")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_PATH", str(tmp_path / "snap_{frame_id}.pt"))

    with pytest.raises(ValueError, match="frame_id.*attempt"):
        _load_hook(monkeypatch, "reference_transition_bad_env")

    assert FrameTracker.track is original


def test_no_overwrite_after_original_call(monkeypatch, tmp_path):
    result = (False, [], False)
    FrameTracker = _install_fake_modules(monkeypatch, [result])
    target = tmp_path / "snap_9_0.pt"
    target.write_bytes(b"exists")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_FRAMES", "9")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_PATH", str(tmp_path / "snap_{frame_id}_{attempt}.pt"))

    _load_hook(monkeypatch, "reference_transition_no_overwrite")
    tracker = FrameTracker()

    with pytest.raises(FileExistsError):
        tracker.track(Frame(9))
    assert tracker.calls == [(9, None, True)]


def _install_fake_global_opt(monkeypatch):
    global_opt = ModuleType("mast3r_slam.global_opt")
    calls = []

    def save_graph_snapshot_if_requested(frame_ids, args):
        calls.append((list(frame_ids), args))
        return "original-return"

    global_opt.save_graph_snapshot_if_requested = save_graph_snapshot_if_requested
    mast3r_pkg = ModuleType("mast3r_slam")
    mast3r_pkg.global_opt = global_opt
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "mast3r_slam", mast3r_pkg)
    monkeypatch.setitem(sys.modules, "mast3r_slam.global_opt", global_opt)
    return global_opt, calls


def test_graph_snapshot_wrapper_default_off_no_global_opt_import(monkeypatch):
    monkeypatch.delenv("MAST3R_REFERENCE_TRANSITION_FRAMES", raising=False)
    monkeypatch.delenv("MAST3R_REFERENCE_TRANSITION_PATH", raising=False)
    monkeypatch.delenv("MAST3R_REFERENCE_TRANSITION_GRAPH_FRAMES", raising=False)
    monkeypatch.delenv("MAST3R_REFERENCE_TRANSITION_GRAPH_PATH", raising=False)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.delitem(sys.modules, "mast3r_slam.global_opt", raising=False)

    _load_hook(monkeypatch, "reference_transition_graph_default_off")

    assert "torch" not in sys.modules
    assert "mast3r_slam.global_opt" not in sys.modules


def test_graph_snapshot_wrapper_calls_original_and_deepclones_selected_args(monkeypatch, tmp_path):
    global_opt, calls = _install_fake_global_opt(monkeypatch)
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_GRAPH_FRAMES", "587,592")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_GRAPH_PATH", str(tmp_path / "graph_{frame_id}.pt"))

    _load_hook(monkeypatch, "reference_transition_graph")
    tensor = FakeTensor([[1.0, 2.0]])
    result = global_opt.save_graph_snapshot_if_requested([576, 587], (tensor, "keep"))

    assert result == "original-return"
    assert calls == [([576, 587], (tensor, "keep"))]
    saved = _read(tmp_path / "graph_587.pt")
    assert saved["frame_ids"] == [576, 587]
    assert saved["args"][0].array.tolist() == [[1.0, 2.0]]
    assert saved["args"][1] == "keep"
    tensor.array[0, 0] = 99.0
    assert saved["args"][0].array.tolist() == [[1.0, 2.0]]
    assert not (tmp_path / "graph_592.pt").exists()


def test_graph_snapshot_wrapper_no_overwrite_and_malformed_env(monkeypatch, tmp_path):
    global_opt, _calls = _install_fake_global_opt(monkeypatch)
    target = tmp_path / "graph_613.pt"
    target.write_bytes(b"exists")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_GRAPH_FRAMES", "613")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_GRAPH_PATH", str(tmp_path / "graph_{frame_id}.pt"))
    _load_hook(monkeypatch, "reference_transition_graph_no_overwrite")
    with pytest.raises(FileExistsError):
        global_opt.save_graph_snapshot_if_requested([587, 613], (FakeTensor([[1.0]]),))

    global_opt2, _calls2 = _install_fake_global_opt(monkeypatch)
    original = global_opt2.save_graph_snapshot_if_requested
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_GRAPH_FRAMES", "613,613")
    monkeypatch.setenv("MAST3R_REFERENCE_TRANSITION_GRAPH_PATH", str(tmp_path / "graph_{frame_id}.pt"))
    with pytest.raises(ValueError, match="unique non-negative"):
        _load_hook(monkeypatch, "reference_transition_graph_bad_env")
    assert global_opt2.save_graph_snapshot_if_requested is original
