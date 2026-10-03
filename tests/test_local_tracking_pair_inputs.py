import importlib.util
from pathlib import Path

import pytest
import torch


BASE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
MODULE = BASE / "local_tracking_pair_inputs.py"


def import_module():
    spec = importlib.util.spec_from_file_location(f"local_tracking_pair_inputs_{id(object())}", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload():
    idx = torch.tensor([[2, 0, 3, 1]], dtype=torch.int64)
    raw_valid = torch.tensor([[[True], [True], [True], [False]]])
    q = torch.tensor([
        [[1.0, 4.0], [9.0, 16.0]],
        [[25.0, 36.0], [49.0, 64.0]],
    ])
    current_conf = torch.tensor([[0.2], [0.9], [0.7], [1.1]])
    reference_conf = torch.tensor([[0.4], [0.4], [1.2], [0.95]])
    flat_x = torch.arange(12, dtype=torch.float32).reshape(4, 3)
    xk = flat_x + 100
    cf = current_conf[idx[0]]
    ck = reference_conf
    qk = torch.sqrt(q[0].reshape(-1, 1)[idx[0]] * q[1].reshape(-1, 1))
    expected_valid = raw_valid[0] & (cf > 0.5) & (ck > 0.6) & (qk > 5.0)
    return {
        "schema": "mast3r_tracking_input_capture_v1",
        "status": "ok",
        "track_entry": {
            "frame_id": 10,
            "reference_frame_id": 7,
            "cfg": {"C_conf": 0.5, "Q_conf": 5.0, "stereo_pointmap_scale_prior": False},
        },
        "mast3r_asymmetric_inference": [
            {
                "frame_i": 10,
                "frame_j": 7,
                "X": torch.stack((flat_x.reshape(2, 2, 3), xk.reshape(2, 2, 3))),
                "D": torch.stack((flat_x.reshape(2, 2, 3) + 200, xk.reshape(2, 2, 3) + 200)),
                "Q": q,
            }
        ],
        "matching_calls": [
            {
                "asymmetric_call_index": 0,
                "X11": flat_x.reshape(1, 2, 2, 3),
                "X21": xk.reshape(1, 2, 2, 3),
                "D11": flat_x.reshape(1, 2, 2, 3) + 200,
                "D21": xk.reshape(1, 2, 2, 3) + 200,
                "idx_1_to_2_init": torch.tensor([[3, 2, 1, 0]], dtype=torch.int64),
                "return": {"idx_1_to_2": idx, "valid_match": raw_valid},
            }
        ],
        "get_points_poses": {
            "frame_id": 10,
            "keyframe_id": 7,
            "idx_f2k": idx[0],
            "img_size": (2, 2),
            "K": torch.eye(3),
            "current": {"confidence": current_conf},
            "reference": {"confidence": reference_conf},
            "return": (
                flat_x[idx[0]],
                xk,
                "T_WCf",
                "T_WCk",
                cf,
                ck,
                torch.zeros(4, 3),
                torch.ones(4, 1, dtype=torch.bool),
            ),
        },
        "opt_pose_calib_sim3": {
            "Xf": flat_x[idx[0]],
            "Xk": xk,
            "Qk": qk,
            "valid": expected_valid,
            "K": torch.eye(3),
            "img_size": (2, 2),
            "return": {
                "T_WCf": {"data": torch.tensor([[0., 0., 0., 0., 0., 0., 1., 1.]])},
                "T_CkCf": {"data": torch.tensor([[0., 0., 0., 0., 0., 0., 1., 1.]])},
            },
        },
    }


def test_selects_nonidentity_forward_pair_and_computes_raw_c_q_validity():
    module = import_module()
    data = payload()
    result = module.select_tracking_match(data)
    assert result["current_frame_id"] == 10
    assert result["keyframe_id"] == 7
    assert result["forward_index"].tolist() == [2, 0, 3, 1]
    assert result["raw_forward_valid"].tolist() == [[True], [True], [True], [False]]
    assert result["tracking_valid"].tolist() == [[False], [False], [True], [False]]
    assert torch.equal(result["forward_Q"], torch.tensor([[15.0], [6.0], [28.0], [16.0]]))
    result["forward_index"][0] = 99
    assert data["matching_calls"][0]["return"]["idx_1_to_2"][0, 0].item() == 2


def test_native_q_is_preserved_after_bounded_cpu_formula_check():
    module = import_module()
    data = payload()
    native_q = torch.nextafter(data["opt_pose_calib_sim3"]["Qk"], torch.full((4, 1), float("inf")))
    data["opt_pose_calib_sim3"]["Qk"] = native_q
    result = module.select_tracking_match(data)
    assert torch.equal(result["forward_Q"], native_q)
    assert result["Q_recompute_max_abs_diff"] > 0


def test_rejects_native_q_formula_difference_above_two_ulp():
    module = import_module()
    data = payload()
    native_q = data["opt_pose_calib_sim3"]["Qk"].clone()
    for _ in range(3):
        native_q = torch.nextafter(native_q, torch.full_like(native_q, float("inf")))
    data["opt_pose_calib_sim3"]["Qk"] = native_q
    with pytest.raises(ValueError, match="beyond two ULP"):
        module.select_tracking_match(data)


def test_native_q_is_used_for_the_unchanged_tracking_threshold():
    module = import_module()
    data = payload()
    data["track_entry"]["cfg"]["C_conf"] = 0.0
    data["track_entry"]["cfg"]["Q_conf"] = 15.0
    native_q = data["opt_pose_calib_sim3"]["Qk"].clone()
    native_q[0] = torch.nextafter(native_q[0], torch.full((1,), float("inf")))
    data["opt_pose_calib_sim3"]["Qk"] = native_q
    data["opt_pose_calib_sim3"]["valid"] = data["matching_calls"][0]["return"]["valid_match"][0] & (native_q > 15.0)
    result = module.select_tracking_match(data)
    assert bool(result["tracking_valid"][0]) is True


@pytest.mark.parametrize("change", ["error", "missing_return", "nan_threshold", "broadcast_confidence"])
def test_rejects_incomplete_opt_or_non_native_broadcasts(change):
    module = import_module()
    data = payload()
    if change == "error":
        data["opt_pose_calib_sim3"]["error"] = {"type": "RuntimeError"}
    elif change == "missing_return":
        data["opt_pose_calib_sim3"].pop("return")
    elif change == "nan_threshold":
        data["track_entry"]["cfg"]["Q_conf"] = float("nan")
        data["opt_pose_calib_sim3"]["valid"].fill_(False)
    else:
        gp = data["get_points_poses"]
        gp["current"]["confidence"] = gp["current"]["confidence"].flatten()
        gp["reference"]["confidence"] = gp["reference"]["confidence"].flatten()
        entries = list(gp["return"])
        entries[4] = gp["current"]["confidence"][gp["idx_f2k"]]
        entries[5] = gp["reference"]["confidence"]
        gp["return"] = tuple(entries)
        data["opt_pose_calib_sim3"]["valid"] = (
            data["matching_calls"][0]["return"]["valid_match"][0]
            & (entries[4] > 0.5) & (entries[5] > 0.5)
            & (data["opt_pose_calib_sim3"]["Qk"] > 5.0)
        )
    with pytest.raises(ValueError):
        module.select_tracking_match(data)


def test_rejects_wrong_reference_or_association():
    module = import_module()
    bad = payload()
    bad["mast3r_asymmetric_inference"][0]["frame_j"] = 8
    with pytest.raises(ValueError, match="unique selected"):
        module.select_tracking_match(bad)
    bad = payload()
    bad["matching_calls"][0]["asymmetric_call_index"] = 3
    with pytest.raises(ValueError, match="association"):
        module.select_tracking_match(bad)


def test_rejects_duplicate_candidate_ambiguity():
    module = import_module()
    bad = payload()
    bad["matching_calls"].append(dict(bad["matching_calls"][0]))
    with pytest.raises(ValueError, match="ambiguous"):
        module.select_tracking_match(bad)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p: p.update(status="error"), "status"),
        (lambda p: p["matching_calls"][0]["return"]["idx_1_to_2"].to(torch.int32), "int64"),
        (lambda p: p["matching_calls"][0]["return"].update(idx_1_to_2=torch.tensor([[4, 0, 1, 2]])), "range"),
        (lambda p: p["matching_calls"][0]["return"].update(valid_match=torch.ones(1, 4, dtype=torch.bool)), "valid_match"),
        (lambda p: p["mast3r_asymmetric_inference"][0]["Q"].__setitem__((0, 0, 0), float("nan")), "Q"),
        (lambda p: p["opt_pose_calib_sim3"].update(Qk=torch.ones(4, 1)), "Qk"),
        (lambda p: p["opt_pose_calib_sim3"].update(valid=torch.ones(4, 1, dtype=torch.bool)), "valid"),
        (lambda p: p["track_entry"]["cfg"].update(stereo_pointmap_scale_prior=True), "stereo_pointmap_scale_prior"),
        (lambda p: p["track_entry"].update(cfg={"Q_conf": 5.0}), "C_conf"),
    ],
)
def test_rejects_malformed_or_changed_fields(mutate, match):
    module = import_module()
    bad = payload()
    replacement = mutate(bad)
    if replacement is not None:
        bad["matching_calls"][0]["return"]["idx_1_to_2"] = replacement
    with pytest.raises(ValueError, match=match):
        module.select_tracking_match(bad)


def test_rejects_img_size_and_get_points_mismatch():
    module = import_module()
    bad = payload()
    bad["get_points_poses"]["idx_f2k"] = torch.tensor([2, 0, 1, 3])
    with pytest.raises(ValueError, match="idx_f2k"):
        module.select_tracking_match(bad)
    bad = payload()
    bad["matching_calls"][0]["X11"] = torch.zeros(1, 1, 4, 3)
    with pytest.raises(ValueError, match="img_size"):
        module.select_tracking_match(bad)
    bad = payload()
    bad["opt_pose_calib_sim3"]["Xf"] = torch.zeros(4, 3)
    with pytest.raises(ValueError, match="Xf"):
        module.select_tracking_match(bad)


def test_preserves_warm_start_in_return_and_does_not_mutate_payload():
    module = import_module()
    data = payload()
    before = data["matching_calls"][0]["idx_1_to_2_init"].clone()
    result = module.select_tracking_match(data)
    assert torch.equal(result["matching_call"]["idx_1_to_2_init"], before)
    result["matching_call"]["idx_1_to_2_init"][0, 0] = 42
    assert torch.equal(data["matching_calls"][0]["idx_1_to_2_init"], before)
