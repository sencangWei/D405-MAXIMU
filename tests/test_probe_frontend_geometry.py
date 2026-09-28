import csv
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


PATH = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928/probe_frontend_geometry.py"
SPEC = importlib.util.spec_from_file_location("probe_frontend_geometry", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def write_priors(path, quats):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("input_index", "qx", "qy", "qz", "qw"))
        for i, quat in enumerate(quats):
            writer.writerow((i, *quat))


def test_priors_right_compose_noncommuting_camera_increments(tmp_path):
    steps = [Rotation.identity(), Rotation.from_euler("x", 40, degrees=True),
             Rotation.from_euler("z", 30, degrees=True)]
    path = tmp_path/"priors.csv"
    write_priors(path, [r.as_quat() for r in steps])
    actual = probe.integrated_priors(path, 3)
    correct = steps[1]*steps[2]
    assert (Rotation.from_quat(actual[-1]).inv()*correct).magnitude() < 1e-12
    wrong = steps[2]*steps[1]
    assert (Rotation.from_quat(actual[-1]).inv()*wrong).magnitude() > .1


@pytest.mark.parametrize("fault", ["missing", "norm", "first"])
def test_prior_bad_coverage_quaternion_and_first_delta_rejected(tmp_path, fault):
    quats = [Rotation.identity().as_quat(), Rotation.identity().as_quat()]
    if fault == "norm":
        quats[1] = np.array([0, 0, 0, 2])
    if fault == "first":
        quats[0] = Rotation.from_euler("x", 10, degrees=True).as_quat()
    path = tmp_path/"priors.csv"
    write_priors(path, quats)
    with pytest.raises(ValueError):
        probe.integrated_priors(path, 3 if fault == "missing" else 2)


def test_sampling_preserves_order_and_is_deterministic():
    mask = np.arange(3000) % 3 != 0
    a = probe.sample_indices(mask, 128)
    np.testing.assert_array_equal(a, probe.sample_indices(mask, 128))
    assert len(a) == 128 and np.all(mask[a]) and np.all(np.diff(a) > 0)
    assert a[0] == np.flatnonzero(mask)[0] and a[-1] == np.flatnonzero(mask)[-1]
    assert len(probe.sample_indices(np.zeros(10, bool))) == 0
    with pytest.raises(ValueError):
        probe.sample_indices(np.ones(10))


def test_hooks_return_original_objects_and_restore_even_when_capture_fails():
    marker, result = object(), (True, object(), False)

    class Tracker:
        keyframes = SimpleNamespace(last_keyframe=lambda: SimpleNamespace(frame_id=999))
        def track(self, frame, *args, **kwargs):
            assert self.opt_pose_calib_sim3("arg", flag=1) is marker
            return result
        def opt_pose_calib_sim3(self, *args, **kwargs):
            assert args == ("arg",) and kwargs == {"flag": 1}
            return marker

    class Recorder:
        context, captured = None, None
        def __init__(self):
            self.errors = []
        def prepare_capture(self, tracker, args):
            raise ValueError("diagnostic capture failure")
        def finish_track(self, tracker, frame, outcome):
            assert outcome is result

    recorder = Recorder()
    orig_track, orig_opt = Tracker.track, Tracker.opt_pose_calib_sim3
    restore = probe.install_hooks(Tracker, recorder)
    try:
        assert Tracker().track(SimpleNamespace(frame_id=1000)) is result
        assert len(recorder.errors) == 1
        assert recorder.errors[0]["stage"] == "prepare"
        assert recorder.context is None
        assert Tracker().track(SimpleNamespace(frame_id=100)) is result
        assert len(recorder.errors) == 1
    finally:
        restore()
    assert Tracker.track is orig_track and Tracker.opt_pose_calib_sim3 is orig_opt


def test_fullrate_identity_requires_timestamps_and_reports_changed_pose(tmp_path):
    frozen, output = tmp_path/"frozen", tmp_path/"output"
    frozen.mkdir(); output.mkdir()
    text = "t_sec,x,y,z,qw,qx,qy,qz\n0,0,0,0,1,0,0,0\n1,1,0,0,1,0,0,0\n"
    for name in ("trajectory_frames.csv", "trajectory_online_frames.csv"):
        (frozen/name).write_text(text)
        (output/name).write_text(text)
    info = probe.compare_trajectories(frozen, output)
    assert all(row["byte_identical"] for row in info.values())
    path = output/"trajectory_frames.csv"
    path.write_text(text.replace("1,1,0", "1,2,0"))
    assert probe.compare_trajectories(frozen, output)[path.name]["array_identical"] is False
    path.write_text(text.replace("1,1,0", "2,1,0"))
    with pytest.raises(ValueError, match="timestamp"):
        probe.compare_trajectories(frozen, output)
