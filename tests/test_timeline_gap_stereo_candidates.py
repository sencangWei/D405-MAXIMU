from copy import deepcopy
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.vio.timeline_gap_stereo_candidates import build_timeline_gap_shared_rows
import ego_vio.vio.timeline_gap_stereo_candidates as bridge


def _body_t(tx=0.0, rotation=None):
    matrix = np.eye(4)
    matrix[:3, :3] = np.eye(3) if rotation is None else rotation
    matrix[:3, 3] = [tx, 0.0, 0.0]
    return matrix.tolist()


def _original_candidate(conf=0.4):
    return {
        "eye": "left",
        "first_index": 0,
        "second_index": 1,
        "first_t_sec": 101.862,
        "second_t_sec": 101.895,
        "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
        "metric_displacement_frame": "infrared_left_camera_i",
        "observation_confidence": conf,
        "body_t_camera": _body_t(0.1),
        "reference_first_index": 0,
        "reference_second_index": 1,
    }


def _original_row(conf=0.4):
    return {
        "accepted": True,
        "first_index": 0,
        "second_index": 1,
        "first_t_sec": 101.862,
        "second_t_sec": 101.895,
        "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
        "metric_displacement_frame": "body_i",
        "pnp_inlier_ratio": conf,
    }


def _native_result(eye, scale=1.0, inliers=0.8, accepted=True, reason=None):
    frame = f"infrared_{eye}_camera_i"
    result = {
        "accepted": accepted,
        "metric_displacement_camera_i_m": [0.2, 0.0, 0.0] if eye == "left" else [0.22, 0.0, 0.0],
        "metric_displacement_frame": frame,
        "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
        "pnp_inlier_ratio": inliers,
        "rotation_error_deg": 0.0,
        "scale": scale,
        "bidirectional_relative_disagreement": 0.0,
    }
    if reason is not None:
        result["reason"] = reason
    return result


def _gap_row(first=2, second=3, *, left_accepted=True, right_accepted=True, left_inliers=0.8, right_inliers=0.8):
    times = [100.0, 101.0, 103.862, 103.895, 103.928, 103.961]
    row = {
        "first_index": first,
        "second_index": second,
        "first_t_sec": times[first],
        "second_t_sec": times[second],
        "duration_s": 0.033,
        "native_bidirectional_cross_class": ("L" if left_accepted else "notL") + "_" + ("R" if right_accepted else "notR"),
        "factory_frame_closure": {"vector_closure_mm": 3.5, "rotation_closure_deg": 0.2},
    }
    for eye, accepted, inliers in (("left", left_accepted, left_inliers), ("right", right_accepted, right_inliers)):
        row[f"raw_forward_{eye}"] = _native_result(eye, inliers=inliers, accepted=accepted, reason=None if accepted else "pnp_failed")
        row[f"raw_reverse_{eye}"] = _native_result(eye, inliers=inliers, accepted=accepted, reason=None if accepted else "pnp_failed")
        row[f"native_combined_{eye}"] = _native_result(eye, inliers=inliers, accepted=accepted, reason=None if accepted else "reverse_motion_failed")
    return row


def _gap_report(left_accepted=True, right_accepted=True, *, observations=None, span_frames=1):
    row = _gap_row(left_accepted=left_accepted, right_accepted=right_accepted)
    return {
        "schema": "umi_independent_ir_timeline_gap_probe_v1",
        "status": "TIMELINE_GAP_SOURCE_PROBE_READY",
        "id": "record",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "backend_launched": False,
        "scorer_launched": False,
        "tracker_reference_used": False,
        "production_promoted": False,
        "gpu_model_used": False,
        "not_rejected_row_recovery": True,
        "image_source_lineage": {
            "selected_frames_loaded_from_db3_directly": True,
        },
        "pair_policy": {
            "selection": "uniform_fixed_span_over_full_d405_timeline_tail",
            "span_frames": span_frames,
            "both_raw_frontend_end_t_sec": 102.0,
        },
        "camera_pose_conversion": {
            "body_T_left_ir": _body_t(0.1),
            "body_T_right_ir": _body_t(0.2, Rotation.from_euler("z", 5.0, degrees=True).as_matrix()),
        },
        "observations": [row] if observations is None else observations,
    }


def _dense_reference_and_d405():
    times = [101.862, 101.895, 103.862, 103.895, 103.928, 103.961]
    full = [100.0, 101.0, 103.862, 103.895, 103.928, 103.961]
    rotations = np.repeat(np.eye(3)[None, :, :], len(times), axis=0)
    return times, rotations, full


def test_appends_timeline_gap_pair_and_transforms_nonzero_left_right_levers():
    body_rotations = np.repeat(np.eye(3)[None, :, :], 4, axis=0)
    body_rotations[3] = Rotation.from_euler("z", 10.0, degrees=True).as_matrix()
    rows, candidates, diag = build_timeline_gap_shared_rows(
        [101.862, 101.895, 103.862, 103.895],
        body_rotations,
        [100.0, 101.0, 103.862, 103.895],
        [_original_row()],
        [_original_candidate()],
        _gap_report(),
    )

    assert diag["external_ground_truth_used"] is False
    assert diag["backend_launched"] is False
    assert diag["appended_pair_count"] == 1
    assert len(rows) == 2
    appended = rows[1]
    assert appended["first_index"] == 2
    assert appended["second_index"] == 3
    assert appended["metric_displacement_frame"] == "body_i"
    right_r = Rotation.from_euler("z", 5.0, degrees=True).as_matrix()
    r_rel = body_rotations[2].T @ body_rotations[3]
    expected_left = np.asarray([0.2, 0.0, 0.0]) - r_rel @ np.asarray([0.1, 0.0, 0.0]) + np.asarray([0.1, 0.0, 0.0])
    expected_right = right_r @ np.asarray([0.22, 0.0, 0.0]) - r_rel @ np.asarray([0.2, 0.0, 0.0]) + np.asarray([0.2, 0.0, 0.0])
    np.testing.assert_allclose(appended["metric_displacement_camera_i_m"], 0.5 * (expected_left + expected_right), atol=1e-12)
    assert {candidate["eye"] for candidate in candidates if candidate.get("timeline_gap_source")} == {"left", "right"}
    assert diag["timeline_gap_closure_metadata"][0]["factory_frame_closure"]["vector_closure_mm"] == 3.5
    assert diag["tracker_reference_used"] is False
    assert diag["production_promoted"] is False
    assert diag["gpu_model_used"] is False


def test_rejected_both_adds_no_rows_and_keeps_original_only():
    rows, candidates, diag = build_timeline_gap_shared_rows(
        [101.862, 101.895, 103.862, 103.895],
        np.repeat(np.eye(3)[None, :, :], 4, axis=0),
        [100.0, 101.0, 103.862, 103.895],
        [_original_row()],
        [_original_candidate()],
        _gap_report(left_accepted=False, right_accepted=False),
    )

    assert len(rows) == 1
    assert diag["appended_pair_count"] == 0
    assert diag["recovered_eye_candidate_count"] == 0
    assert set(diag["skipped_eye_candidate_counts"]) == {"left:forward_pnp_failed", "right:forward_pnp_failed"}


def test_reference_binding_uses_full_d405_time_not_raw_index_offset():
    rows, _candidates, diag = build_timeline_gap_shared_rows(
        [101.862, 101.895, 103.862, 103.895],
        np.repeat(np.eye(3)[None, :, :], 4, axis=0),
        [100.0, 101.0, 103.862, 103.895],
        [_original_row()],
        [_original_candidate()],
        _gap_report(),
    )

    assert rows[1]["first_index"] == 2
    assert rows[1]["first_t_sec"] == pytest.approx(103.862)
    assert diag["appended_pair_count"] == 1


def test_rejects_bad_timeline_span_and_report_flags():
    report = _gap_report()
    report["observations"][0]["second_index"] = 4
    with pytest.raises(ValueError, match="span_frames"):
        build_timeline_gap_shared_rows(
            [101.862, 101.895, 103.862, 103.895, 103.928],
            np.repeat(np.eye(3)[None, :, :], 5, axis=0),
            [100.0, 101.0, 103.862, 103.895, 103.928],
            [_original_row()],
            [_original_candidate()],
            report,
        )

    report = _gap_report()
    report["external_ground_truth_used"] = True
    with pytest.raises(ValueError, match="external_ground_truth_used"):
        build_timeline_gap_shared_rows(
            [101.862, 101.895, 103.862, 103.895],
            np.repeat(np.eye(3)[None, :, :], 4, axis=0),
            [100.0, 101.0, 103.862, 103.895],
            [_original_row()],
            [_original_candidate()],
            report,
        )

    report = _gap_report()
    del report["image_source_lineage"]
    with pytest.raises(ValueError, match="DB3"):
        build_timeline_gap_shared_rows(
            [101.862, 101.895, 103.862, 103.895],
            np.repeat(np.eye(3)[None, :, :], 4, axis=0),
            [100.0, 101.0, 103.862, 103.895],
            [_original_row()],
            [_original_candidate()],
            report,
        )

    report = _gap_report()
    report["image_source_lineage"]["selected_frames_loaded_from_db3_directly"] = False
    with pytest.raises(ValueError, match="DB3"):
        build_timeline_gap_shared_rows(
            [101.862, 101.895, 103.862, 103.895],
            np.repeat(np.eye(3)[None, :, :], 4, axis=0),
            [100.0, 101.0, 103.862, 103.895],
            [_original_row()],
            [_original_candidate()],
            report,
        )


def test_confidence_uses_existing_fusion_function(monkeypatch):
    calls = []

    def fake_confidence(observation, reference_scale):
        calls.append((observation["metric_displacement_frame"], reference_scale))
        return 0.7 if observation["metric_displacement_frame"] == "infrared_left_camera_i" else 0.6

    monkeypatch.setattr(bridge.fusion, "stereo_observation_confidence", fake_confidence)
    rows, candidates, _diag = build_timeline_gap_shared_rows(
        [101.862, 101.895, 103.862, 103.895],
        np.repeat(np.eye(3)[None, :, :], 4, axis=0),
        [100.0, 101.0, 103.862, 103.895],
        [_original_row()],
        [_original_candidate()],
        _gap_report(),
        reference_scale_by_eye={"left": 1.25, "right": 1.5},
    )

    assert calls == [("infrared_left_camera_i", 1.25), ("infrared_right_camera_i", 1.5)]
    by_eye = {candidate["eye"]: candidate["observation_confidence"] for candidate in candidates if candidate.get("timeline_gap_source")}
    assert by_eye == {"left": 0.7, "right": 0.6}
    assert rows[1]["pnp_inlier_ratio"] == pytest.approx(0.7)


def test_duplicate_timeline_same_eye_deduplicates_by_existing_confidence_policy_and_inputs_immutable():
    report = _gap_report()
    duplicate = deepcopy(report["observations"][0])
    duplicate["native_combined_left"]["pnp_inlier_ratio"] = 0.2
    duplicate["raw_forward_left"]["pnp_inlier_ratio"] = 0.2
    duplicate["raw_reverse_left"]["pnp_inlier_ratio"] = 0.2
    report["observations"].append(duplicate)
    original_rows = [_original_row()]
    original_candidates = [_original_candidate()]
    frozen_report = deepcopy(report)
    frozen_rows = deepcopy(original_rows)
    frozen_candidates = deepcopy(original_candidates)

    rows, _candidates, diag = build_timeline_gap_shared_rows(
        [101.862, 101.895, 103.862, 103.895],
        np.repeat(np.eye(3)[None, :, :], 4, axis=0),
        [100.0, 101.0, 103.862, 103.895],
        original_rows,
        original_candidates,
        report,
    )

    assert report == frozen_report
    assert original_rows == frozen_rows
    assert original_candidates == frozen_candidates
    assert len(rows) == 2
    assert diag["recovered_pairs"]["deduplicated_recovered_same_eye_count"] >= 1


def test_support_policy_default_all_is_explicitly_unchanged():
    times, rotations, full = _dense_reference_and_d405()
    report = _gap_report(
        observations=[
            _gap_row(2, 3, left_inliers=0.9, right_inliers=0.8),
            _gap_row(3, 4, left_inliers=0.7, right_inliers=0.6),
        ],
        span_frames=1,
    )

    rows_default, candidates_default, diag_default = build_timeline_gap_shared_rows(
        times,
        rotations,
        full,
        [_original_row()],
        [_original_candidate()],
        report,
    )
    rows_all, candidates_all, diag_all = build_timeline_gap_shared_rows(
        times,
        rotations,
        full,
        [_original_row()],
        [_original_candidate()],
        report,
        support_policy="all",
    )

    assert rows_default == rows_all
    assert candidates_default == candidates_all
    assert diag_default["support_sampling"]["support_policy"] == "all"
    assert diag_default["support_sampling"] == diag_all["support_sampling"]
    assert diag_default["support_sampling"]["support_retained_pair_count"] == 2
    assert diag_default["support_sampling"]["support_redundant_pair_count"] == 0


def test_nonoverlap_policy_keeps_higher_confidence_pair_and_both_eyes():
    times, rotations, full = _dense_reference_and_d405()
    report = _gap_report(
        observations=[
            _gap_row(2, 4, left_inliers=0.2, right_inliers=0.3),
            _gap_row(3, 5, left_inliers=0.9, right_inliers=0.8),
        ],
        span_frames=2,
    )

    rows, candidates, diag = build_timeline_gap_shared_rows(
        times,
        rotations,
        full,
        [_original_row()],
        [_original_candidate()],
        report,
        support_policy="nonoverlap",
    )

    assert [(row["first_index"], row["second_index"]) for row in rows] == [(0, 1), (3, 5)]
    retained_gap = [candidate for candidate in candidates if candidate.get("timeline_gap_source")]
    assert {(candidate["reference_first_index"], candidate["reference_second_index"]) for candidate in retained_gap} == {(3, 5)}
    assert {candidate["eye"] for candidate in retained_gap} == {"left", "right"}
    support = diag["support_sampling"]
    assert support["support_policy"] == "nonoverlap"
    assert support["support_retained_pair_count"] == 1
    assert support["support_redundant_pair_count"] == 1
    assert support["support_retained_pairs"][0]["reference_pair"] == [3, 5]
    assert support["support_redundant_pairs"][0]["reference_pair"] == [2, 4]


def test_nonoverlap_ties_are_deterministic_by_reference_pair_not_input_order():
    times, rotations, full = _dense_reference_and_d405()
    rows_a, _candidates_a, diag_a = build_timeline_gap_shared_rows(
        times,
        rotations,
        full,
        [_original_row()],
        [_original_candidate()],
        _gap_report(observations=[_gap_row(3, 5), _gap_row(2, 4)], span_frames=2),
        support_policy="nonoverlap",
    )
    rows_b, _candidates_b, diag_b = build_timeline_gap_shared_rows(
        times,
        rotations,
        full,
        [_original_row()],
        [_original_candidate()],
        _gap_report(observations=[_gap_row(2, 4), _gap_row(3, 5)], span_frames=2),
        support_policy="nonoverlap",
    )

    assert [(row["first_index"], row["second_index"]) for row in rows_a] == [(0, 1), (2, 4)]
    assert rows_a == rows_b
    assert diag_a["support_sampling"]["support_retained_pairs"] == diag_b["support_sampling"]["support_retained_pairs"]
    assert diag_a["support_sampling"]["support_redundant_pairs"] == diag_b["support_sampling"]["support_redundant_pairs"]


def test_nonoverlap_counts_touching_endpoints_as_shared_support():
    times, rotations, full = _dense_reference_and_d405()
    rows, _candidates, diag = build_timeline_gap_shared_rows(
        times,
        rotations,
        full,
        [_original_row()],
        [_original_candidate()],
        _gap_report(observations=[_gap_row(2, 3), _gap_row(3, 4)], span_frames=1),
        support_policy="nonoverlap",
    )

    assert [(row["first_index"], row["second_index"]) for row in rows] == [(0, 1), (2, 3)]
    assert diag["support_sampling"]["support_redundant_pair_count"] == 1
    assert diag["support_sampling"]["support_redundant_pairs"][0]["overlaps_retained_pair"]["support_interval"] == [2, 3]


def test_nonoverlap_never_removes_original_rows_and_rejects_invalid_policy():
    times, rotations, full = _dense_reference_and_d405()
    original_rows = [_original_row()]
    original_candidates = [_original_candidate()]
    rows, _candidates, diag = build_timeline_gap_shared_rows(
        times,
        rotations,
        full,
        original_rows,
        original_candidates,
        _gap_report(observations=[_gap_row(2, 4), _gap_row(3, 5)], span_frames=2),
        support_policy="nonoverlap",
    )

    assert original_rows == [_original_row()]
    assert original_candidates == [_original_candidate()]
    assert (rows[0]["first_index"], rows[0]["second_index"]) == (0, 1)
    assert rows[0]["metric_displacement_camera_i_m"] == _original_row()["metric_displacement_camera_i_m"]
    assert diag["appended_pair_count"] == 1
    with pytest.raises(ValueError, match="support_policy"):
        build_timeline_gap_shared_rows(
            times,
            rotations,
            full,
            [_original_row()],
            [_original_candidate()],
            _gap_report(),
            support_policy="nearest",
        )
