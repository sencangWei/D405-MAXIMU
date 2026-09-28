import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest


PATH = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928/probe_keyframe_update.py"
SPEC = importlib.util.spec_from_file_location("probe_keyframe_update", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class Sim:
    def __init__(self, data):
        self.data = np.asarray(data, dtype=float)

    def inv(self):
        return Sim(np.array([0, 0, 0, 0, 0, 0, 1, 1], dtype=float))

    def __mul__(self, other):
        return other

    def act(self, points):
        return np.asarray(points) * self.data[7] + self.data[:3]


class Keyframes:
    def __init__(self, keyframe):
        self.keyframe = keyframe

    def last_keyframe(self):
        return self.keyframe


class FakeFrame:
    def __init__(self, frame_id, x_offset=0.0):
        self.frame_id = frame_id
        self.X_canon = np.arange(18, dtype=float).reshape(6, 3) + x_offset
        self.C = np.ones((6, 1), dtype=float)
        self.N = 2
        self.T_WC = Sim(np.array([1, 2, 3, 0, 0, 0, 1, 1], dtype=float))


def weighted_update(self, X, C):
    self.X_canon = (self.C * self.X_canon + C * X) / (self.C + C)
    self.C = self.C + C
    self.N += 1
    return None


def install_fake_modules(monkeypatch, update_impl=weighted_update, matcher_impl=None):
    package = types.ModuleType("mast3r_slam")
    frame_module = types.ModuleType("mast3r_slam.frame")
    tracker_module = types.ModuleType("mast3r_slam.fake_tracker")
    config_module = types.ModuleType("mast3r_slam.config")
    frame_module.Frame = FakeFrame
    frame_module.Frame.update_pointmap = update_impl
    config_module.config = {"tracking": {"filtering_mode": "weighted_pointmap"}}

    def default_matcher(model, frame, keyframe, *args, **kwargs):
        xff = np.zeros_like(keyframe.X_canon)
        cff = np.ones_like(keyframe.C)
        return (object(), object(), xff, cff, object(), keyframe.X_canon + 10, keyframe.C * 3, object())

    tracker_module.mast3r_match_asymmetric = matcher_impl or default_matcher
    monkeypatch.setitem(sys.modules, "mast3r_slam", package)
    monkeypatch.setitem(sys.modules, "mast3r_slam.frame", frame_module)
    monkeypatch.setitem(sys.modules, "mast3r_slam.fake_tracker", tracker_module)
    monkeypatch.setitem(sys.modules, "mast3r_slam.config", config_module)
    return frame_module, tracker_module


class StubRecorder:
    def __init__(self):
        self.context = None
        self.captured = None
        self.errors = []
        self.finished = []
        self.match_seen = False
        self.scale_seen = False
        self.update_seen = False

    def reset_native_capture(self, frame):
        self.frame = frame

    def prepare_capture(self, tracker, args):
        return {"keyframe_pixel_ids": np.array([0, 2, 4], dtype=int)}

    def record_match(self, frame, keyframe, result):
        if self.context is not None:
            self.match_seen = True

    def record_scale(self, pointmaps, confidence, result):
        if self.context is not None:
            self.scale_seen = True

    def record_optimize_result(self, args, result):
        if self.context is not None:
            self.opt_args = args

    def record_update(self, frame, X, C, original_update):
        if self.context is not None and frame.frame_id == self.context[1]:
            self.update_seen = True
        return original_update(frame, X, C)

    def finish_track(self, tracker, frame, outcome):
        self.finished.append((frame.frame_id, outcome, self.captured))


class Tracker:
    __module__ = "mast3r_slam.fake_tracker"

    def __init__(self, keyframe, result):
        self.model = object()
        self.keyframes = Keyframes(keyframe)
        self.result = result

    def scale_pointmaps(self, pointmaps, confidence, metric_depth):
        return pointmaps, {"accepted": True}

    def opt_pose_calib_sim3(self, *args, **kwargs):
        assert kwargs == {"flag": 1}
        return "world-pose", Sim(np.array([1, 2, 3, 0, 0, 0, 1, 1], dtype=float))

    def track(self, frame, *args, **kwargs):
        tracker_module = sys.modules["mast3r_slam.fake_tracker"]
        keyframe = self.keyframes.last_keyframe()
        _, _, xff, cff, _, xkf, ckf, _ = tracker_module.mast3r_match_asymmetric(self.model, frame, keyframe)
        (xff, xkf), _ = self.scale_pointmaps((xff, xkf), cff, None)
        frame.update_pointmap(xff, cff)
        valid = np.array([[True], [False], [True], [False], [True], [False]])
        opt = self.opt_pose_calib_sim3(None, None, None, None, None, valid, flag=1)
        keyframe.update_pointmap(opt[1].act(xkf), ckf)
        return self.result


def test_hooked_track_preserves_return_objects_and_restores(monkeypatch):
    frame_module, tracker_module = install_fake_modules(monkeypatch)
    recorder = StubRecorder()
    original_track = Tracker.track
    original_opt = Tracker.opt_pose_calib_sim3
    original_scale = Tracker.scale_pointmaps
    original_update = frame_module.Frame.update_pointmap
    original_matcher = tracker_module.mast3r_match_asymmetric
    result = (True, object(), False)
    restore = probe.install_hooks(Tracker, recorder)
    try:
        assert Tracker(FakeFrame(999), result).track(FakeFrame(1000)) is result
        assert recorder.match_seen and recorder.scale_seen and recorder.update_seen
        assert recorder.finished[0][1] is result
        assert recorder.finished[0][2]["T_post"].shape == (8,)
    finally:
        restore()
    assert Tracker.track is original_track
    assert Tracker.opt_pose_calib_sim3 is original_opt
    assert Tracker.scale_pointmaps is original_scale
    assert frame_module.Frame.update_pointmap is original_update
    assert tracker_module.mast3r_match_asymmetric is original_matcher


def test_outside_fixed_scope_does_not_record(monkeypatch):
    install_fake_modules(monkeypatch)
    recorder = StubRecorder()
    restore = probe.install_hooks(Tracker, recorder)
    try:
        result = object()
        assert Tracker(FakeFrame(10), result).track(FakeFrame(999)) is result
        assert recorder.context is None
        assert recorder.finished == []
        assert not recorder.match_seen and not recorder.scale_seen and not recorder.update_seen
    finally:
        restore()


def test_native_update_exception_propagates_and_restore_still_possible(monkeypatch):
    def exploding_update(self, X, C):
        raise RuntimeError("native boom")

    frame_module, _ = install_fake_modules(monkeypatch, update_impl=exploding_update)
    recorder = StubRecorder()
    restore = probe.install_hooks(Tracker, recorder)
    try:
        with pytest.raises(RuntimeError, match="native boom"):
            Tracker(FakeFrame(999), object()).track(FakeFrame(1000))
    finally:
        restore()
    assert frame_module.Frame.update_pointmap is exploding_update


def test_record_update_ignores_current_frame_and_captures_keyframe(monkeypatch):
    install_fake_modules(monkeypatch)
    recorder = object.__new__(probe.Recorder)
    recorder.context = (1000, 900)
    recorder.current_frame = FakeFrame(1000)
    recorder.errors = []
    recorder._opt = {"keyframe_pixel_ids": np.array([0, 2, 4], dtype=int)}
    recorder._match = {"raw_Xkf_full": np.zeros((6, 3)), "raw_Ckf_full": np.ones((6, 1))}
    recorder._scaled = {"working_Xkf_full": np.ones((6, 3)), "working_Ckf_full": np.ones((6, 1))}
    recorder._update = None
    recorder.ignored_current_updates = 0

    current = FakeFrame(1000)
    keyframe = FakeFrame(900)
    proposal = keyframe.X_canon + 5
    confidence = np.full((6, 1), 2.0)
    assert recorder.record_update(current, proposal, confidence, weighted_update) is None
    assert recorder.ignored_current_updates == 1
    assert recorder._update is None
    assert recorder.record_update(keyframe, proposal, confidence, weighted_update) is None
    assert recorder._update["raw_X_old"].shape == (3, 3)
    assert recorder._update["raw_X_proposal"].shape == (3, 3)
    assert recorder._update["raw_X_after"].shape == (3, 3)
    assert recorder._update["update_N_before"].item() == 2
    assert recorder._update["update_N_after"].item() == 3
    assert recorder._update["update_filtering_mode"].item() == "weighted_pointmap"
    assert recorder._update["ignored_current_update_count"].item() == 1


def test_finish_track_missing_update_is_fail_closed(monkeypatch):
    calls = []

    def fake_base_finish(self, tracker, frame, outcome):
        calls.append((tracker, frame, outcome))
        return "base-finish"

    monkeypatch.setattr(probe.base.Recorder, "finish_track", fake_base_finish)
    recorder = object.__new__(probe.Recorder)
    recorder.context = (1000, 900)
    recorder.captured = {"keyframe_pixel_ids": np.array([0], dtype=int)}
    recorder.errors = []
    recorder._opt = {"keyframe_pixel_ids": np.array([0], dtype=int)}
    recorder._match = {"raw_Xkf_full": np.zeros((1, 3)), "raw_Ckf_full": np.ones((1, 1))}
    recorder._scaled = {"working_Xkf_full": np.zeros((1, 3)), "working_Ckf_full": np.ones((1, 1))}
    recorder._update = None
    assert recorder.finish_track(object(), FakeFrame(1000), object()) == "base-finish"
    assert calls
    assert any(error["stage"] == "finish_keyframe_update" for error in recorder.errors)


def test_source_hashes_include_adapter_and_existing_diagnostics(monkeypatch, tmp_path):
    def fake_frozen_inputs(frozen, manifest, dataset):
        return {"base": "hash"}

    monkeypatch.setattr(probe, "_ORIGINAL_BASE_FROZEN_INPUTS", fake_frozen_inputs)
    hashes = probe.frozen_inputs(tmp_path, {}, tmp_path)
    assert str(PATH) in hashes
    assert str(PATH.with_name("keyframe_update_diagnostics.py")) in hashes


def test_producer_config_guard_binds_expected_inherited_frontend_mode(tmp_path):
    toolchain = tmp_path/"toolchain"
    (toolchain/"config").mkdir(parents=True)
    (toolchain/"config/base.yaml").write_text(
        "use_calib: false\n"
        "single_thread: false\n"
        "tracking:\n"
        "  filtering_mode: weighted_pointmap\n"
        "  stereo_pointmap_scale_prior: false\n"
        "  stereo_pointmap_depth_anchor: false\n"
        "  stereo_preserve_keyframe_pointmap: false\n"
    )
    config = tmp_path/"config.yaml"
    config.write_text(
        'inherit: "config/base.yaml"\n'
        "use_calib: false\n"
        "single_thread: true\n"
        "tracking:\n"
        "  stereo_pointmap_scale_prior: false\n"
        "  stereo_pointmap_depth_anchor: false\n"
        "  stereo_preserve_keyframe_pointmap: false\n"
    )
    actual = probe.validate_producer_config(config, toolchain)
    assert actual["single_thread"] is True
    assert actual["tracking.filtering_mode"] == "weighted_pointmap"
    config.write_text(config.read_text().replace("single_thread: true", "single_thread: false"))
    with pytest.raises(ValueError, match="producer config"):
        probe.validate_producer_config(config, toolchain)


def test_producer_config_guard_refuses_unexpected_inherit(tmp_path):
    toolchain = tmp_path/"toolchain"
    (toolchain/"config").mkdir(parents=True)
    (toolchain/"config/base.yaml").write_text("{}\n")
    config = tmp_path/"config.yaml"
    config.write_text('inherit: "../outside.yaml"\nsingle_thread: true\n')
    with pytest.raises(ValueError, match="inherit"):
        probe.validate_producer_config(config, toolchain)


def test_actual_frozen_manifest_config_matches_native_effective_runtime():
    trace = Path("reports/metric_window_bundle_20260928/frontend_geometry_probe_v1/fresh4_mast3r/geometry_trace.json")
    if not trace.exists():
        pytest.skip("actual frozen probe trace unavailable")
    frozen = Path(json.loads(trace.read_text())["frozen"])
    manifest = json.loads((frozen/"run_manifest.json").read_text())
    effective = probe.effective_producer_config(manifest["config"], manifest["toolchain"])

    config_py = Path(manifest["toolchain"])/"mast3r_slam/config.py"
    spec = importlib.util.spec_from_file_location("native_mast3r_config_for_test", config_py)
    native = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    cwd = Path.cwd()
    try:
        os.chdir(manifest["toolchain"])
        spec.loader.exec_module(native)
        native.load_config(manifest["config"])
    finally:
        os.chdir(cwd)

    assert effective["tracking"]["filtering_mode"] == native.config["tracking"]["filtering_mode"]
    assert effective["tracking"]["filtering_mode"] == "weighted_pointmap"
    assert effective["single_thread"] is True
    assert effective["tracking"]["stereo_pointmap_scale_prior"] is False
    assert effective["tracking"]["stereo_pointmap_depth_anchor"] is False
    assert effective["tracking"]["stereo_preserve_keyframe_pointmap"] is False
    assert probe.validate_producer_config(manifest["config"], manifest["toolchain"])["tracking.filtering_mode"] == "weighted_pointmap"


def test_main_monkeypatch_path_uses_original_frozen_inputs_once_and_restores(monkeypatch, tmp_path):
    calls = []
    original_recorder = probe.base.Recorder
    original_install_hooks = probe.base.install_hooks
    original_base_frozen = probe.base.frozen_inputs
    original_captured = probe._ORIGINAL_BASE_FROZEN_INPUTS

    def fake_original(frozen, manifest, dataset):
        calls.append((frozen, manifest, dataset))
        return {"base": "hash"}

    def fake_digest(path):
        return f"digest:{Path(path).name}"

    def fake_main():
        assert probe.base.Recorder is probe.Recorder
        assert probe.base.install_hooks is probe.install_hooks
        assert probe.base.frozen_inputs is probe.frozen_inputs
        return probe.base.frozen_inputs(tmp_path/"frozen", {}, tmp_path/"dataset")

    monkeypatch.setattr(probe, "_ORIGINAL_BASE_FROZEN_INPUTS", fake_original)
    monkeypatch.setattr(probe.base, "main", fake_main)
    monkeypatch.setattr(probe.base, "digest", fake_digest)
    result = probe.main()

    assert result["base"] == "hash"
    assert result[str(PATH)] == f"digest:{PATH.name}"
    assert len(calls) == 1
    assert probe.base.Recorder is original_recorder
    assert probe.base.install_hooks is original_install_hooks
    assert probe.base.frozen_inputs is original_base_frozen
    monkeypatch.setattr(probe, "_ORIGINAL_BASE_FROZEN_INPUTS", original_captured)
