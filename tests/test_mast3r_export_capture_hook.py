import importlib.util
import sys
import types
from pathlib import Path

import pytest
import torch
import numpy as np


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "export_capture_hook/sitecustomize.py"
)


class Pose:
    def __init__(self, data):
        self.data = data


class Frame:
    def __init__(self, frame_id, x=0.0, scale=1.0):
        data = torch.zeros(1, 8)
        data[0, 0] = x
        data[0, 6] = 1.0
        data[0, 7] = scale
        self.frame_id = frame_id
        self.T_WC = Pose(data)


def import_hook(monkeypatch, tmp_path, *, enabled=True):
    eval_mod = types.ModuleType("mast3r_slam.evaluate")

    def save_full_traj(logdir, logfile, timestamps, frames, tracked_poses):
        path = Path(logdir) / logfile
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("native export\n", encoding="utf-8")
        return "ORIGINAL_RESULT"

    eval_mod.save_full_traj = save_full_traj
    pkg = types.ModuleType("mast3r_slam")
    pkg.evaluate = eval_mod
    if enabled:
        monkeypatch.setitem(sys.modules, "mast3r_slam", pkg)
        monkeypatch.setitem(sys.modules, "mast3r_slam.evaluate", eval_mod)
        monkeypatch.setenv("MAST3R_EXPORT_SNAPSHOT_PATH", str(tmp_path / "export.pt"))
    else:
        monkeypatch.delenv("MAST3R_EXPORT_SNAPSHOT_PATH", raising=False)
    spec = importlib.util.spec_from_file_location(f"export_hook_{id(tmp_path)}", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return eval_mod


def test_default_off_does_not_import_evaluate(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "mast3r_slam.evaluate", raising=False)
    import_hook(monkeypatch, tmp_path, enabled=False)
    assert "mast3r_slam.evaluate" not in sys.modules


def test_writer_result_unchanged_and_snapshot_cpu_clones(monkeypatch, tmp_path):
    eval_mod = import_hook(monkeypatch, tmp_path)
    frames = [Frame(10), Frame(20, x=1.0)]
    relative = torch.zeros(1, 8)
    relative[0, 6] = 1.0
    relative[0, 7] = 1.0
    result = eval_mod.save_full_traj(
        tmp_path, "traj.txt", [0.0] * 31, frames, [(20, 0, relative)]
    )
    assert result == "ORIGINAL_RESULT"
    assert (tmp_path / "traj.txt").read_text(encoding="utf-8") == "native export\n"
    snapshot = torch.load(tmp_path / "export.pt", weights_only=True)
    assert snapshot["schema"] == "mast3r_native_full_export_snapshot_v1"
    assert snapshot["original_export_sha256"]
    assert snapshot["keyframes"][0]["frame_id"] == 10
    assert snapshot["tracked_poses"][0]["anchor_frame_id"] == 10
    assert snapshot["tracked_poses"][0]["timestamp_text"] == "0.0"
    relative[0, 0] = 99.0
    assert snapshot["tracked_poses"][0]["relative_pose_data"][0, 0].item() == 0.0


def test_previous_anchor_index_is_preserved(monkeypatch, tmp_path):
    eval_mod = import_hook(monkeypatch, tmp_path)
    relative = torch.zeros(1, 8)
    relative[0, 6] = 1.0
    relative[0, 7] = 1.0
    eval_mod.save_full_traj(
        tmp_path, "traj.txt", [0.0] * 31, [Frame(10), Frame(20)], [(20, 0, relative)]
    )
    snapshot = torch.load(tmp_path / "export.pt", weights_only=True)
    assert snapshot["tracked_poses"][0]["anchor_idx"] == 0
    assert snapshot["tracked_poses"][0]["anchor_frame_id"] == 10


def test_timestamp_text_matches_native_fstring_not_numpy_str(monkeypatch, tmp_path):
    eval_mod = import_hook(monkeypatch, tmp_path)
    stamp = np.float32(1 / 30)
    relative = torch.zeros(1, 8)
    relative[0, 6:8] = 1.0
    eval_mod.save_full_traj(tmp_path, "traj.txt", [stamp], [Frame(0)], [(0, 0, relative)])
    snapshot = torch.load(tmp_path / "export.pt", weights_only=True)
    assert snapshot["tracked_poses"][0]["timestamp_text"] == f"{stamp}"
    assert snapshot["tracked_poses"][0]["timestamp_text"] != str(stamp)


def test_no_overwrite(monkeypatch, tmp_path):
    (tmp_path / "export.pt").write_bytes(b"old")
    eval_mod = import_hook(monkeypatch, tmp_path)
    relative = torch.zeros(1, 8)
    relative[0, 6] = 1.0
    relative[0, 7] = 1.0
    with pytest.raises(FileExistsError):
        eval_mod.save_full_traj(tmp_path, "traj.txt", [0.0], [Frame(0)], [(0, 0, relative)])
    assert (tmp_path / "export.pt").read_bytes() == b"old"


def test_invalid_scale_rejected_after_native_writer(monkeypatch, tmp_path):
    eval_mod = import_hook(monkeypatch, tmp_path)
    relative = torch.zeros(1, 8)
    relative[0, 6] = 1.0
    relative[0, 7] = 1.0
    with pytest.raises(ValueError, match="scale"):
        eval_mod.save_full_traj(tmp_path, "traj.txt", [0.0], [Frame(0, scale=0.0)], [(0, 0, relative)])
    assert (tmp_path / "traj.txt").exists()


def test_malformed_sim3_shape_is_rejected(monkeypatch, tmp_path):
    eval_mod = import_hook(monkeypatch, tmp_path)
    malformed = torch.zeros(2, 4)
    with pytest.raises(ValueError, match="shape"):
        eval_mod.save_full_traj(tmp_path, "traj.txt", [0.0], [Frame(0)], [(0, 0, malformed)])
    assert (tmp_path / "traj.txt").exists()


def test_duplicate_tracked_frame_ids_are_rejected(monkeypatch, tmp_path):
    eval_mod = import_hook(monkeypatch, tmp_path)
    relative = torch.zeros(1, 8)
    relative[0, 6] = 1.0
    relative[0, 7] = 1.0
    with pytest.raises(ValueError, match="duplicate tracked"):
        eval_mod.save_full_traj(
            tmp_path, "traj.txt", [0.0], [Frame(0)],
            [(0, 0, relative), (0, 0, relative)],
        )


def test_nonfinite_timestamp_is_rejected(monkeypatch, tmp_path):
    eval_mod = import_hook(monkeypatch, tmp_path)
    relative = torch.zeros(1, 8)
    relative[0, 6] = 1.0
    relative[0, 7] = 1.0
    with pytest.raises(ValueError, match="timestamp"):
        eval_mod.save_full_traj(tmp_path, "traj.txt", [float("nan")], [Frame(0)], [(0, 0, relative)])
