from copy import deepcopy
import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_bidirectional_se3_source_probe.py"
spec = importlib.util.spec_from_file_location("bidirectional_se3_source_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def motions():
    common = {"accepted": True, "metric_displacement_frame": "infrared_left_camera_i",
              "pnp_rotation_quaternion_xyzw": [0, 0, 0, 1], "pnp_inlier_ratio": 0.9,
              "rotation_error_deg": 0.0}
    return (dict(common, metric_displacement_camera_i_m=[0.01, 0, 0],
                 metric_distance_m=0.01, scale=1.0),
            dict(common, metric_displacement_camera_i_m=[-0.012, 0, 0],
                 metric_distance_m=0.012, scale=1.2))


def test_native_scalar_gate_preserved_but_geometry_really_fused():
    forward, reverse = motions()
    before = deepcopy(forward), deepcopy(reverse)
    native = probe.source.diag.combine_bidirectional_native
    result = probe.combine_native_geometry(forward, reverse, native)
    assert np.allclose(result["metric_displacement_camera_i_m"], [0.011, 0, 0])
    assert result["metric_distance_m"] == pytest.approx(0.011)
    assert result["scale"] == pytest.approx(1.1)
    assert result["native_scalar_acceptance_unchanged"] is True
    assert result["scalar_bidirectional_baseline"] == native(forward, reverse)
    assert result["pnp_reprojection_source"] == "raw_forward_before_se3_midpoint"
    assert (forward, reverse) == before


def test_native_rejection_never_salvaged():
    forward, reverse = motions()
    reverse["accepted"] = False
    native = probe.source.diag.combine_bidirectional_native
    assert probe.combine_native_geometry(forward, reverse, native) == native(forward, reverse)


def test_mismatched_visual_scale_sources_rejected():
    forward, reverse = motions()
    reverse["metric_distance_m"] = 0.02
    with pytest.raises(ValueError, match="scale source mismatch"):
        probe.combine_native_geometry(forward, reverse, lambda f, r: dict(f))


def test_zero_midpoint_retains_evidence_but_is_not_geometry_accepted():
    forward, reverse = motions()
    reverse.update(metric_displacement_camera_i_m=[0.01, 0, 0],
                   metric_distance_m=0.01, scale=1.0)
    result = probe.combine_native_geometry(forward, reverse, probe.source.diag.combine_bidirectional_native)
    assert result["accepted"] is False
    assert result["reason"] == "bidirectional_se3_zero_motion"
    assert result["scalar_bidirectional_baseline"]["accepted"] is True
    assert result["bidirectional_se3"]["vector_closure_m"] == pytest.approx(0.02)


def test_near_pi_keeps_native_evidence_without_promoting_ambiguous_geometry():
    forward, reverse = motions()
    reverse["pnp_rotation_quaternion_xyzw"] = [1, 0, 0, 0]
    result = probe.combine_native_geometry(forward, reverse, probe.source.diag.combine_bidirectional_native)
    assert result["accepted"] is False
    assert result["reason"] == "bidirectional_se3_rotation_ambiguous"
    assert result["scalar_bidirectional_baseline"]["accepted"] is True


def test_candidate_rows_include_existing_and_recovery_without_low_excitation():
    base = {"first_index": 0, "second_index": 1, "first_t_sec": 1.0, "second_t_sec": 1.03}
    report = {"report_path": "/source.json", "observations": [
        dict(base, accepted=True), dict(base, accepted=False, reason="pnp_failed"),
        dict(base, accepted=False, reason="translation_excitation_low"),
    ]}
    selected = probe.candidate_rows([report], {"/source.json": Path("/source.json")}, np.array([1.0, 1.03]))
    assert [row["index"] for row in selected] == [0, 1]


def test_patch_restoration_and_full_raw_results(monkeypatch):
    original = probe.source.diag.combine_bidirectional_native
    def run(argv):
        assert probe.source.SCHEMA == "umi_bidirectional_se3_source_preflight_v1"
        assert probe.source.DisparityCache().max_entries == 2048
        forward, reverse = motions()
        assert probe.source.diag._result_summary(reverse) == reverse
        assert probe.source.diag.combine_bidirectional_native(forward, reverse)["metric_distance_m"] == pytest.approx(0.011)
        raise RuntimeError("stop")
    monkeypatch.setattr(probe.source, "main", run)
    with pytest.raises(RuntimeError, match="stop"):
        probe.main([])
    assert probe.source.diag.combine_bidirectional_native is original
