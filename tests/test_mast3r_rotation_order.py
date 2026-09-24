import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_mast3r_d405_temporal_finetune import compose_camera_rotation


def test_noncommuting_imu_increments_preserve_pose_chain():
    first = Rotation.from_euler("x", 30.0, degrees=True)
    second = Rotation.from_euler("y", 20.0, degrees=True)
    priors = [
        {"qx": "0", "qy": "0", "qz": "0", "qw": "1"},
        dict(zip(("qx", "qy", "qz", "qw"), map(str, first.as_quat()))),
        dict(zip(("qx", "qy", "qz", "qw"), map(str, second.as_quat()))),
    ]
    whole = compose_camera_rotation(priors, 0, 2)
    first_leg = compose_camera_rotation(priors, 0, 1)
    second_leg = compose_camera_rotation(priors, 1, 2)
    assert np.degrees((whole.inv() * (first * second)).magnitude()) < 1e-9
    assert np.degrees((whole.inv() * (first_leg * second_leg)).magnitude()) < 1e-9
