import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
import torch


MODULE = Path(__file__).resolve().parents[1] / "scripts/capture_mast3r_tracking_inputs.py"


class Pose:
    def __init__(self, value):
        self.data = torch.tensor([[float(value), 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0]])

    def inv(self):
        return self

    def __mul__(self, other):
        return other


class Frame:
    def __init__(self, frame_id):
        self.frame_id = frame_id
        self.T_WC = Pose(frame_id)
        self.X_canon = torch.tensor([[float(frame_id), 1.0, 2.0]])
        self.C = torch.tensor([[float(frame_id) + 0.5]])
        self.K = torch.eye(3)

    def get_average_conf(self):
        return self.C


class Keyframes:
    def __init__(self):
        self.items = [Frame(7), Frame(9)]

    def __getitem__(self, idx):
        return self.items[idx]

    def last_keyframe(self):
        return self.items[-1]


class Tracker:
    def __init__(self, fail_opt=False):
        self.keyframes = Keyframes()
        self.cfg = {"max_iters": 2, "C_conf": 0.0}
        self.fail_opt = fail_opt
        self.solve_calls = 0

    def track(self, frame, diagnostic_depth=None, reference_keyframe_index=None, update_reference=True):
        keyframe = (
            self.keyframes.last_keyframe()
            if reference_keyframe_index is None
            else self.keyframes[reference_keyframe_index]
        )
        idx = torch.tensor([0])
        Xf, Xk, T_WCf, T_WCk, Cf, Ck, meas, valid_meas = self.get_points_poses(
            frame, keyframe, idx, (3, 4), True, frame.K
        )
        Qk = torch.ones(1, 1)
        valid = torch.ones(1, 1, dtype=torch.bool)
        conf_w = torch.ones(1, 1)
        target = torch.tensor([1.0, 2.0, 3.0])
        T_WCf, local = self.opt_pose_calib_sim3(
            Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w, meas, valid_meas,
            frame.K, (3, 4), target, 2.5,
        )
        frame.T_WC = T_WCf
        return "ADDKF", ["match"], False

    def get_points_poses(self, frame, keyframe, idx_f2k, img_size, use_calib, K=None):
        meas = torch.tensor([[0.1, 0.2, 0.3]])
        valid_meas = torch.ones(1, 1, dtype=torch.bool)
        return (
            frame.X_canon[idx_f2k],
            keyframe.X_canon,
            frame.T_WC,
            keyframe.T_WC,
            frame.get_average_conf()[idx_f2k],
            keyframe.get_average_conf(),
            meas,
            valid_meas,
        )

    def opt_pose_calib_sim3(
        self, Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w, meas, valid_meas, K, img_size,
        metric_translation_target=None, metric_world_scale=1.0,
    ):
        if self.fail_opt:
            raise RuntimeError("boom")
        tau, cost = self.solve_pose_increment(torch.ones(1, 3), torch.zeros(1, 3), torch.ones(1, 3, 7))
        out = Pose(42)
        local = Pose(24)
        return out, local

    def solve_pose_increment(self, sqrt_info, r, J, visual_row_count=0, visual_effective_count=1):
        self.solve_calls += 1
        return torch.full((1, 7), float(self.solve_calls)), 12.5 + self.solve_calls


def import_module():
    spec = importlib.util.spec_from_file_location(f"capture_mast3r_tracking_inputs_{id(object())}", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flag_off_and_no_target_do_not_wrap_or_write(tmp_path):
    module = import_module()
    original = Tracker.track
    with module.capture_mast3r_tracking_inputs(
        Tracker, capture_dir=None, frame_ids={5}, enabled=False
    ):
        assert Tracker.track is original
    with module.capture_mast3r_tracking_inputs(
        Tracker, capture_dir=tmp_path, frame_ids=set(), enabled=True
    ):
        assert Tracker.track is original
    assert list(tmp_path.iterdir()) == []


def test_requested_frame_exact_return_input_identity_and_clone_isolation(tmp_path):
    module = import_module()
    tracker = Tracker()
    frame = Frame(11)
    original_x = frame.X_canon
    with module.capture_mast3r_tracking_inputs(
        Tracker, capture_dir=tmp_path, frame_ids={11}, enabled=True
    ):
        result = tracker.track(frame, reference_keyframe_index=0, update_reference=False)
    assert result == ("ADDKF", ["match"], False)
    assert frame.X_canon is original_x
    assert tracker.solve_calls == 1
    saved = sorted(tmp_path.glob("*.pt"))
    assert [p.name for p in saved] == ["mast3r_tracking_frame000011_attempt001.pt"]
    payload = torch.load(saved[0], weights_only=True)
    assert payload["schema"] == "mast3r_tracking_input_capture_v1"
    assert payload["status"] == "ok"
    assert payload["track_entry"]["frame_id"] == 11
    assert payload["track_entry"]["reference_frame_id"] == 7
    assert payload["track_return"] == {"add_new_kf": "ADDKF", "try_reloc": False}
    assert payload["get_points_poses"]["idx_f2k"].item() == 0
    assert payload["opt_pose_calib_sim3"]["metric_world_scale"] == 2.5
    assert torch.equal(payload["solve_pose_increment"][0]["tau"], torch.ones(1, 7))
    original_x[0, 0] = 999.0
    assert payload["get_points_poses"]["current"]["X_canon"][0, 0].item() == 11.0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["schema"] == "umi_mast3r_tracking_input_capture_v1"
    assert summary["requested_frame_ids"] == [11]
    assert summary["captures"][0]["status"] == "CAPTURED"
    assert summary["captures"][0]["path"] == "mast3r_tracking_frame000011_attempt001.pt"
    assert summary["missing_frame_ids"] == []
    assert summary["status"] == "CAPTURED_NOT_SCORED"
    assert summary["external_ground_truth_used"] is False
    assert summary["precision_pass"] is False
    assert summary["production_promoted"] is False


def test_repeated_attempts_get_suffix_and_missing_frame_record(tmp_path):
    module = import_module()
    tracker = Tracker()
    with module.capture_mast3r_tracking_inputs(
        Tracker, capture_dir=tmp_path, frame_ids={11, 12}, enabled=True
    ):
        tracker.track(Frame(11))
        tracker.track(Frame(11))
    names = sorted(p.name for p in tmp_path.glob("*.pt"))
    assert names == [
        "mast3r_tracking_frame000011_attempt001.pt",
        "mast3r_tracking_frame000011_attempt002.pt",
        "mast3r_tracking_frame000012_missing.pt",
    ]
    missing = torch.load(tmp_path / "mast3r_tracking_frame000012_missing.pt", weights_only=True)
    assert missing["status"] == "missing"
    assert missing["frame_id"] == 12
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "INCOMPLETE"
    assert summary["missing_frame_ids"] == [12]


def test_restore_on_track_exception_and_error_file(tmp_path):
    module = import_module()
    original = Tracker.track

    def broken(self, frame, *args, **kwargs):
        raise ValueError("bad frame")

    Tracker.track = broken
    try:
        with pytest.raises(ValueError, match="bad frame"):
            with module.capture_mast3r_tracking_inputs(
                Tracker, capture_dir=tmp_path, frame_ids={13}, enabled=True
            ):
                Tracker().track(Frame(13))
        assert Tracker.track is broken
    finally:
        Tracker.track = original
    payload = torch.load(tmp_path / "mast3r_tracking_frame000013_attempt001.pt", weights_only=True)
    assert payload["status"] == "error"
    assert payload["error"]["type"] == "ValueError"
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["captures"][0]["status"] == "ERROR"
    assert summary["status"] == "INCOMPLETE"


def test_failed_local_solve_is_reported_and_reraised(tmp_path):
    module = import_module()
    tracker = Tracker(fail_opt=True)
    with pytest.raises(RuntimeError, match="boom"):
        with module.capture_mast3r_tracking_inputs(
            Tracker, capture_dir=tmp_path, frame_ids={14}, enabled=True
        ):
            tracker.track(Frame(14))
    payload = torch.load(tmp_path / "mast3r_tracking_frame000014_attempt001.pt", weights_only=True)
    assert payload["status"] == "error"
    assert payload["opt_pose_calib_sim3"]["error"]["type"] == "RuntimeError"
    assert payload["error"]["type"] == "RuntimeError"


def test_unselected_frame_passthrough_does_not_snapshot(monkeypatch, tmp_path):
    module = import_module()
    tracker = Tracker()
    with module.capture_mast3r_tracking_inputs(
        Tracker, capture_dir=tmp_path, frame_ids={11}, enabled=True
    ):
        tracker.track(Frame(11))

        def forbidden_snapshot(_value):
            raise AssertionError("unselected frame must not snapshot")

        monkeypatch.setattr(module, "_snapshot", forbidden_snapshot)
        result = tracker.track(Frame(12))
    assert result == ("ADDKF", ["match"], False)


def test_soft_local_solve_failure_is_error_capture_without_reraising(tmp_path):
    module = import_module()

    class SoftFailTracker(Tracker):
        def track(self, frame, *args, **kwargs):
            try:
                self.opt_pose_calib_sim3(
                    frame.X_canon, frame.X_canon, frame.T_WC, frame.T_WC,
                    torch.ones(1, 1), torch.ones(1, 1, dtype=torch.bool),
                    torch.ones(1, 1), torch.zeros(1, 3),
                    torch.ones(1, 1, dtype=torch.bool), frame.K, (3, 4),
                )
            except RuntimeError:
                return False, [], True
            raise AssertionError("expected local solve failure")

        def opt_pose_calib_sim3(self, *args, **kwargs):
            raise RuntimeError("native swallowed failure")

    result = None
    with module.capture_mast3r_tracking_inputs(
        SoftFailTracker, capture_dir=tmp_path, frame_ids={16}, enabled=True
    ):
        result = SoftFailTracker().track(Frame(16))
    assert result == (False, [], True)
    payload = torch.load(tmp_path / "mast3r_tracking_frame000016_attempt001.pt", weights_only=True)
    assert payload["status"] == "error"
    assert payload["track_return"] == {"add_new_kf": False, "try_reloc": True}
    assert payload["opt_pose_calib_sim3"]["error"]["type"] == "RuntimeError"
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["captures"][0]["status"] == "ERROR"
    assert summary["missing_frame_ids"] == [16]
    assert summary["status"] == "INCOMPLETE"


def test_opt_pose_calib_sim3_wrapper_accepts_native_keyword_names(tmp_path):
    module = import_module()

    class KeywordTracker(Tracker):
        def track(self, frame, *args, **kwargs):
            return self.opt_pose_calib_sim3(
                Xf=frame.X_canon,
                Xk=frame.X_canon,
                T_WCf=frame.T_WC,
                T_WCk=frame.T_WC,
                Qk=torch.ones(1, 1),
                valid=torch.ones(1, 1, dtype=torch.bool),
                conf_w=torch.ones(1, 1),
                meas_k=torch.zeros(1, 3),
                valid_meas_k=torch.ones(1, 1, dtype=torch.bool),
                K=frame.K,
                img_size=(3, 4),
                metric_translation_target=torch.ones(3),
                metric_world_scale=1.25,
            )

        def opt_pose_calib_sim3(
            self, Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w,
            meas_k, valid_meas_k, K, img_size,
            metric_translation_target=None, metric_world_scale=1.0,
        ):
            assert meas_k.shape == (1, 3)
            assert valid_meas_k.shape == (1, 1)
            return "POSE", "LOCAL"

    tracker = KeywordTracker()
    with module.capture_mast3r_tracking_inputs(
        KeywordTracker, capture_dir=tmp_path, frame_ids={17}, enabled=True
    ):
        assert tracker.track(Frame(17)) == ("POSE", "LOCAL")
    payload = torch.load(tmp_path / "mast3r_tracking_frame000017_attempt001.pt", weights_only=True)
    assert payload["opt_pose_calib_sim3"]["metric_world_scale"] == 1.25


def test_get_points_poses_k_keyword_preserves_identity(tmp_path):
    module = import_module()

    class KeywordGetPointsTracker(Tracker):
        def __init__(self):
            super().__init__()
            self.saw_k_identity = False

        def track(self, frame, *args, **kwargs):
            return self.get_points_poses(
                frame, self.keyframes.last_keyframe(), torch.tensor([0]),
                (3, 4), True, K=frame.K,
            )

        def get_points_poses(self, frame, keyframe, idx_f2k, img_size, use_calib, K=None):
            assert K is frame.K
            self.saw_k_identity = True
            return super().get_points_poses(frame, keyframe, idx_f2k, img_size, use_calib, K=K)

    tracker = KeywordGetPointsTracker()
    frame = Frame(18)
    with module.capture_mast3r_tracking_inputs(
        KeywordGetPointsTracker, capture_dir=tmp_path, frame_ids={18}, enabled=True
    ):
        result = tracker.track(frame)
    assert tracker.saw_k_identity is True
    assert torch.equal(result[0], frame.X_canon)


def test_snapshot_save_uses_exclusive_create(tmp_path):
    module = import_module()
    target = tmp_path / "snapshot.pt"
    target.write_bytes(b"old")
    writer = module.CaptureWriter(tmp_path, {1})
    with pytest.raises(FileExistsError):
        writer.save(target, {"schema": "x"})
    assert target.read_bytes() == b"old"


def test_asymmetric_inference_return_identity_and_clone_isolation(monkeypatch, tmp_path):
    module = import_module()
    utils = types.ModuleType("mast3r_slam.mast3r_utils")
    returned = (
        torch.tensor([[[1.0]]]),
        torch.tensor([[[2.0]]]),
        torch.tensor([[[3.0]]]),
        torch.tensor([[[4.0]]]),
    )

    def mast3r_asymmetric_inference(model, frame_i, frame_j):
        return returned

    utils.mast3r_asymmetric_inference = mast3r_asymmetric_inference
    pkg = types.ModuleType("mast3r_slam")
    pkg.mast3r_utils = utils
    monkeypatch.setitem(sys.modules, "mast3r_slam", pkg)
    monkeypatch.setitem(sys.modules, "mast3r_slam.mast3r_utils", utils)

    class InferenceTracker(Tracker):
        def track(self, frame, *args, **kwargs):
            import mast3r_slam.mast3r_utils as imported_utils

            self.inference_result = imported_utils.mast3r_asymmetric_inference(
                None, frame, self.keyframes.last_keyframe()
            )
            return False, [], False

    tracker = InferenceTracker()
    with module.capture_mast3r_tracking_inputs(
        InferenceTracker, capture_dir=tmp_path, frame_ids={15}, enabled=True
    ):
        result = tracker.track(Frame(15))
    assert result == (False, [], False)
    assert tracker.inference_result is returned
    payload = torch.load(tmp_path / "mast3r_tracking_frame000015_attempt001.pt", weights_only=True)
    assert payload["mast3r_asymmetric_inference"][0]["frame_i"] == 15
    assert payload["mast3r_asymmetric_inference"][0]["frame_j"] == 9
    returned[2][0, 0, 0] = 99.0
    assert payload["mast3r_asymmetric_inference"][0]["D"][0, 0, 0].item() == 3.0
    assert utils.mast3r_asymmetric_inference is mast3r_asymmetric_inference


def install_fake_mast3r_utils(monkeypatch, match_impl):
    utils = types.ModuleType("mast3r_slam.mast3r_utils")
    matching = types.ModuleType("mast3r_slam.matching")
    utils.config = {"matching": {"dist_thresh": 0.5, "radius": 3}}
    utils.matching = matching
    matching.match = match_impl

    def mast3r_asymmetric_inference(model, frame_i, frame_j):
        base = float(frame_i.frame_id)
        return (
            torch.full((2, 1, 2, 3), base),
            torch.full((2, 1, 2), base + 1),
            torch.full((2, 1, 2, 4), base + 2),
            torch.full((2, 1, 2), base + 3),
        )

    utils.mast3r_asymmetric_inference = mast3r_asymmetric_inference
    pkg = types.ModuleType("mast3r_slam")
    pkg.mast3r_utils = utils
    pkg.matching = matching
    monkeypatch.setitem(sys.modules, "mast3r_slam", pkg)
    monkeypatch.setitem(sys.modules, "mast3r_slam.mast3r_utils", utils)
    monkeypatch.setitem(sys.modules, "mast3r_slam.matching", matching)
    return utils, matching


def test_matching_call_captures_warmstart_raw_valid_identity_and_pre_call_clones(monkeypatch, tmp_path):
    module = import_module()
    returned = (torch.tensor([[1, 0]]), torch.tensor([[[False], [True]]]))

    def match(X11, X21, D11, D21, **kwargs):
        assert kwargs["idx_1_to_2_init"].tolist() == [[1, 0]]
        X11[0, 0, 0, 0] = 999.0
        return returned

    utils, matching = install_fake_mast3r_utils(monkeypatch, match)

    class MatchingTracker(Tracker):
        def track(self, frame, *args, **kwargs):
            import mast3r_slam.mast3r_utils as imported_utils

            X, _C, D, _Q = imported_utils.mast3r_asymmetric_inference(None, frame, self.keyframes.last_keyframe())
            X11, X21, D11, D21 = X[0:1].clone(), X[1:2].clone(), D[0:1], D[1:2]
            self.match_result = imported_utils.matching.match(
                X11, X21, D11, D21,
                idx_1_to_2_init=torch.tensor([[1, 0]]),
                metric_distance_m=0.25,
                metric_scale=2.0,
            )
            assert X11[0, 0, 0, 0].item() == 999.0
            return False, [], False

    tracker = MatchingTracker()
    with module.capture_mast3r_tracking_inputs(
        MatchingTracker, capture_dir=tmp_path, frame_ids={19}, enabled=True
    ):
        tracker.track(Frame(19))
    assert tracker.match_result is returned
    payload = torch.load(tmp_path / "mast3r_tracking_frame000019_attempt001.pt", weights_only=True)
    call = payload["matching_calls"][0]
    assert call["asymmetric_call_index"] == 0
    assert call["idx_1_to_2_init"].tolist() == [[1, 0]]
    assert call["metric_distance_m"] == 0.25
    assert call["metric_scale"] == 2.0
    assert call["matching_config"] == {"dist_thresh": 0.5, "radius": 3}
    assert call["return"]["idx_1_to_2"].tolist() == [[1, 0]]
    assert call["return"]["valid_match"].tolist() == [[[False], [True]]]
    assert call["X11"][0, 0, 0, 0].item() == 19.0
    assert utils.matching.match is match
    assert matching.match is match


def test_matching_nonselected_frame_passthrough_has_no_observation(monkeypatch, tmp_path):
    module = import_module()
    calls = []

    def match(X11, X21, D11, D21, **kwargs):
        calls.append("called")
        return torch.tensor([[0]]), torch.ones(1, 1, 1, dtype=torch.bool)

    install_fake_mast3r_utils(monkeypatch, match)

    class MatchingTracker(Tracker):
        def track(self, frame, *args, **kwargs):
            import mast3r_slam.mast3r_utils as imported_utils

            X, _C, D, _Q = imported_utils.mast3r_asymmetric_inference(None, frame, self.keyframes.last_keyframe())
            imported_utils.matching.match(X[0:1], X[1:2], D[0:1], D[1:2])
            return False, [], False

    with module.capture_mast3r_tracking_inputs(
        MatchingTracker, capture_dir=tmp_path, frame_ids={20}, enabled=True
    ):
        MatchingTracker().track(Frame(21))
    assert calls == ["called"]
    assert sorted(p.name for p in tmp_path.glob("*.pt")) == ["mast3r_tracking_frame000020_missing.pt"]


def test_matching_restored_on_exception_and_error_recorded(monkeypatch, tmp_path):
    module = import_module()

    def match(X11, X21, D11, D21, **kwargs):
        raise RuntimeError("match boom")

    utils, _matching = install_fake_mast3r_utils(monkeypatch, match)

    class MatchingTracker(Tracker):
        def track(self, frame, *args, **kwargs):
            import mast3r_slam.mast3r_utils as imported_utils

            X, _C, D, _Q = imported_utils.mast3r_asymmetric_inference(None, frame, self.keyframes.last_keyframe())
            imported_utils.matching.match(X[0:1], X[1:2], D[0:1], D[1:2])
            return False, [], False

    with pytest.raises(RuntimeError, match="match boom"):
        with module.capture_mast3r_tracking_inputs(
            MatchingTracker, capture_dir=tmp_path, frame_ids={22}, enabled=True
        ):
            MatchingTracker().track(Frame(22))
    assert utils.matching.match is match
    payload = torch.load(tmp_path / "mast3r_tracking_frame000022_attempt001.pt", weights_only=True)
    assert payload["matching_calls"][0]["error"]["type"] == "RuntimeError"
    assert payload["error"]["type"] == "RuntimeError"


def test_multiple_asymmetric_calls_have_distinct_matching_associations(monkeypatch, tmp_path):
    module = import_module()

    def match(X11, X21, D11, D21, **kwargs):
        value = int(X11[0, 0, 0, 0].item())
        return torch.tensor([[value]]), torch.ones(1, 1, 1, dtype=torch.bool)

    install_fake_mast3r_utils(monkeypatch, match)

    class MultiMatchingTracker(Tracker):
        def track(self, frame, *args, **kwargs):
            import mast3r_slam.mast3r_utils as imported_utils

            for offset in (0, 1):
                fake_frame = Frame(frame.frame_id + offset)
                X, _C, D, _Q = imported_utils.mast3r_asymmetric_inference(None, fake_frame, self.keyframes.last_keyframe())
                imported_utils.matching.match(X[0:1], X[1:2], D[0:1], D[1:2])
            return False, [], False

    with module.capture_mast3r_tracking_inputs(
        MultiMatchingTracker, capture_dir=tmp_path, frame_ids={23}, enabled=True
    ):
        MultiMatchingTracker().track(Frame(23))
    payload = torch.load(tmp_path / "mast3r_tracking_frame000023_attempt001.pt", weights_only=True)
    assert [c["matching_call_index"] for c in payload["matching_calls"]] == [0, 1]
    assert [c["asymmetric_call_index"] for c in payload["matching_calls"]] == [0, 1]
    assert [c["return"]["idx_1_to_2"].item() for c in payload["matching_calls"]] == [23, 24]


def test_dataset_prefix_wrapper_delegates_len_attrs_setattr_and_subsample():
    module = import_module()

    class Dataset:
        def __init__(self):
            self.values = [1, 2, 3, 4]
            self.subsample_calls = []

        def __len__(self):
            return len(self.values)

        def __getitem__(self, idx):
            return self.values[idx]

        def subsample(self, value):
            self.subsample_calls.append(value)
            return "SUBSAMPLED"

    dataset = Dataset()
    wrapped = module.PrefixDataset(dataset, 2)
    assert len(wrapped) == 2
    assert wrapped[1] == 2
    assert wrapped.values == [1, 2, 3, 4]
    assert wrapped.subsample(3) == "SUBSAMPLED"
    wrapped.extra = "x"
    assert dataset.extra == "x"


def test_cli_runpy_binds_main_file_and_prefix_wrapper(monkeypatch, tmp_path):
    module = import_module()
    dataloader = types.ModuleType("mast3r_slam.dataloader")
    tracker_mod = types.ModuleType("mast3r_slam.tracker")

    class FrameTracker:
        def track(self):
            raise AssertionError("not used")

    class Dataset:
        marker = "dataset"

        def __len__(self):
            return 5

        def __getitem__(self, index):
            return index

        def subsample(self, value):
            self.subsample_value = value
            return None

    dataloader.load_dataset = lambda path: Dataset()
    tracker_mod.FrameTracker = FrameTracker
    pkg = types.ModuleType("mast3r_slam")
    pkg.dataloader = dataloader
    pkg.tracker = tracker_mod
    monkeypatch.setitem(sys.modules, "mast3r_slam", pkg)
    monkeypatch.setitem(sys.modules, "mast3r_slam.dataloader", dataloader)
    monkeypatch.setitem(sys.modules, "mast3r_slam.tracker", tracker_mod)
    output = tmp_path / "runpy.json"
    original_main = tmp_path / "main.py"
    original_main.write_text(
        """
import json
import os
import sys
from pathlib import Path
from mast3r_slam.dataloader import load_dataset
dataset = load_dataset("dummy")
Path(os.environ["RUNPY_OUT"]).write_text(json.dumps({
    "file": __file__,
    "registered_main_file": sys.modules["__main__"].__file__,
    "argv0": sys.argv[0],
    "len": len(dataset),
    "marker": dataset.marker,
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RUNPY_OUT", str(output))
    rc = module.main([
        "--capture-dir", str(tmp_path / "captures"),
        "--prefix-count", "2",
        "--", str(original_main), "--native-arg",
    ])
    assert rc == 0
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert observed["file"] == str(original_main)
    assert observed["registered_main_file"] == str(original_main)
    assert observed["argv0"] == str(original_main)
    assert observed["len"] == 2
    assert observed["marker"] == "dataset"


def test_cli_inserts_original_main_parent_for_imports(monkeypatch, tmp_path):
    module = import_module()
    tool_root = tmp_path / "tool"
    package = tool_root / "mast3r_slam"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "tracker.py").write_text(
        "class FrameTracker:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "dataloader.py").write_text(
        "class Dataset:\n"
        "    marker = 'loaded-from-tool-root'\n"
        "    def __len__(self): return 3\n"
        "    def subsample(self, value): self.subsample_value = value\n"
        "def load_dataset(path): return Dataset()\n",
        encoding="utf-8",
    )
    output = tmp_path / "out.json"
    original_main = tool_root / "main.py"
    original_main.write_text(
        """
import json
import os
from pathlib import Path
from mast3r_slam.dataloader import load_dataset
dataset = load_dataset("dummy")
Path(os.environ["RUNPY_OUT"]).write_text(json.dumps({
    "marker": dataset.marker,
    "len": len(dataset),
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RUNPY_OUT", str(output))
    monkeypatch.chdir(tmp_path)
    for name in ["mast3r_slam", "mast3r_slam.tracker", "mast3r_slam.dataloader"]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    old_path = list(sys.path)
    try:
        rc = module.main([
            "--capture-dir", str(tmp_path / "captures"),
            "--", str(original_main),
        ])
    finally:
        sys.path[:] = old_path
    assert rc == 0
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert observed == {"marker": "loaded-from-tool-root", "len": 3}
    for name in ["mast3r_slam", "mast3r_slam.tracker", "mast3r_slam.dataloader"]:
        sys.modules.pop(name, None)


def test_cli_runpy_spawn_child_resolves_original_main(tmp_path):
    tool_root = tmp_path / "tool"
    package = tool_root / "mast3r_slam"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "tracker.py").write_text(
        "class FrameTracker:\n"
        "    def track(self, *args, **kwargs): return False, [], False\n"
        "    def get_points_poses(self, *args, **kwargs): return None\n"
        "    def opt_pose_calib_sim3(self, *args, **kwargs): return None\n"
        "    def solve_pose_increment(self, *args, **kwargs): return None\n",
        encoding="utf-8",
    )
    (package / "dataloader.py").write_text(
        "class Dataset:\n"
        "    def __len__(self): return 0\n"
        "    def subsample(self, value): self.subsample_value = value\n"
        "def load_dataset(path): return Dataset()\n",
        encoding="utf-8",
    )
    output = tmp_path / "spawn_child.json"
    original_main = tool_root / "original_main.py"
    original_main.write_text(
        """
import json
import multiprocessing as mp
import sys
from pathlib import Path

def run_backend(output_file):
    Path(output_file).write_text(json.dumps({
        "file": __file__,
        "name": __name__,
    }), encoding="utf-8")

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    process = mp.Process(target=run_backend, args=(sys.argv[1],))
    process.start()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join()
        raise SystemExit("spawn child timeout")
    raise SystemExit(process.exitcode)
""",
        encoding="utf-8",
    )
    capture_dir = tmp_path / "captures"
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE),
            "--capture-dir", str(capture_dir),
            "--frame-id", "0",
            "--", str(original_main), str(output),
        ],
        cwd=other_cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert observed["file"] == str(original_main)
    assert observed["name"] == "__mp_main__"
    summary = json.loads((capture_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "INCOMPLETE"
    assert summary["missing_frame_ids"] == [0]
