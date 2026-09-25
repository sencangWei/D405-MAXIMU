import csv
import importlib.util
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_mast3r_vins_camera_priors.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location(SCRIPT.stem, SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_camera_pose_priors_convert_body_lever_arm_and_do_not_extrapolate(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    with (dataset / "frames.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("input_index", "t_sec"))
        writer.writerows(enumerate((0.0, 1.5, 2.5, 4.0)))

    body_t_camera = np.eye(4)
    body_t_camera[0, 3] = 0.1
    monkeypatch.setattr(
        module,
        "load_vins_config",
        lambda _path, expected_td_s: {"body_T_camera": body_t_camera},
    )
    monkeypatch.setattr(
        module,
        "load_trajectory",
        lambda _path: (
            np.array([1.0, 2.0, 3.0]),
            np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
            Rotation.identity(3),
            [],
        ),
    )
    output = tmp_path / "priors.csv"
    assert module.generate(dataset, tmp_path / "vio.csv", tmp_path / "config.yaml", output) == 2
    rows = list(csv.DictReader(output.open(newline="")))
    assert [row["valid"] for row in rows] == ["0", "1", "1", "0"]
    assert float(rows[1]["x"]) == 0.6
    assert float(rows[2]["x"]) == 1.6
