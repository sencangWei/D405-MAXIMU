import numpy as np
import rerun.blueprint as rrb

from ego_vio.visualizer.rerun_viz import RerunVisualizer


class _FakeRerun:
    def __init__(self):
        self.blueprints = []

    def send_blueprint(self, blueprint, **kwargs):
        self.blueprints.append((blueprint, kwargs))


class _FakeRerun022:
    def __init__(self):
        self.times = []

    def set_time_seconds(self, timeline, timestamp_s):
        self.times.append((timeline, timestamp_s))

    @staticmethod
    def Scalar(value):
        return ("scalar", value)


def test_blueprint_works_without_programmatic_eye_pose_fields():
    viz = RerunVisualizer.__new__(RerunVisualizer)
    viz._rrb = rrb
    viz.rr = _FakeRerun()
    viz.unit_names = ["left_hand"]
    viz._supports_eye_pose = False

    viz._send_blueprint(np.zeros(3), 0.2, make_default=True)

    assert len(viz.rr.blueprints) == 1
    assert viz.rr.blueprints[0][1] == {
        "make_active": True,
        "make_default": True,
    }


def test_rich_visualizer_uses_rerun_022_time_api():
    viz = RerunVisualizer.__new__(RerunVisualizer)
    viz.rr = _FakeRerun022()
    viz._epoch_offset = 0.0

    viz._set_time(1234.5)

    assert viz.rr.times == [("time", 1234.5)]


def test_rich_visualizer_uses_rerun_022_scalar_archetype():
    viz = RerunVisualizer.__new__(RerunVisualizer)
    viz.rr = _FakeRerun022()

    assert viz._make_scalar(3.25) == ("scalar", 3.25)
