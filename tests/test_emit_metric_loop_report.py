import csv
import importlib.util
import json
from pathlib import Path

import pytest


MODULE = (Path(__file__).resolve().parents[1] / ".planning" /
          "metric_window_bundle_20260928" / "emit_metric_loop_report.py")
spec = importlib.util.spec_from_file_location("emit_metric_loop_report", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_one_accepted_bidirectional_metric_link(tmp_path):
    trajectory = tmp_path / "poses.csv"
    with trajectory.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z"])
        writer.writerow(["1.0", 0, 0, 0])
        writer.writerow(["2.0", 1, 0, 0])
    primary = dict(session="session", trajectory=str(trajectory),
                   observation_frame="infrared_left_camera_i",
                   scale_m_per_mast3r_unit=0.5,
                   factory_stereo_calibration={"baseline_m": 0.018083254})
    row = dict(first_raw=0, second_raw=1, accepted=True,
               bidirectional_mean_displacement_camera_i_m=[1.1, 0, 0],
               visual_displacement_camera_i_m=[1, 0, 0],
               forward_reverse_displacement_disagreement_mm=2,
               visual_minus_stereo_relative_rotation_deg=0.2,
               forward=dict(inlier_ratio=0.8, reprojection_p95_px=1.2),
               reverse=dict(inlier_ratio=0.6, reprojection_p95_px=1.4))
    probe = dict(schema="saved_metric_loop_displacement_check_v1",
                 external_reference_used=False, trajectory=str(trajectory),
                 metric_scale_m_per_mast3r_unit=0.5, results=[row])
    primary_path, probe_path = tmp_path / "primary.json", tmp_path / "probe.json"
    primary_path.write_text(json.dumps(primary))
    probe_path.write_text(json.dumps(probe))
    report = module.make_report(primary, probe, 0, 1, primary_path, probe_path)
    observation = report["observations"][0]
    assert observation["scale"] == pytest.approx(0.55)
    assert observation["metric_displacement_camera_i_m"] == [1.1, 0, 0]
    assert observation["pnp_inlier_ratio"] == 0.6
    assert observation["bidirectional_relative_disagreement"] == pytest.approx(0.002 / 1.1)
    assert report["external_ground_truth_used"] is False
    assert report["schema"] == "umi_mast3r_stereo_scale_v2"
    assert report["diagnostic_only"] is True
    probe["external_reference_used"] = True
    with pytest.raises(ValueError, match="external reference"):
        module.make_report(primary, probe, 0, 1, primary_path, probe_path)
