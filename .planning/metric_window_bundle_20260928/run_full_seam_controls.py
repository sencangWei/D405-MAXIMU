"""Full paired-40 raw-frame seam controls adapter; no GT or selection."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_seam_window_controls as base


EXPECTED_CASES = {
    "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4",
    "fresh1", "fresh2", "fresh3", "fresh4",
}


def pair_windows(count: int):
    if count < 41:
        raise ValueError("recording too short for full-seam controls")
    return [np.arange(start, start + 41, 5) for start in range(0, count - 40, 40)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_metadata(case: dict, recording_count: int) -> dict:
    pairs = case.get("pairs", [])
    expected = [window.tolist() for window in pair_windows(recording_count)]
    pair_count = len(expected)
    if len(pairs) != pair_count:
        raise ValueError(f"{case.get('case')} pair count does not match recording frame count")
    actual = [pair.get("indices") for pair in pairs]
    if actual != expected:
        raise ValueError(f"{case.get('case')} pair indices do not match full-seam schedule")
    for pair, indices in zip(pairs, expected):
        raw = pair.get("raw_frame_indices")
        if raw != list(range(indices[0], indices[-1] + 1)):
            raise ValueError(f"{case.get('case')} raw frame indices do not match 41-frame pair")
    for left, right in zip(expected, expected[1:]):
        if left[-1] != right[0]:
            raise ValueError("adjacent pairs must share only endpoint")
        if set(left[:-1]) & set(right[1:]):
            raise ValueError("pair windows overlap beyond shared endpoint")
    if len(case.get("windows", [])) != 2 * pair_count:
        raise ValueError(f"{case.get('case')} joint window count must equal 2*pairs")
    if len(case.get("independent_windows", case.get("windows", []))) != 2 * pair_count:
        raise ValueError(f"{case.get('case')} independent window count must equal 2*pairs")
    for index, pair in enumerate(expected):
        expected_halves = (pair[:5], pair[4:])
        for offset, expected_indices in enumerate(expected_halves):
            row_index = 2 * index + offset
            if case["windows"][row_index].get("indices") != expected_indices:
                raise ValueError(f"{case.get('case')} joint window indices disagree with pair")
            if case.get("independent_windows", case.get("windows", []))[row_index].get("indices") != expected_indices:
                raise ValueError(f"{case.get('case')} independent window indices disagree with pair")
    raw_loaded = max(max(pair.get("raw_frame_indices", [0])) for pair in pairs) + 1 if pairs else 0
    last_endpoint = expected[-1][-1] if expected else -1
    return {
        "case": case.get("case"),
        "pair_count": pair_count,
        "joint_window_count": len(case.get("windows", [])),
        "independent_window_count": len(case.get("independent_windows", case.get("windows", []))),
        "recording_raw_frame_count": int(recording_count),
        "raw_count_loaded_pairs_prefix": int(raw_loaded),
        "last_endpoint_index": last_endpoint,
        "uncovered_tail_frames_after_last_endpoint": max(0, int(recording_count) - 1 - last_endpoint),
    }


def _annotate(summary_path: Path, adapter_hash: str, adapter_path: Path, *, independent: bool, recording_counts: list[int]) -> None:
    data = json.loads(summary_path.read_text())
    cases = data.get("cases", [])
    names = [case.get("case") for case in cases]
    if len(cases) != 10 or set(names) != EXPECTED_CASES:
        raise ValueError("full-seam summaries must contain exact ten known cases")
    if len(recording_counts) != len(cases):
        raise ValueError("recording frame counts missing for cases")
    case_rows = [_case_metadata(case, count) for case, count in zip(cases, recording_counts)]
    data.setdefault("source_sha256", {})[str(adapter_path)] = adapter_hash
    data["full_seam_adapter"] = {
        "source": str(adapter_path),
        "source_sha256": adapter_hash,
        "raw_interval_frames": 40,
        "ba_node_offsets": [0, 5, 10, 15, 20, 25, 30, 35, 40],
        "case_counts": case_rows,
        "pair_counts": {row["case"]: row["pair_count"] for row in case_rows},
        "leftover_tail_policy": "explicitly_uncovered_no_duplicate_partial_edge_no_tail_fallback",
        "correlated_paired_endpoints": True,
        "calibrated_covariance": False,
        "external_ground_truth_used": False,
        "production_selection": False,
        "independent_summary": bool(independent),
        "independent_flag_policy": "descriptive_only_same_candidate_inputs",
    }
    summary_path.write_text(json.dumps(data, indent=2) + "\n")


def main(argv=None):
    adapter_path = Path(__file__).resolve()
    before_hash = _sha(adapter_path)
    old_pair_windows = base.previous.pair_windows
    old_argv = sys.argv[:]
    recording_counts = []
    def capturing_pair_windows(count: int):
        recording_counts.append(int(count))
        return pair_windows(count)
    if argv is not None:
        sys.argv = [str(adapter_path)] + list(argv)
    try:
        base.previous.pair_windows = capturing_pair_windows
        result = base.main()
    finally:
        base.previous.pair_windows = old_pair_windows
        sys.argv = old_argv
    if _sha(adapter_path) != before_hash:
        raise ValueError("adapter source changed during controls")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args(argv)
    _annotate(args.output / "summary.json", before_hash, adapter_path, independent=False, recording_counts=recording_counts)
    _annotate(args.output / "independent_summary.json", before_hash, adapter_path, independent=True, recording_counts=recording_counts)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
