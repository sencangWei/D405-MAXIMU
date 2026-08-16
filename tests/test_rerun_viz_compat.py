import numpy as np
import rerun.blueprint as rrb

from ego_vio.visualizer.rerun_viz import RerunVisualizer


class _FakeRerun:
    def __init__(self):
        self.blueprints = []

    def send_blueprint(self, blueprint, **kwargs):
        self.blueprints.append((blueprint, kwargs))


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
