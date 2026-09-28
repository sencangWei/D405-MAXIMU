import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".planning/metric_window_bundle_20260928/trace_window_feature_losses.py"
SPEC = importlib.util.spec_from_file_location("trace_window_feature_losses", MODULE_PATH)
trace = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trace
SPEC.loader.exec_module(trace)


def _calibration():
    intr = {"fx": 100.0, "fy": 100.0, "cx": 32.0, "cy": 32.0, "coeffs": [0, 0, 0, 0]}
    return {"left_intrinsics": intr, "right_intrinsics": dict(intr), "baseline_m": 0.05}


def test_ordered_gate_counts_reports_overlap_and_exclusive():
    gates = [
        ("a", np.array([True, True, False, True])),
        ("b", np.array([True, False, True, True])),
        ("c", np.array([False, True, True, True])),
    ]
    counts = trace.ordered_gate_counts(gates)
    assert counts["total"] == 4
    assert counts["overlap_pass"] == {"a": 3, "b": 3, "c": 3}
    assert counts["exclusive_first_failure"] == {"a": 1, "b": 1, "c": 1}
    assert counts["final_pass"] == 1


def test_trace_returns_original_helper_result_unchanged_on_rejection():
    left = [np.zeros((160, 200), dtype=np.uint8) for _ in range(3)]
    right = [np.zeros((160, 200), dtype=np.uint8) for _ in range(3)]
    original = trace.prepare.track_stereo_window(left, right, _calibration(), initialize_poses=False)
    result, diagnostics = trace.trace_stereo_window(left, right, _calibration(), initialize_poses=False)
    assert result == original
    assert diagnostics["diagnostic_only"] is True
    assert diagnostics["source_eligible_corners"] == 0
    assert diagnostics["source_unmasked_corners"] == 0


def test_trace_stereo_window_checks_accepted_array_identity(monkeypatch):
    def fake_track(*args, **kwargs):
        obs = np.array([[[1.0, 2.0, 0.5, 2.0]], [[2.0, 2.0, 1.5, 2.0]], [[3.0, 2.0, 2.5, 2.0]]])
        return {
            "accepted": True,
            "reason": "ok",
            "observations": obs,
            "valid": np.ones((3, 1), dtype=bool),
            "initial_points": np.array([[0.0, 0.0, 0.2]]),
            "observation_frame": "infrared_left_camera0",
            "source_depth_limits_m": [0.07, 0.6],
            "tracked_landmarks": 1,
            "observations_count": 3,
            "policy": "fake",
        }

    def fake_shadow(*args, **kwargs):
        shadow = fake_track()
        shadow["observations"] = shadow["observations"].copy()
        shadow["observations"][1, 0, 0] += 1.0
        return {"diagnostic_only": True, "_shadow_result": shadow}

    monkeypatch.setattr(trace.prepare, "track_stereo_window", fake_track)
    monkeypatch.setattr(trace, "shadow_track_stages", fake_shadow)
    left = [np.zeros((160, 200), dtype=np.uint8) for _ in range(3)]
    right = [np.zeros((160, 200), dtype=np.uint8) for _ in range(3)]
    with pytest.raises(ValueError, match="observations"):
        trace.trace_stereo_window(left, right, _calibration(), initialize_poses=False)


def test_shadow_flow_matches_original_flow_validity():
    image = np.zeros((80, 120), dtype=np.uint8)
    cv2 = trace.cv2
    cv2.circle(image, (40, 40), 5, 255, -1)
    target = image.copy()
    points = np.array([[40.0, 40.0]], dtype=float)
    original_points, original_valid = trace.prepare._flow(image, target, points)
    shadow_points, shadow_valid, diagnostics = trace._flow_with_diagnostics(image, target, points)
    np.testing.assert_allclose(shadow_points, original_points, atol=0.0, rtol=0.0)
    np.testing.assert_array_equal(shadow_valid, original_valid)
    assert diagnostics["ordered_gates"]["final_pass"] == int(np.count_nonzero(original_valid))


def test_training_admission_does_not_claim_postfilter_as_tracking_loss(monkeypatch):
    base = trace.seam_controls.previous.base

    def fake_visual_initialization(data, calibration, train):
        admission = np.array(
            [
                [True, True, True, False],
                [True, False, True, False],
                [False, False, True, False],
            ],
            dtype=bool,
        )
        return np.zeros((3, 3)), [object(), object(), object()], admission

    def fake_training_support(train, admission, points):
        return np.array([True, False, True, False])

    monkeypatch.setattr(base, "visual_initialization", fake_visual_initialization)
    monkeypatch.setattr(base, "training_support", fake_training_support)
    data = {"valid": np.ones((3, 4), dtype=bool), "initial_points": np.zeros((4, 3))}
    out = trace.trace_training_admission(data, _calibration(), np.array([True, True, True, False]))
    assert out["accepted"] is True
    assert out["train_tracks_before_pnp"] == 3
    assert out["pnp_admission_counts"] == [3, 2, 1]
    assert out["training_support_tracks_after_pnp"] == 2
    assert "tracking" not in json.dumps(out).lower()


def test_skeleton_write_refuses_overwrite(tmp_path):
    output = tmp_path / "feature_loss_trace_v1.json"
    trace.write_json_no_overwrite(output, trace.build_skeleton())
    with pytest.raises(FileExistsError):
        trace.write_json_no_overwrite(output, trace.build_skeleton())


def test_frozen_pair_summary_identity_reports_exact_count_match(tmp_path):
    summary = {
        "cases": [
            {
                "case": "fresh4",
                "pairs": [
                    {
                        "pair": 27,
                        "indices": [1040, 1045],
                        "raw_frame_indices": [1040, 1041, 1042],
                        "node_train_tracks": [143, 19],
                        "crosswindow_support_count": 27,
                        "seam_source_candidates": 85,
                        "seam_born_added": 42,
                        "seam_born_train": 33,
                        "seam_born_heldout": 9,
                        "boundary_matches": 9,
                        "accepted": True,
                        "reason": "ok",
                    }
                ],
            }
        ]
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    actual = {
        "indices": [1040, 1045],
        "raw_frame_indices": [1040, 1041, 1042],
        "node_train_tracks_after_combined_pnp": [143, 19],
        "crosswindow_support_count": 27,
        "seam_replenish": {
            "seam_source_candidates": 85,
            "seam_born_added": 42,
            "seam_born_train": 33,
            "seam_born_heldout": 9,
        },
        "boundary_matches": {"matched_pairs": 9},
        "accepted": True,
        "reason": "ok_pre_ba_trace",
    }
    identity = trace.compare_frozen_pair_summary("fresh4", 27, actual, summary_path=path)
    assert identity["all_match"] is True
    assert identity["expected"]["node_train_tracks"] == [143, 19]
    assert identity["actual"]["node_train_tracks"] == [143, 19]
    assert identity["frozen_ba_reason"] == "ok"
    assert identity["trace_pre_ba_reason"] == "ok_pre_ba_trace"


def test_batch_requires_unique_10_by_3_and_keeps_acceptance_semantics_separate(monkeypatch):
    cases = [f"case{i}" for i in range(10)]
    monkeypatch.setattr(trace.full_shape_controls, "case_graphs", lambda: [(case, Path(f"/tmp/{case}.json")) for case in cases])

    def fake_trace_case_pair(case, pair):
        return {
            "case": case,
            "pair": pair,
            "accepted": True,
            "frozen_summary_identity": {"all_match": True},
        }

    monkeypatch.setattr(trace, "trace_case_pair", fake_trace_case_pair)
    monkeypatch.setattr(trace, "frozen_pair_context", lambda case, pair: {"accepted": pair != 28})
    out = trace.trace_batch()
    assert out["row_count"] == 30
    assert out["unique_case_pair_count"] == 30
    assert out["trace_pre_ba_accepted_count"] == 30
    assert out["frozen_ba_accepted_count"] == 20

    monkeypatch.setattr(trace.full_shape_controls, "case_graphs", lambda: [(cases[0], Path("/tmp/a.json"))] * 10)
    with pytest.raises(ValueError, match="ten unique"):
        trace.trace_batch()


def test_batch_does_not_mask_frozen_hash_contract_errors(monkeypatch):
    cases = [f"case{i}" for i in range(10)]
    monkeypatch.setattr(trace.full_shape_controls, "case_graphs", lambda: [(case, Path(f"/tmp/{case}.json")) for case in cases])

    def fake_trace_case_pair(case, pair):
        raise ValueError("frozen producer source hash mismatch")

    monkeypatch.setattr(trace, "trace_case_pair", fake_trace_case_pair)
    with pytest.raises(ValueError, match="frozen producer source hash mismatch"):
        trace.trace_batch()


def test_batch_does_not_mask_shadow_identity_contract_errors(monkeypatch):
    cases = [f"case{i}" for i in range(10)]
    monkeypatch.setattr(trace.full_shape_controls, "case_graphs", lambda: [(case, Path(f"/tmp/{case}.json")) for case in cases])

    def fake_trace_case_pair(case, pair):
        raise ValueError("instrumented tracking changed numeric array observations")

    monkeypatch.setattr(trace, "trace_case_pair", fake_trace_case_pair)
    with pytest.raises(ValueError, match="instrumented tracking changed"):
        trace.trace_batch()


def test_cohort_counts_separates_raw_valid_from_train_pnp():
    valid = np.array(
        [
            [True, True, True, False],
            [True, False, True, True],
        ],
        dtype=bool,
    )
    admission = np.array(
        [
            [True, False, True, False],
            [False, False, True, True],
        ],
        dtype=bool,
    )
    train = np.array([True, False, True, True])
    mask = np.array([True, True, False, False])
    out = trace.cohort_counts("a", valid, admission, train, mask)
    assert out["points"] == 2
    assert out["train_points"] == 1
    assert out["raw_valid_counts"] == [2, 1]
    assert out["train_raw_valid_counts"] == [1, 1]
    assert out["pnp_admission_counts_train"] == [1, 0]
