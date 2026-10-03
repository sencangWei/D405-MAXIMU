import importlib.util
import sys
import types
from pathlib import Path

import pytest


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "dense_capture_hook/sitecustomize.py"
)


class FakeBool:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value

    def __bool__(self):
        return bool(self.value)


class FakeMask:
    def __init__(self, value=True):
        self.value = value

    def all(self):
        return FakeBool(self.value)


class FakeTensor:
    def __init__(self, value, finite=True):
        self.value = value
        self.finite = finite
        self.clones = 0

    def detach(self):
        return self

    def cpu(self):
        return self

    def clone(self):
        out = FakeTensor(self.value, self.finite)
        self.clones += 1
        return out


class FakePose:
    def __init__(self, name):
        self.data = FakeTensor(f"{name}.data")


class FakeTorch(types.ModuleType):
    def __init__(self):
        super().__init__("torch")
        self.saved = []

    @staticmethod
    def is_tensor(value):
        return isinstance(value, FakeTensor)

    @staticmethod
    def isfinite(value):
        return FakeMask(value.finite)

    def save(self, payload, stream):
        self.saved.append((payload, stream.name))
        stream.write(b"snapshot")


class FakeKeyframes:
    def __init__(self, keyframes):
        self.keyframes = keyframes

    def __len__(self):
        return len(self.keyframes)

    def __getitem__(self, index):
        return self.keyframes[index]

    def last_keyframe(self):
        return self.keyframes[-1]


class FakeFrame:
    def __init__(self, frame_id):
        self.frame_id = frame_id
        for name in ("img", "img_shape", "img_true_shape", "uimg", "X_canon",
                     "C", "feat", "pos", "K"):
            setattr(self, name, FakeTensor(f"{frame_id}.{name}"))
        self.T_WC = FakePose(str(frame_id))
        self.N = 3
        self.N_updates = 4


def import_hook(
    monkeypatch,
    tmp_path,
    *,
    enabled=True,
    original_result=(False, [], False),
    frames="808",
    path=None,
):
    tracker_mod = types.ModuleType("mast3r_slam.tracker")

    class FrameTracker:
        def __init__(self):
            self.keyframes = FakeKeyframes([FakeFrame(7), FakeFrame(9)])

        def track(self, frame, diagnostic_depth=None, reference_keyframe_index=None,
                  update_reference=True):
            frame.img = FakeTensor("mutated_img")
            return original_result

    tracker_mod.FrameTracker = FrameTracker
    mast3r_mod = types.ModuleType("mast3r_slam")
    if enabled:
        fake_torch = FakeTorch()
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        monkeypatch.setitem(sys.modules, "mast3r_slam", mast3r_mod)
        monkeypatch.setitem(sys.modules, "mast3r_slam.tracker", tracker_mod)
        monkeypatch.setenv("MAST3R_DENSE_SNAPSHOT_FRAME", frames)
        monkeypatch.setenv("MAST3R_DENSE_SNAPSHOT_PATH", path or str(tmp_path / "snap.pt"))
    else:
        fake_torch = None
        monkeypatch.delenv("MAST3R_DENSE_SNAPSHOT_FRAME", raising=False)
        monkeypatch.delenv("MAST3R_DENSE_SNAPSHOT_PATH", raising=False)
    spec = importlib.util.spec_from_file_location(f"dense_hook_{id(tmp_path)}", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, FrameTracker, fake_torch


def test_default_off_does_not_import_or_wrap(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.delitem(sys.modules, "mast3r_slam.tracker", raising=False)
    import_hook(monkeypatch, tmp_path, enabled=False)
    assert "mast3r_slam.tracker" not in sys.modules


def test_successful_requested_frame_writes_cpu_clone_snapshot(monkeypatch, tmp_path):
    _, FrameTracker, fake_torch = import_hook(monkeypatch, tmp_path)
    tracker = FrameTracker()
    frame = FakeFrame(808)
    result = tracker.track(frame, reference_keyframe_index=0)
    assert result == (False, [], False)
    assert (tmp_path / "snap.pt").read_bytes() == b"snapshot"
    payload, path = fake_torch.saved[0]
    assert path == str(tmp_path / "snap.pt")
    assert payload["schema"] == "mast3r_dense_frame_snapshot_v1"
    assert payload["requested_frame_id"] == 808
    assert payload["frame"]["frame_id"] == 808
    assert payload["frame"]["img"].value == "mutated_img"
    assert payload["frame"]["img"] is not frame.img
    assert payload["frame"]["K"].value == "7.K"
    assert payload["frame"]["K"] is not tracker.keyframes[0].K
    assert payload["reference"]["index"] == 0
    assert payload["reference"]["frame_id"] == 7
    assert payload["track_return"] == {"add_new_kf": False, "try_reloc": False}


def test_new_keyframe_result_does_not_write_snapshot(monkeypatch, tmp_path):
    _, FrameTracker, fake_torch = import_hook(
        monkeypatch, tmp_path, original_result=(True, [], False)
    )
    assert FrameTracker().track(FakeFrame(808)) == (True, [], False)
    assert fake_torch.saved == []
    assert not (tmp_path / "snap.pt").exists()


def test_scalar_bool_like_false_new_keyframe_allows_snapshot(monkeypatch, tmp_path):
    _, FrameTracker, fake_torch = import_hook(
        monkeypatch, tmp_path, original_result=(FakeBool(False), [], False)
    )
    result = FrameTracker().track(FakeFrame(808))
    assert bool(result[0]) is False
    assert len(fake_torch.saved) == 1


def test_multiple_requested_frames_write_distinct_template_paths(monkeypatch, tmp_path):
    _, FrameTracker, fake_torch = import_hook(
        monkeypatch,
        tmp_path,
        frames="807,808",
        path=str(tmp_path / "snap_{frame_id}.pt"),
    )
    tracker = FrameTracker()
    tracker.track(FakeFrame(807))
    tracker.track(FakeFrame(808))
    assert (tmp_path / "snap_807.pt").read_bytes() == b"snapshot"
    assert (tmp_path / "snap_808.pt").read_bytes() == b"snapshot"
    assert [payload["requested_frame_id"] for payload, _ in fake_torch.saved] == [807, 808]


def test_multiple_requested_frames_require_path_template(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="\\{frame_id\\}"):
        import_hook(monkeypatch, tmp_path, frames="807,808", path=str(tmp_path / "snap.pt"))


@pytest.mark.parametrize("frames", ["808,808", "-1", "807,,808"])
def test_bad_requested_frame_list_is_rejected(monkeypatch, tmp_path, frames):
    with pytest.raises(ValueError, match="MAST3R_DENSE_SNAPSHOT_FRAME"):
        import_hook(
            monkeypatch,
            tmp_path,
            frames=frames,
            path=str(tmp_path / "snap_{frame_id}.pt"),
        )


def test_failed_tracking_does_not_write_snapshot(monkeypatch, tmp_path):
    _, FrameTracker, fake_torch = import_hook(
        monkeypatch, tmp_path, original_result=(False, [], True)
    )
    assert FrameTracker().track(FakeFrame(808)) == (False, [], True)
    assert fake_torch.saved == []
    assert not (tmp_path / "snap.pt").exists()


def test_open_xb_refuses_overwrite(monkeypatch, tmp_path):
    path = tmp_path / "snap.pt"
    path.write_bytes(b"old")
    _, FrameTracker, _ = import_hook(monkeypatch, tmp_path)
    with pytest.raises(FileExistsError):
        FrameTracker().track(FakeFrame(808))
    assert path.read_bytes() == b"old"


def test_hook_wraps_track_only_once(monkeypatch, tmp_path):
    _, FrameTracker, _ = import_hook(monkeypatch, tmp_path)
    first = FrameTracker.track
    spec = importlib.util.spec_from_file_location("dense_hook_again", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert FrameTracker.track is first
