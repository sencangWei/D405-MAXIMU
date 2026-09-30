"""Run unchanged full-seam math on a Sep-30 one-case control summary.

The historical wrapper hard-codes exactly ten Sep-27 case names. This adapter
changes only that cohort assertion; schedule, factor gates and native solver
remain in the historical wrapper. It never loads external reference data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fuse_mast3r_seam_pair_windows as seam


def validate_one_case(summary: dict, *, independent: bool) -> None:
    if summary.get("external_reference_used") is not False:
        raise ValueError("external reference used")
    seam.verify_nonempty_hashes(summary.get("source_sha256", {}), "source")
    adapter = summary.get("full_seam_adapter", {})
    if adapter.get("raw_interval_frames") != 40 or adapter.get("correlated_paired_endpoints") is not True:
        raise ValueError("full-seam schedule contract changed")
    if adapter.get("calibrated_covariance") is not False or bool(adapter.get("independent_summary")) != independent:
        raise ValueError("full-seam mode contract changed")
    cases = summary.get("cases", [])
    counts = adapter.get("case_counts", [])
    if len(cases) != 1 or len(counts) != 1 or cases[0].get("case") != counts[0].get("case"):
        raise ValueError("expected exactly one matched new case")
    case = cases[0]
    count = counts[0]
    if case["case"] not in {"take2", "take4", "take5", "take6"}:
        raise ValueError("unexpected case")
    if count.get("pair_count") != 29 or count.get("joint_window_count") != 58:
        raise ValueError("full-seam count changed")
    if count.get("recording_raw_frame_count") not in (1199, 1200):
        raise ValueError("unexpected frame count")
    if count.get("last_endpoint_index") != 1160:
        raise ValueError("full-seam endpoint changed")
    seam.full_schedule_rows(case)


def main() -> int:
    seam.validate_summary_contract = validate_one_case
    return seam.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
