import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


PATH = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928/keyframe_update_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("keyframe_update_diagnostics", PATH)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def fixture():
    n = 40
    old = np.column_stack((np.linspace(-.1, .1, n), np.zeros(n), np.linspace(.3, .6, n))).astype(np.float32)
    working = old.copy()
    proposal = 1.1*working
    cold, cin = np.full((n, 1), 3, np.float32), np.ones((n, 1), np.float32)
    return dict(keyframe_pixel_ids=np.arange(n), raw_X_old=old, raw_X_proposal=proposal,
                raw_X_after=(cold*old+cin*proposal)/(cold+cin), raw_Xkf_match=working.copy(),
                raw_Xkf_working=working, raw_C_old=cold, raw_C_new=cin, raw_C_after=cold+cin,
                raw_Ckf_match=cin.copy(), raw_Ckf_working=cin.copy(), update_N_before=np.array(3),
                update_N_after=np.array(4), update_filtering_mode=np.array("weighted_pointmap"),
                update_T_boundary=np.array([0, 0, 0, 0, 0, 0, 1, 1.1], dtype=np.float32),
                K=np.array([[260, 0, 255], [0, 260, 142], [0, 0, 1]]),
                pixel_keyframe=old[:, :2]/old[:, 2:3]*260+np.array([255, 142]),
                depth_keyframe_m=old[:, 2].copy(), valid=np.ones(n, bool))


def test_native_weighted_formula_and_incoming_fraction():
    result = check.summarize_update(fixture())
    assert result["weighted_formula_consistent"]
    assert result["derived_pose_binds_actual_proposal"]
    assert result["confidence_incoming_fraction"]["median"] == .25
    assert result["stereo_consistency"]["proposal_centered_log_depth_shape"]["max"] < 1e-6


def test_wrong_update_rejected_by_formula_without_relabeling_trajectory_error():
    sample = fixture()
    sample["raw_X_after"][4, 0] += .001
    result = check.summarize_update(sample)
    assert not result["weighted_formula_consistent"]
    assert result["weighted_xyz_formula_error_learned_units"]["max"] > .0009


def test_derived_pose_not_assumed_to_bind_proposal():
    sample = fixture()
    sample["update_T_boundary"][0] = .1
    assert not check.summarize_update(sample)["derived_pose_binds_actual_proposal"]


def test_noncommuting_rotation_translation_sim3_and_matching_proposal():
    sample = fixture()
    rotation = Rotation.from_euler("xyz", [20, -5, 30], degrees=True)
    sample["update_T_boundary"] = np.r_[.01, -.02, .03, rotation.as_quat(), 1.1].astype(np.float32)
    sample["raw_X_proposal"] = (1.1*rotation.apply(sample["raw_Xkf_working"])+[.01, -.02, .03]).astype(np.float32)
    sample["raw_X_after"] = (sample["raw_C_old"]*sample["raw_X_old"]+sample["raw_C_new"]*sample["raw_X_proposal"])/sample["raw_C_after"]
    assert check.summarize_update(sample)["derived_pose_binds_actual_proposal"]


def test_common_metric_innovation_invariant_to_uniform_learned_gauge():
    sample = fixture()
    first = check.summarize_update(sample)
    for key in ("raw_X_old", "raw_X_proposal", "raw_X_after", "raw_Xkf_match", "raw_Xkf_working"):
        sample[key] *= 7
    second = check.summarize_update(sample)
    key = "same_old_units_proposal_innovation_norm_mm"
    assert first["stereo_consistency"][key]["median"] == pytest.approx(second["stereo_consistency"][key]["median"], rel=1e-5)


def test_missing_stereo_is_unknown_and_all_native_rays_retained():
    sample = fixture()
    sample["depth_keyframe_m"][:] = np.nan
    result = check.summarize_update(sample)
    assert result["count"] == 40 and result["weighted_formula_consistent"]
    assert result["stereo_consistency"]["status"] == "UNKNOWN"


def test_raw_projection_disagreement_not_hidden_by_optimizer_ray_reconstraint():
    sample = fixture()
    sample["raw_X_proposal"][:, 0] += .01
    sample["update_T_boundary"][0] = .01
    sample["raw_X_after"] = (sample["raw_C_old"]*sample["raw_X_old"]+sample["raw_C_new"]*sample["raw_X_proposal"])/sample["raw_C_after"]
    result = check.summarize_update(sample)
    assert result["raw_keyframe_ray_pixel_disagreement"]["old"]["max"] < 1e-4
    assert result["raw_keyframe_ray_pixel_disagreement"]["proposal"]["median"] > 5
    assert result["raw_keyframe_ray_pixel_disagreement"]["after"]["median"] > 1


@pytest.mark.parametrize("fault", ["mode", "shape", "mask", "counter", "confidence", "duplicate", "nonfinite"])
def test_malformed_capture_fails_closed(fault):
    sample = fixture()
    if fault == "mode": sample["update_filtering_mode"] = np.array("recent")
    elif fault == "shape": sample["raw_C_old"] = sample["raw_C_old"].ravel()
    elif fault == "mask": sample["valid"] = np.ones(40)
    elif fault == "counter": sample["update_N_after"] = np.array(5)
    elif fault == "confidence": sample["raw_C_new"][0] = 0
    elif fault == "duplicate": sample["keyframe_pixel_ids"][0] = 1
    else: sample["raw_X_old"][0, 0] = np.nan
    with pytest.raises(ValueError): check.summarize_update(sample)
