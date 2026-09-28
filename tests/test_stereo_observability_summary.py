import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".planning" / "metric_window_bundle_20260928" / "summarize_observability_controls.py"
spec = importlib.util.spec_from_file_location("summarize_observability_controls", SCRIPT)
summary = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = summary
spec.loader.exec_module(summary)


CASES = ["dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh1", "fresh2", "fresh3", "fresh4"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def window(row_id: int, accepted=True, obs=True, endpoint_delta=0.0):
    start = row_id * 20
    row = {
        "window": row_id + 1,
        "indices": [start, start + 5, start + 10, start + 15, start + 20],
        "accepted": accepted,
        "reason": "ok" if accepted else "training_only_PnP_failed",
    }
    if accepted:
        row["endpoint_m"] = [0.001 * row_id + endpoint_delta, 0.0, 0.0]
        row["initial_endpoint_m"] = [0.002 * row_id, 0.0, 0.0]
        row["diagnostics"] = {}
        if obs:
            row["diagnostics"]["endpoint_observability"] = {
                "diagnostic_only": True,
                "all_factors": {
                    "endpoint_rank": 3,
                    "rank_deficient": False,
                    "weak_response_mm_per_unit_normalized_residual": 0.2 + row_id,
                },
                "pixel_rows_only": {
                    "endpoint_rank": 2,
                    "rank_deficient": True,
                    "weak_response_mm_per_unit_normalized_residual": None,
                },
                "endpoint_tracks": 20 + row_id,
                "source_endpoint_common_tracks": 10 + row_id,
                "endpoint_depth_m_quantiles": [0.1, 0.2, 0.3],
                "endpoint_fx_baseline_over_depth_px_quantiles": [10.0, 20.0, 30.0],
            }
    return row


def make_summary(tmp_path: Path, *, obs=True, duplicate=False, endpoint_delta=0.0):
    source = tmp_path / ("source_obs.txt" if obs else "source_old.txt")
    source.write_text("source")
    input_file = tmp_path / "input_shared.txt"
    input_file.write_text("input")
    cases = []
    names = CASES.copy()
    if duplicate:
        names[-1] = names[0]
    for name in names:
        rows = [window(i, accepted=i % 7 != 1, obs=obs) for i in range(59)]
        if endpoint_delta:
            for row in rows:
                if row.get("accepted"):
                    row["endpoint_m"][0] += endpoint_delta
                    break
        cases.append(
            {
                "case": name,
                "windows": rows,
                "input_sha256": {str(input_file): sha(input_file)},
                "decoded_grayscale_frame_sha256": {"left:1": "abc", "right:1": "def"},
            }
        )
    data = {
        "cases": cases,
        "source_sha256": {str(source): sha(source)},
        "external_reference_used": False,
        "production_modified": False,
    }
    if obs:
        data["endpoint_observability_adapter"] = {
            "diagnostic_only": True,
            "external_reference_used": False,
            "used_for_selection_or_graph_weights": False,
            "source_sha256": {str(source): sha(source)},
            "noise_assumption": "provisional normalized robust pixel/gyro/bias residuals",
            "all_factors": "nuisance-marginalized optimized robust Jacobian",
            "pixel_rows_only": "same solution and robust pixel weights; gyro/bias rows excluded",
        }
    path = tmp_path / ("observability.json" if obs else "previous.json")
    path.write_text(json.dumps(data) + "\n")
    return path, input_file


def make_numeric_proof(tmp_path: Path, source: Path, *, numpy_version: str, match_path: Path):
    source_data = json.loads(source.read_text())
    match_data = json.loads(match_path.read_text())
    proof_cases = []
    for case, match_case in zip(source_data["cases"], match_data["cases"]):
        row = dict(case["windows"][0])
        row["endpoint_m"] = match_case["windows"][0]["endpoint_m"]
        row["initial_endpoint_m"] = match_case["windows"][0]["initial_endpoint_m"]
        proof = {
            "accepted_reason_equal": True,
            "original": {
                "nfev": 3,
                "cost": 1.25,
                "solver_status": 2,
                "endpoint_m": row["endpoint_m"],
            },
            "instrumented": {
                "nfev": 3,
                "cost": 1.25,
                "solver_status": 2,
                "endpoint_m": row["endpoint_m"],
            },
            "centers_bit_equal": True,
            "centers_max_absolute_difference": 0.0,
            "landmarks_bit_equal": True,
            "landmarks_max_absolute_difference": 0.0,
            "gyro_bias_bit_equal": True,
            "gyro_bias_max_absolute_difference": 0.0,
        }
        row.setdefault("diagnostics", {})["same_input_paired_proof"] = proof
        proof_cases.append({**case, "windows": [row]})
    proof = {**source_data, "cases": proof_cases, "runtime": {"numpy": numpy_version}}
    path = tmp_path / f"proof_{numpy_version}.json"
    path.write_text(json.dumps(proof) + "\n")
    return path


def mutate_case(path: Path, case_name: str, mutate):
    data = json.loads(path.read_text())
    for case in data["cases"]:
        if case["case"] == case_name:
            mutate(case)
            break
    path.write_text(json.dumps(data) + "\n")


def test_aggregation_and_identity_verification(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)
    output = tmp_path / "report.json"

    assert summary.main(["--observability-summary", str(observed), "--previous-summary", str(previous), "--output", str(output)]) == 0

    report = json.loads(output.read_text())
    assert report["diagnostic_only"] is True
    assert report["external_ground_truth_read"] is False
    assert report["no_weights_or_trajectory_selection"] is True
    assert report["accuracy_claim"] is False
    assert report["cases"] == 10
    assert report["windows_per_case"] == 59
    assert report["identity"]["accepted_reason_indices_unchanged"] is True
    assert report["identity"]["max_endpoint_difference_m"] == 0.0
    assert report["identity"]["decoded_image_hash_identity"] is True
    assert report["per_case"]["dev1"]["accepted_windows"] > 0
    assert report["per_case"]["dev1"]["all_factors"]["endpoint_rank"]["p50"] == 3.0
    assert report["per_case"]["dev1"]["pixel_rows_only"]["weak_response_mm_per_unit_normalized_residual"]["unavailable_rank_deficient"] > 0
    assert "distribution_of_window_quantiles" in report["per_case"]["dev1"]["endpoint_depth_m_quantiles"]
    assert report["input_summaries"]["observability"]["path"] == str(observed.resolve())
    assert len(report["input_summaries"]["previous"]["sha256"]) == 64
    assert report["endpoint_observability_adapter"]["diagnostic_only"] is True


def test_rejects_incomplete_or_duplicate_cases(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True, duplicate=True)
    with pytest.raises(ValueError, match="ten unique"):
        summary.summarize(observed, previous)


def test_rejects_wrong_exact_case_names_even_when_ten_unique(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)
    for path in (previous, observed):
        data = json.loads(path.read_text())
        data["cases"][-1]["case"] = "surprise"
        path.write_text(json.dumps(data) + "\n")

    with pytest.raises(ValueError, match="exact case names"):
        summary.summarize(observed, previous)


def test_rejects_shifted_frozen_schedule_even_when_both_summaries_match(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)
    for path in (previous, observed):
        mutate_case(path, "dev1", lambda case: case["windows"][0].update({"indices": [1, 6, 11, 16, 21]}))

    with pytest.raises(ValueError, match="frozen schedule"):
        summary.summarize(observed, previous)


def test_rejects_hash_mismatch(tmp_path):
    previous, input_file = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)
    input_file.write_text("changed")
    with pytest.raises(ValueError, match="hash changed"):
        summary.summarize(observed, previous)


def test_rejects_solver_identity_change(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True, endpoint_delta=1e-6)
    with pytest.raises(ValueError, match="endpoint changed"):
        summary.summarize(observed, previous)


def test_rejects_admission_identity_change(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)
    mutate_case(
        observed,
        "dev1",
        lambda case: case["windows"][0].update(
            {"admission": {"policy": "fixed", "pre_count": 40, "post_count": 39}}
        ),
    )

    with pytest.raises(ValueError, match="admission"):
        summary.summarize(observed, previous)


def test_strict_endpoint_identity_fails_but_numeric_runtime_proof_mode_passes(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True, endpoint_delta=5e-7)
    proof_old = make_numeric_proof(tmp_path, previous, numpy_version="2.2.6", match_path=previous)
    proof_new = make_numeric_proof(tmp_path, observed, numpy_version="1.26.4", match_path=observed)

    with pytest.raises(ValueError, match="endpoint changed"):
        summary.summarize(observed, previous)

    report = summary.summarize(
        observed, previous, numeric_runtime_summaries=[proof_old, proof_new]
    )

    assert report["identity"]["endpoint_exact_identity"] is False
    assert report["identity"]["strict_bound_m"] == 1e-10
    assert report["identity"]["numeric_bound_m"] == 1e-6
    assert report["identity"]["max_endpoint_difference_m"] == pytest.approx(5e-7)
    assert sorted(report["numeric_runtime_proof"]["numpy_versions"]) == ["1.26.4", "2.2.6"]
    assert report["numeric_runtime_proof"]["label"] == "diagnostic_numerical_reproducibility_boundary"


def test_invalid_numeric_runtime_proof_is_not_accepted(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True, endpoint_delta=5e-7)
    proof_old = make_numeric_proof(tmp_path, previous, numpy_version="2.2.6", match_path=previous)
    proof_new = make_numeric_proof(tmp_path, observed, numpy_version="1.26.4", match_path=observed)
    data = json.loads(proof_new.read_text())
    data["cases"][0]["windows"][0]["diagnostics"]["same_input_paired_proof"]["centers_bit_equal"] = False
    proof_new.write_text(json.dumps(data) + "\n")

    with pytest.raises(ValueError, match="centers"):
        summary.summarize(observed, previous, numeric_runtime_summaries=[proof_old, proof_new])


def test_numeric_runtime_proof_requires_same_case_input_hashes(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True, endpoint_delta=5e-7)
    proof_old = make_numeric_proof(tmp_path, previous, numpy_version="2.2.6", match_path=previous)
    proof_new = make_numeric_proof(tmp_path, observed, numpy_version="1.26.4", match_path=observed)
    different_input = tmp_path / "different_input.txt"
    different_input.write_text("different input")
    data = json.loads(proof_new.read_text())
    data["cases"][0]["input_sha256"] = {str(different_input): sha(different_input)}
    proof_new.write_text(json.dumps(data) + "\n")

    with pytest.raises(ValueError, match="input_sha256"):
        summary.summarize(observed, previous, numeric_runtime_summaries=[proof_old, proof_new])


def test_numeric_runtime_proof_decoded_hashes_must_be_matching_subset(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True, endpoint_delta=5e-7)
    proof_old = make_numeric_proof(tmp_path, previous, numpy_version="2.2.6", match_path=previous)
    proof_new = make_numeric_proof(tmp_path, observed, numpy_version="1.26.4", match_path=observed)
    data = json.loads(proof_new.read_text())
    data["cases"][0]["decoded_grayscale_frame_sha256"] = {"left:1": "wrong"}
    proof_new.write_text(json.dumps(data) + "\n")

    with pytest.raises(ValueError, match="decoded hash"):
        summary.summarize(observed, previous, numeric_runtime_summaries=[proof_old, proof_new])


def test_numeric_runtime_proof_does_not_allow_endpoint_delta_above_bound(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True, endpoint_delta=2e-6)
    proof_old = make_numeric_proof(tmp_path, previous, numpy_version="2.2.6", match_path=previous)
    proof_new = make_numeric_proof(tmp_path, observed, numpy_version="1.26.4", match_path=observed)

    with pytest.raises(ValueError, match="numeric reproducibility bound"):
        summary.summarize(observed, previous, numeric_runtime_summaries=[proof_old, proof_new])


def test_available_false_observability_counts_unavailable_and_skips_stats(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)

    def make_unavailable(case):
        for row in case["windows"]:
            if row.get("accepted"):
                row["diagnostics"]["endpoint_observability"] = {"available": False}
                break

    mutate_case(observed, "dev1", make_unavailable)
    report = summary.summarize(observed, previous)

    assert report["per_case"]["dev1"]["counts_diagnostic_unavailable"] == 1


@pytest.mark.parametrize(
    "factor,match",
    [
        ({"endpoint_rank": 4, "rank_deficient": False, "weak_response_mm_per_unit_normalized_residual": 1.0}, "endpoint rank"),
        ({"endpoint_rank": 2, "rank_deficient": False, "weak_response_mm_per_unit_normalized_residual": None}, "rank_deficient"),
        ({"endpoint_rank": 2, "rank_deficient": True, "weak_response_mm_per_unit_normalized_residual": 1.0}, "rank deficient"),
        ({"endpoint_rank": 3, "rank_deficient": False, "weak_response_mm_per_unit_normalized_residual": None}, "rank 3"),
        ({"endpoint_rank": 3, "rank_deficient": False, "weak_response_mm_per_unit_normalized_residual": -1.0}, "positive finite"),
        ({"rank_deficient": False, "weak_response_mm_per_unit_normalized_residual": 1.0}, "endpoint rank"),
    ],
)
def test_factor_stats_rejects_invalid_rank_response_invariants(factor, match):
    with pytest.raises(ValueError, match=match):
        summary.factor_stats([{"all_factors": factor}], "all_factors")


def test_requires_fail_closed_provenance_hash_sections(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)
    data = json.loads(observed.read_text())
    data["source_sha256"] = {}
    observed.write_text(json.dumps(data) + "\n")

    with pytest.raises(ValueError, match="source_sha256"):
        summary.summarize(observed, previous)


def test_accepts_exact_producer_adapter_metadata_shape_without_entries_included(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)
    data = json.loads(observed.read_text())
    source_sha256 = data["endpoint_observability_adapter"]["source_sha256"]
    data["endpoint_observability_adapter"] = {
        "diagnostic_only": True,
        "source_sha256": source_sha256,
        "external_reference_used": False,
        "used_for_selection_or_graph_weights": False,
        "noise_assumption": "provisional normalized robust pixel/gyro/bias residuals",
        "all_factors": "nuisance-marginalized optimized robust Jacobian",
        "pixel_rows_only": "same solution and robust pixel weights; gyro/bias rows excluded",
    }
    observed.write_text(json.dumps(data) + "\n")

    report = summary.summarize(observed, previous)

    assert "entries_included" not in report["endpoint_observability_adapter"]


def test_plain_old_controls_as_new_is_refused(tmp_path):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=False)

    with pytest.raises(ValueError, match="endpoint_observability_adapter"):
        summary.summarize(observed, previous)


@pytest.mark.parametrize(
    "patch,match",
    [
        (lambda obs: obs.pop("endpoint_tracks"), "endpoint_tracks"),
        (lambda obs: obs.update({"endpoint_tracks": -1}), "endpoint_tracks"),
        (lambda obs: obs.update({"source_endpoint_common_tracks": 1.5}), "source_endpoint_common_tracks"),
        (lambda obs: obs.update({"endpoint_depth_m_quantiles": [0.1, 0.2]}), "depth"),
        (lambda obs: obs.update({"endpoint_depth_m_quantiles": [0.1, float('nan'), 0.3]}), "depth"),
        (lambda obs: obs.update({"endpoint_fx_baseline_over_depth_px_quantiles": [10.0, 0.0, 30.0]}), "disparity"),
    ],
)
def test_available_observability_requires_wellformed_tracks_and_leverage(tmp_path, patch, match):
    previous, _ = make_summary(tmp_path, obs=False)
    observed, _ = make_summary(tmp_path, obs=True)

    def mutate(case):
        for row in case["windows"]:
            if row.get("accepted"):
                patch(row["diagnostics"]["endpoint_observability"])
                break

    mutate_case(observed, "dev1", mutate)

    with pytest.raises(ValueError, match=match):
        summary.summarize(observed, previous)
