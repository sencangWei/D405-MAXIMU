"""Summarize stereo-window observability diagnostics without evaluation/GT."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


EXPECTED_CASES = {
    "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4",
    "fresh1", "fresh2", "fresh3", "fresh4",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_hashes(summary: dict, label: str) -> None:
    for section in ("source_sha256",):
        if not summary.get(section):
            raise ValueError(f"{label} {section} must be nonempty")
        for raw, expected in summary.get(section, {}).items():
            path = Path(raw)
            if not path.exists() or sha256(path) != expected:
                raise ValueError(f"{label} {section} hash changed: {path}")
    for case in summary.get("cases", []):
        if not case.get("input_sha256"):
            raise ValueError(f"{label} input_sha256 must be nonempty")
        if not case.get("decoded_grayscale_frame_sha256"):
            raise ValueError(f"{label} decoded hashes must be nonempty")
        for raw, expected in case.get("input_sha256", {}).items():
            path = Path(raw)
            if not path.exists() or sha256(path) != expected:
                raise ValueError(f"{label} input hash changed: {path}")


def cases_by_name(summary: dict) -> dict:
    if summary.get("external_reference_used") is not False:
        raise ValueError("summary external_reference_used must be false")
    cases = summary.get("cases", [])
    names = [case.get("case") for case in cases]
    if len(cases) != 10 or len(set(names)) != 10:
        raise ValueError("summary must contain exact ten unique case names")
    if set(names) != EXPECTED_CASES:
        raise ValueError("summary must contain exact case names")
    return {case["case"]: case for case in cases}


def validate_observability_adapter(summary: dict) -> dict:
    adapter = summary.get("endpoint_observability_adapter")
    if not isinstance(adapter, dict):
        raise ValueError("endpoint_observability_adapter metadata missing")
    if adapter.get("diagnostic_only") is not True:
        raise ValueError("endpoint_observability_adapter diagnostic_only must be true")
    if adapter.get("external_reference_used") is not False:
        raise ValueError("endpoint_observability_adapter external_reference_used must be false")
    if adapter.get("used_for_selection_or_graph_weights") is not False:
        raise ValueError("endpoint_observability_adapter must not be used for selection or graph weights")
    if not adapter.get("source_sha256"):
        raise ValueError("endpoint_observability_adapter source_sha256 must be nonempty")
    summary_sources = summary.get("source_sha256", {})
    for raw, expected in adapter.get("source_sha256", {}).items():
        if raw not in summary_sources or summary_sources[raw] != expected:
            raise ValueError("endpoint_observability_adapter source_sha256 not represented in summary source map")
    return adapter


def validate_windows(case: dict) -> None:
    rows = case.get("windows", [])
    if len(rows) != 59:
        raise ValueError(f"{case.get('case')} must contain 59 fixed windows")
    previous_start = -1
    for expected, row in enumerate(rows, 1):
        if int(row.get("window", -1)) != expected:
            raise ValueError("window ids must be sequential")
        indices = row.get("indices")
        expected_indices = [20 * (expected - 1) + offset for offset in (0, 5, 10, 15, 20)]
        if (
            not isinstance(indices, list)
            or len(indices) != 5
            or any(not isinstance(value, int) for value in indices)
            or any(a >= b for a, b in zip(indices, indices[1:]))
        ):
            raise ValueError("window indices must be five increasing integers")
        if indices != expected_indices:
            raise ValueError("window indices do not match frozen schedule")
        if indices[0] <= previous_start:
            raise ValueError("window starts must increase")
        previous_start = indices[0]


def compare_identity(new_case: dict, old_case: dict, endpoint_bound_m: float = 1e-10) -> tuple[float, bool]:
    validate_windows(new_case)
    validate_windows(old_case)
    max_endpoint_diff = 0.0
    for new, old in zip(new_case["windows"], old_case["windows"]):
        for key in ("window", "indices", "accepted", "reason", "admission"):
            if new.get(key) != old.get(key):
                raise ValueError(f"{new_case['case']} solver identity changed: {key}")
        if new.get("accepted"):
            new_initial = np.asarray(new.get("initial_endpoint_m"), dtype=float)
            old_initial = np.asarray(old.get("initial_endpoint_m"), dtype=float)
            if new_initial.shape != (3,) or old_initial.shape != (3,):
                raise ValueError("accepted initial endpoint must be xyz")
            if not np.all(np.isfinite(new_initial)) or not np.all(np.isfinite(old_initial)):
                raise ValueError("accepted initial endpoint contains non-finite values")
            initial_diff = float(np.max(np.abs(new_initial - old_initial)))
            if initial_diff > 1e-10:
                raise ValueError(f"{new_case['case']} initial endpoint changed by {initial_diff} m")
            new_endpoint = np.asarray(new.get("endpoint_m"), dtype=float)
            old_endpoint = np.asarray(old.get("endpoint_m"), dtype=float)
            if new_endpoint.shape != (3,) or old_endpoint.shape != (3,):
                raise ValueError("accepted endpoint must be xyz")
            if not np.all(np.isfinite(new_endpoint)) or not np.all(np.isfinite(old_endpoint)):
                raise ValueError("accepted endpoint contains non-finite values")
            diff = float(np.max(np.abs(new_endpoint - old_endpoint)))
            max_endpoint_diff = max(max_endpoint_diff, diff)
            if diff > endpoint_bound_m:
                if endpoint_bound_m > 1e-10:
                    raise ValueError(f"{new_case['case']} endpoint changed by {diff} m beyond numeric reproducibility bound")
                raise ValueError(f"{new_case['case']} endpoint changed by {diff} m")
    decoded_equal = (
        new_case.get("decoded_grayscale_frame_sha256")
        == old_case.get("decoded_grayscale_frame_sha256")
    )
    if not decoded_equal:
        raise ValueError(f"{new_case['case']} decoded image hashes changed")
    return max_endpoint_diff, decoded_equal


def accepted_first_windows(summary: dict) -> dict:
    cases = cases_by_name(summary)
    rows = {}
    for name, case in cases.items():
        windows = case.get("windows", [])
        if len(windows) != 1:
            raise ValueError("numeric runtime proof must contain one first window per case")
        row = windows[0]
        if row.get("window") != 1 or row.get("indices") != [0, 5, 10, 15, 20]:
            raise ValueError("numeric runtime proof must use first window [0,5,10,15,20]")
        rows[name] = row
    return rows


def endpoint_delta(a: dict, b: dict) -> float:
    return float(np.max(np.abs(np.asarray(a["endpoint_m"], dtype=float) - np.asarray(b["endpoint_m"], dtype=float))))


def validate_same_input_pair(row: dict) -> None:
    proof = row.get("diagnostics", {}).get("same_input_paired_proof")
    if not isinstance(proof, dict):
        raise ValueError("numeric runtime proof missing same_input_paired_proof")
    if proof.get("accepted_reason_equal") is not True:
        raise ValueError("numeric runtime proof accepted/reason mismatch")
    for field in ("centers", "landmarks", "gyro_bias"):
        if proof.get(f"{field}_bit_equal") is not True:
            raise ValueError(f"numeric runtime proof {field} not bit equal")
        if float(proof.get(f"{field}_max_absolute_difference", float("inf"))) != 0.0:
            raise ValueError(f"numeric runtime proof {field} difference nonzero")
    original = proof.get("original", {})
    instrumented = proof.get("instrumented", {})
    for key in ("nfev", "cost", "solver_status", "endpoint_m"):
        if original.get(key) != instrumented.get(key):
            raise ValueError(f"numeric runtime proof original/instrumented {key} mismatch")
    if original.get("endpoint_m") != row.get("endpoint_m"):
        raise ValueError("numeric runtime proof endpoint does not match row endpoint")


def validate_numeric_runtime_summaries(paths: list[Path], new_summary: dict, old_summary: dict) -> dict:
    if len(paths) != 2:
        raise ValueError("numeric runtime proof requires exactly two summaries")
    proofs = []
    versions = []
    for path in paths:
        data = load_json(path)
        verify_hashes(data, "numeric runtime proof")
        rows = accepted_first_windows(data)
        for row in rows.values():
            validate_same_input_pair(row)
        runtime = data.get("runtime", {})
        numpy_version = runtime.get("numpy")
        if not numpy_version:
            raise ValueError("numeric runtime proof missing numpy version")
        versions.append(numpy_version)
        proofs.append({"path": path, "data": data, "rows": rows, "runtime": runtime})
    if len(set(versions)) != 2:
        raise ValueError("numeric runtime proof requires distinct numpy versions")

    old_rows = {name: case["windows"][0] for name, case in cases_by_name(old_summary).items()}
    new_rows = {name: case["windows"][0] for name, case in cases_by_name(new_summary).items()}
    old_cases = cases_by_name(old_summary)
    new_cases = cases_by_name(new_summary)
    for proof in proofs:
        proof_cases = cases_by_name(proof["data"])
        for name, row in proof["rows"].items():
            proof_case = proof_cases[name]
            old_case = old_cases[name]
            new_case = new_cases[name]
            if (
                proof_case.get("input_sha256") != old_case.get("input_sha256")
                or proof_case.get("input_sha256") != new_case.get("input_sha256")
            ):
                raise ValueError("numeric runtime proof input_sha256 mismatch")
            proof_decoded = proof_case.get("decoded_grayscale_frame_sha256")
            old_decoded = old_case.get("decoded_grayscale_frame_sha256")
            new_decoded = new_case.get("decoded_grayscale_frame_sha256")
            if not isinstance(proof_decoded, dict) or not proof_decoded:
                raise ValueError("numeric runtime proof decoded hashes must be nonempty")
            for key, value in proof_decoded.items():
                if old_decoded.get(key) != value or new_decoded.get(key) != value:
                    raise ValueError("numeric runtime proof decoded hash mismatch")
            for reference in (old_rows[name], new_rows[name]):
                for key in ("window", "indices", "accepted", "reason", "admission", "initial_endpoint_m"):
                    if row.get(key) != reference.get(key):
                        raise ValueError("numeric runtime proof admission or initial endpoint changed")
    assignments = []
    for proof in proofs:
        max_old = max(endpoint_delta(proof["rows"][name], old_rows[name]) for name in old_rows)
        max_new = max(endpoint_delta(proof["rows"][name], new_rows[name]) for name in new_rows)
        assignments.append((max_old, max_new))
    old_matches = [index for index, (old, _) in enumerate(assignments) if old <= 1e-10]
    new_matches = [index for index, (_, new) in enumerate(assignments) if new <= 1e-10]
    if len(old_matches) != 1 or len(new_matches) != 1 or old_matches[0] == new_matches[0]:
        raise ValueError("numeric runtime proof does not match old/new first-window endpoints")
    return {
        "label": "diagnostic_numerical_reproducibility_boundary",
        "strict_bound_m": 1e-10,
        "numeric_bound_m": 1e-6,
        "proofs": [
            {
                "path": str(proof["path"].resolve()),
                "sha256": sha256(proof["path"]),
                "runtime": proof["runtime"],
            }
            for proof in proofs
        ],
        "numpy_versions": versions,
        "old_match_max_endpoint_delta_m": assignments[old_matches[0]][0],
        "new_match_max_endpoint_delta_m": assignments[new_matches[0]][1],
    }


def q(values: list[float]) -> dict | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    return {
        "p10": float(np.percentile(arr, 10)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
    }


def factor_stats(values: list[dict], name: str) -> dict:
    ranks = []
    weak = []
    unavailable_rank_deficient = 0
    unavailable_other = 0
    for item in values:
        factor = item.get(name)
        if not isinstance(factor, dict):
            unavailable_other += 1
            continue
        if "endpoint_rank" not in factor:
            raise ValueError("endpoint rank missing")
        rank = factor["endpoint_rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or not 0 <= rank <= 3:
            raise ValueError("endpoint rank must be integer 0..3")
        ranks.append(float(rank))
        response = factor.get("weak_response_mm_per_unit_normalized_residual")
        rank_deficient = bool(factor.get("rank_deficient"))
        if rank_deficient != (rank < 3):
            raise ValueError("rank_deficient must match endpoint rank")
        if response is None:
            if rank < 3:
                unavailable_rank_deficient += 1
            else:
                raise ValueError("weak response cannot be None for rank 3")
        else:
            response = float(response)
            if rank < 3:
                raise ValueError("rank deficient factor cannot have finite weak response")
            if not np.isfinite(response) or response <= 0.0:
                raise ValueError("weak response must be positive finite")
            weak.append(response)
    return {
        "endpoint_rank": q(ranks),
        "weak_response_mm_per_unit_normalized_residual": {
            "quantiles": q(weak),
            "unavailable_rank_deficient": unavailable_rank_deficient,
            "unavailable_other": unavailable_other,
        },
    }


def require_nonnegative_int(obs: dict, key: str) -> int:
    if key not in obs:
        raise ValueError(f"{key} missing")
    value = obs[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be nonnegative integer")
    return value


def require_positive_triplet(obs: dict, key: str, label: str) -> list[float]:
    values = obs.get(key)
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError(f"{label} leverage quantiles must be three values")
    out = []
    for value in values:
        value = float(value)
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError(f"{label} leverage quantiles must be positive finite")
        out.append(value)
    return out


def validated_available_observability(obs: dict) -> dict | None:
    if not isinstance(obs, dict) or obs.get("available") is False:
        return None
    endpoint_tracks = require_nonnegative_int(obs, "endpoint_tracks")
    common_tracks = require_nonnegative_int(obs, "source_endpoint_common_tracks")
    depth_q = require_positive_triplet(obs, "endpoint_depth_m_quantiles", "depth")
    disparity_q = require_positive_triplet(
        obs, "endpoint_fx_baseline_over_depth_px_quantiles", "disparity"
    )
    return {
        "obs": obs,
        "endpoint_tracks": endpoint_tracks,
        "source_endpoint_common_tracks": common_tracks,
        "depth_q": depth_q,
        "disparity_q": disparity_q,
    }


def aggregate_case(case: dict) -> dict:
    observability = []
    unavailable = 0
    endpoint_tracks = []
    common_tracks = []
    depths = [[], [], []]
    disparity = [[], [], []]
    for row in case["windows"]:
        if not row.get("accepted"):
            continue
        obs = row.get("diagnostics", {}).get("endpoint_observability")
        validated = validated_available_observability(obs)
        if validated is None:
            unavailable += 1
            continue
        obs = validated["obs"]
        observability.append(obs)
        endpoint_tracks.append(float(validated["endpoint_tracks"]))
        common_tracks.append(float(validated["source_endpoint_common_tracks"]))
        for index, value in enumerate(validated["depth_q"]):
            depths[index].append(float(value))
        for index, value in enumerate(validated["disparity_q"]):
            disparity[index].append(float(value))
    return {
        "accepted_windows": int(sum(1 for row in case["windows"] if row.get("accepted"))),
        "counts_diagnostic_unavailable": unavailable,
        "all_factors": factor_stats(observability, "all_factors"),
        "pixel_rows_only": factor_stats(observability, "pixel_rows_only"),
        "endpoint_tracks": q(endpoint_tracks),
        "source_endpoint_common_tracks": q(common_tracks),
        "endpoint_depth_m_quantiles": {
            "distribution_of_window_quantiles": {
                "q0": q(depths[0]), "q1": q(depths[1]), "q2": q(depths[2])
            }
        },
        "endpoint_fx_baseline_over_depth_px_quantiles": {
            "distribution_of_window_quantiles": {
                "q0": q(disparity[0]), "q1": q(disparity[1]), "q2": q(disparity[2])
            }
        },
    }


def summarize(observability_summary: Path, previous_summary: Path, *, numeric_runtime_summaries=None) -> dict:
    new_summary = load_json(observability_summary)
    old_summary = load_json(previous_summary)
    verify_hashes(new_summary, "observability")
    verify_hashes(old_summary, "previous")
    adapter = validate_observability_adapter(new_summary)
    new_cases = cases_by_name(new_summary)
    old_cases = cases_by_name(old_summary)
    if set(new_cases) != set(old_cases):
        raise ValueError("case names changed between summaries")
    proof = None
    endpoint_bound = 1e-10
    if numeric_runtime_summaries is not None:
        proof = validate_numeric_runtime_summaries(
            [Path(path) for path in numeric_runtime_summaries], new_summary, old_summary
        )
        endpoint_bound = proof["numeric_bound_m"]
    max_endpoint = 0.0
    per_case = {}
    for name in sorted(new_cases):
        endpoint_diff, _ = compare_identity(new_cases[name], old_cases[name], endpoint_bound)
        max_endpoint = max(max_endpoint, endpoint_diff)
        per_case[name] = aggregate_case(new_cases[name])
    if max_endpoint > endpoint_bound:
        raise ValueError("endpoint difference exceeds numeric reproducibility bound")
    report = {
        "schema": "stereo_window_observability_summary_v1",
        "diagnostic_only": True,
        "external_ground_truth_read": False,
        "no_weights_or_trajectory_selection": True,
        "accuracy_claim": False,
        "cases": len(new_cases),
        "windows_per_case": 59,
        "source_sha256": {str(Path(__file__).resolve()): sha256(Path(__file__).resolve())},
        "input_summaries": {
            "observability": {
                "path": str(observability_summary.resolve()),
                "sha256": sha256(observability_summary),
            },
            "previous": {
                "path": str(previous_summary.resolve()),
                "sha256": sha256(previous_summary),
            },
        },
        "endpoint_observability_adapter": adapter,
        "identity": {
            "accepted_reason_indices_unchanged": True,
            "endpoint_exact_identity": max_endpoint <= 1e-10,
            "max_endpoint_difference_m": max_endpoint,
            "decoded_image_hash_identity": True,
            "endpoint_tolerance_m": 1e-10,
            "strict_bound_m": 1e-10,
            "numeric_bound_m": endpoint_bound if proof is not None else None,
        },
        "per_case": per_case,
        "counts_diagnostic_unavailable": int(
            sum(item["counts_diagnostic_unavailable"] for item in per_case.values())
        ),
        "interpretation": "raw local observability diagnostics only; no GT, no accuracy claim",
    }
    if proof is not None:
        report["numeric_runtime_proof"] = proof
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observability-summary", type=Path, required=True)
    parser.add_argument("--previous-summary", type=Path, required=True)
    parser.add_argument("--numeric-runtime-summary", type=Path, action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError(f"refuse to overwrite output: {args.output}")
    report = summarize(
        args.observability_summary,
        args.previous_summary,
        numeric_runtime_summaries=args.numeric_runtime_summary,
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
