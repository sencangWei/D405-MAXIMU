from copy import deepcopy
import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_ir_source_consistency_probe.py"
spec = importlib.util.spec_from_file_location("ir_source_consistency_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def inputs():
    tracks = {}
    candidates = []
    for eye in ("left", "right"):
        row = {
            "accepted": True, "first_index": 0, "second_index": 1,
            "first_t_sec": 1.0, "second_t_sec": 1.03,
            "metric_displacement_frame": f"infrared_{eye}_camera_i",
            "metric_displacement_camera_i_m": [0.01, 0.0, 0.0],
        }
        tracks[eye] = {
            "eye": eye, "times": [1.0, 1.03],
            "metric_camera_positions": [[0, 0, 0], [0.02, 0, 0]],
            "camera_rotations": np.tile(np.eye(3), (2, 1, 1)),
            "body_t_camera": np.eye(4), "observations": [row],
            "observation_confidences": [0.5],
        }
        candidates.append({
            **deepcopy(row), "eye": eye, "body_t_camera": np.eye(4),
            "observation_confidence": 0.8,
            "metric_displacement_camera_i_m": [0.02, 0.0, 0.0],
        })
    return tracks, candidates


def build(tracks):
    from ego_vio.vio.symmetric_ir_factors import build_symmetric_ir_factors
    return build_symmetric_ir_factors(
        [1.0, 1.03], np.tile(np.eye(3), (2, 1, 1)),
        [tracks["left"], tracks["right"]],
    )[0]


def test_actual_builder_targets_frozen_but_source_metadata_refreshes():
    tracks, candidates = inputs()
    original = build(tracks)
    refreshed, report = probe.refreshed_existing_tracks(tracks, candidates)
    rebuilt = build(refreshed)
    assert probe.validate_unchanged_targets(original, rebuilt)["max_target_abs_error_m"] == 0
    output, _ = probe.sync_existing_factor_source_metadata(original, rebuilt)
    assert output[0]["confidence"] != original[0]["confidence"]
    assert output[0]["own_stereo_residual_m"] == 0
    assert output[0]["metric_displacement_world_m"] == original[0]["metric_displacement_world_m"]
    assert report["appended_learned_observation_count"] == 0
    assert report["refreshed_existing_observation_count"] == 2
    assert tracks["left"]["observations"][0]["metric_displacement_camera_i_m"] == [0.01, 0, 0]


def test_missing_source_preserves_old_observation_and_factor_count():
    tracks, candidates = inputs()
    refreshed, report = probe.refreshed_existing_tracks(tracks, candidates[:1])
    assert refreshed["right"]["observations"] == tracks["right"]["observations"]
    assert len(build(refreshed)) == len(build(tracks))
    assert report["retained_original_observations_without_new_source"] == [
        {"eye": "right", "first_index": 0, "second_index": 1}
    ]


def test_extra_recovered_pair_does_not_append_learned_factor():
    tracks, candidates = inputs()
    extra = dict(candidates[0], first_index=1, second_index=2)
    refreshed, report = probe.refreshed_existing_tracks(tracks, [*candidates, extra])
    assert len(build(refreshed)) == 2
    assert report["appended_learned_observation_count"] == 0


@pytest.mark.parametrize("field,value", [
    ("first_t_sec", 2.0), ("metric_displacement_frame", "body_i"),
    ("body_t_camera", np.eye(4) * 2),
])
def test_changed_source_binding_rejected(field, value):
    tracks, candidates = inputs()
    candidates[0][field] = value
    with pytest.raises(ValueError, match="changed"):
        probe.refreshed_existing_tracks(tracks, candidates)


def test_duplicate_raw_candidate_rejected():
    tracks, candidates = inputs()
    with pytest.raises(ValueError, match="duplicate"):
        probe.refreshed_existing_tracks(tracks, [*candidates, candidates[0]])


@pytest.mark.parametrize("change", ["keys", "duplicate", "target", "nonfinite"])
def test_factor_target_identity_fail_closed(change):
    tracks, _ = inputs()
    before = build(tracks)
    after = deepcopy(before)
    if change == "keys":
        after.pop()
    elif change == "duplicate":
        after.append(deepcopy(after[0]))
    else:
        after[0]["metric_displacement_world_m"][0] = float("nan") if change == "nonfinite" else 1.0
    with pytest.raises(ValueError, match="changed|duplicate"):
        probe.validate_unchanged_targets(before, after)


def test_main_adapter_supplies_updated_factors_and_restores_patches(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from scipy.spatial.transform import Rotation
    tracks, candidates = inputs()
    before = build(tracks)
    state = SimpleNamespace(times=np.array([1.0, 1.03]), rotations=Rotation.identity(2))
    candidate = {"policy_arguments": {"stereo_weight_policy": "observation",
                                       "learned_motion_consistency_limit_m": None}}
    probe.paired.write_json(tmp_path / "local_motion_factors.json", before)
    monkeypatch.setattr(probe.paired, "load_refined_all_eye_candidates",
                        lambda *args, **kwargs: (candidates, {}, [], {}))
    monkeypatch.setattr(probe.gauge, "reconstruct_tracks",
                        lambda *args: (tracks, {}, []))
    monkeypatch.setattr(probe.gauge, "rebuild_source_factors",
                        lambda state, source, policy: (build(source), [], {}))
    monkeypatch.setattr(probe.gauge, "transform_existing_motion_factors",
                        lambda *args: (before, {}))
    received = []

    def solver(*args):
        received.append((args[2], deepcopy(args[9]), args[10]))
        return {"solver": "stub"}

    monkeypatch.setattr(probe.paired.physical, "run_solver_variant", solver)
    loader_before = probe.paired.load_refined_all_eye_candidates
    variant_before = probe.recovery.REFINED_VARIANT

    def run(*args):
        record = {"id": "record"}
        probe.paired.load_refined_all_eye_candidates(record)
        for variant in (probe.paired.ORIGINAL_VARIANT, probe.VARIANT):
            probe.paired.physical.run_solver_variant(
                record, tmp_path, variant, tmp_path, state, candidate,
                [], {}, [], before, "old", [], {},
            )
        return 0

    monkeypatch.setattr(probe.recovery, "main", run)
    assert probe.main([]) == 0
    assert received[0][1] == before
    assert received[1][1][0]["confidence"] != before[0]["confidence"]
    assert received[1][1][0]["metric_displacement_world_m"] == before[0]["metric_displacement_world_m"]
    assert received[1][2] == "same_native_source_consistency"
    assert probe.paired.physical.run_solver_variant is solver
    assert probe.paired.load_refined_all_eye_candidates is loader_before
    assert probe.recovery.REFINED_VARIANT == variant_before


def test_main_restores_patches_after_exception(monkeypatch):
    previous = probe.paired.physical.run_solver_variant
    def failed(*args):
        raise RuntimeError("abort")
    monkeypatch.setattr(probe.recovery, "main", failed)
    with pytest.raises(RuntimeError, match="abort"):
        probe.main([])
    assert probe.paired.physical.run_solver_variant is previous
