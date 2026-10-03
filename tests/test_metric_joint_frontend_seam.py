"""Run with the installed MASt3R toolchain Python; no live hardware."""
from pathlib import Path
import os
import sys
from types import SimpleNamespace

import pytest
import torch

TOOL = Path(os.environ.get("MAST3R_SLAM_DIR", "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM"))
sys.path.insert(0, str(TOOL))
import lietorch  # noqa: E402
from mast3r_slam import global_opt  # noqa: E402


class Frames(list):
    def __init__(self):
        poses = torch.zeros((2, 8))
        poses[:, 6:] = 1
        super().__init__([
            SimpleNamespace(frame_id=fid, img=torch.zeros((1, 3, 2, 2)),
                            X_canon=torch.ones((4, 3)), T_WC=lietorch.Sim3(poses[i:i+1]),
                            get_average_conf=lambda: torch.ones((4, 1)))
            for i, fid in enumerate((0, 7))
        ])
        self.updates = []

    def update_T_WCs(self, poses, indices):
        self.updates.append((poses.data.clone(), indices.clone()))


def graph(monkeypatch, tracking=None, pin=1, K=True):
    local = dict(window_size=10, pin=pin, C_conf=.5, Q_conf=.5,
                 pixel_border=0, depth_eps=.001, max_iters=2,
                 sigma_pixel=1., sigma_depth=.1, delta_norm=.001)
    monkeypatch.setitem(global_opt.config, "local_opt", local)
    monkeypatch.setitem(global_opt.config, "tracking", tracking or {})
    frames = Frames()
    value = global_opt.FactorGraph(None, frames, K=torch.eye(3) if K else None, device="cpu")
    value.ii, value.jj = torch.tensor([0]), torch.tensor([1])
    value.idx_ii2jj = value.idx_jj2ii = torch.arange(4).reshape(1, 4)
    value.valid_match_i = value.valid_match_j = torch.ones((1, 4), dtype=torch.bool)
    value.Q_ii2jj = value.Q_jj2ii = torch.ones((1, 4))
    monkeypatch.setattr(global_opt, "constrain_points_to_ray", lambda size, points, K: points)
    monkeypatch.delenv("MAST3R_GRAPH_SNAPSHOT_FRAME", raising=False)
    return value, frames


def test_default_off_uses_original_native_call_without_importing_adapter(monkeypatch):
    def unexpected_import(*_):
        raise AssertionError("disabled candidate imported adapter")

    monkeypatch.setattr(global_opt.importlib.util, "spec_from_file_location", unexpected_import)
    calls = []

    def native(*args):
        assert len(args) == 19
        calls.append(args[0].clone())
        args[0][1, 0] = 4

    monkeypatch.setattr(global_opt.mast3r_slam_backends, "gauss_newton_calib", native)
    value, frames = graph(monkeypatch)
    assert value.metric_relative_joint_solver is None
    value.solve_GN_calib()
    assert len(calls) == 1
    assert frames.updates[0][0][0, 0, 0] == 4
    assert frames.updates[0][1].tolist() == [1]
    assert frames[0].T_WC.data[0, 0] == 0


@pytest.mark.parametrize("tracking,pin,K", [
    ({"metric_relative_joint": True}, 0, True),
    ({"metric_relative_joint": True}, 1, False),
    ({"metric_relative_joint": True, "stereo_fix_pose_scale": True}, 1, True),
    ({"metric_relative_joint": True, "vins_backend_position_sigma_m": .01}, 1, True),
])
def test_candidate_rejects_conflicting_objective_or_gauge(monkeypatch, tracking, pin, K):
    monkeypatch.setenv("MAST3R_VINS_CAMERA_POSES", "/not-read.csv")
    monkeypatch.setattr(global_opt, "MetricKeyframePrior", lambda path: object())
    with pytest.raises(ValueError):
        graph(monkeypatch, tracking, pin, K)


@pytest.mark.parametrize("env", [None, "relative.py", "/does/not/exist.py"])
def test_enabled_candidate_requires_explicit_absolute_existing_adapter(monkeypatch, env):
    name = "MAST3R_METRIC_RELATIVE_JOINT_ADAPTER"
    if env is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, env)
    with pytest.raises(ValueError, match="explicit absolute adapter"):
        graph(monkeypatch, {"metric_relative_joint": True})


def test_callback_copies_cpu_result_into_pose_view_and_shared_keyframe_update(monkeypatch, tmp_path):
    path = tmp_path / "adapter.py"
    path.write_text(
        "def solve_calibrated(frame_ids, args):\n"
        "    assert frame_ids == [0, 7]\n"
        "    result = args[0].clone()\n"
        "    result[1, 0] = 9\n"
        "    return result\n"
    )
    monkeypatch.setenv("MAST3R_METRIC_RELATIVE_JOINT_ADAPTER", str(path))
    monkeypatch.setattr(global_opt.mast3r_slam_backends, "gauss_newton_calib", lambda *_: pytest.fail("candidate fell back to native"))
    value, frames = graph(monkeypatch, {"metric_relative_joint": True})
    value.solve_GN_calib()
    assert len(frames.updates) == 1
    assert frames.updates[0][0][0, 0, 0] == 9
    assert frames.updates[0][1].tolist() == [1]
    assert frames[0].T_WC.data[0, 0] == 0


@pytest.mark.parametrize("bad_output", ["shape", "nan"])
def test_invalid_callback_result_never_updates_keyframes(monkeypatch, bad_output):
    value, frames = graph(monkeypatch)
    value.metric_relative_joint_solver = (
        lambda ids, args: args[0][:1]
        if bad_output == "shape" else torch.full_like(args[0], float("nan"))
    )
    with pytest.raises(ValueError, match="invalid poses"):
        value.solve_GN_calib()
    assert not frames.updates
