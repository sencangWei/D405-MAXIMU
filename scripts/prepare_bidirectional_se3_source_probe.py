#!/usr/bin/env python3
"""Source-only native SE3 experiment over normally admitted per-eye reports.

The existing scalar bidirectional acceptance policy is unchanged. Preserve
complete raw forward/reverse results; replace accepted native geometry with
their symmetric SE3 midpoint. This does not launch any backend or scorer.
"""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import prepare_independent_ir_recovery_probe as source  # noqa: E402
from ego_vio.vio.bidirectional_se3_motion import combine_bidirectional_se3_motion  # noqa: E402


def candidate_rows(reports, paths_by_report, times):
    """Replay existing accepted and eligible rejected rows with the same inputs."""
    rows = []
    for report in reports:
        path = paths_by_report[report["report_path"]]
        for index, row in enumerate(report.get("observations", [])):
            if row.get("reason") == source.LOW_EXCITATION_REASON:
                continue
            if row.get("accepted") is not True and row.get("accepted") is not False:
                raise ValueError("source row acceptance must be explicit")
            first, second = source.accepted_row_indices(row, times, path)
            rows.append({"report": report, "path": path, "index": index,
                         "row": row, "first": first, "second": second})
    return rows


def combine_native_geometry(forward, reverse, original_combine):
    baseline = original_combine(forward, reverse)
    if baseline.get("accepted") is not True:
        return baseline
    geometry, diagnostic = combine_bidirectional_se3_motion(forward, reverse)
    diagnostic["scale_source"] = "unchanged_native_scalar_bidirectional_baseline"
    if geometry.get("accepted") is not True:
        return {
            **deepcopy(baseline), "accepted": False, "reason": geometry["reason"],
            "bidirectional_se3": diagnostic,
            "scalar_bidirectional_baseline": deepcopy(baseline),
            "native_scalar_acceptance_unchanged": True,
            "scale_source": "unchanged_native_scalar_bidirectional_baseline",
            "raw_confidence_fields_source": "unchanged_native_scalar_bidirectional_baseline",
        }
    forward_mast3r_distance = float(forward["mast3r_distance"])
    reverse_mast3r_distance = float(reverse["mast3r_distance"])
    if (not np.isfinite(forward_mast3r_distance) or forward_mast3r_distance <= 0
            or not np.isfinite(reverse_mast3r_distance)
            or not np.isclose(forward_mast3r_distance, reverse_mast3r_distance, rtol=1e-6, atol=1e-12)):
        raise ValueError("native forward/reverse mast3r_distance mismatch")
    distance = float(np.linalg.norm(geometry["metric_displacement_camera_i_m"]))
    if not np.isfinite(distance) or distance <= np.finfo(float).eps:
        return {
            **deepcopy(baseline), "accepted": False,
            "reason": "bidirectional_se3_zero_motion",
            "bidirectional_se3": diagnostic,
            "scalar_bidirectional_baseline": deepcopy(baseline),
            "native_scalar_acceptance_unchanged": True,
            "scale_source": "unchanged_native_scalar_bidirectional_baseline",
            "raw_confidence_fields_source": "unchanged_native_scalar_bidirectional_baseline",
        }
    result = deepcopy(baseline)
    result.update(geometry)
    result.update(
        metric_distance_m=distance,
        bidirectional_se3=diagnostic,
        scalar_bidirectional_baseline=deepcopy(baseline),
        scale_source="unchanged_native_scalar_bidirectional_baseline",
        raw_confidence_fields_source="unchanged_native_scalar_bidirectional_baseline",
        pnp_reprojection_source="raw_forward_before_se3_midpoint",
        rotation_error_source="raw_forward_before_se3_midpoint",
        native_scalar_acceptance_unchanged=True,
    )
    return result


def main(argv=None):
    original_combine = source.diag.combine_bidirectional_native
    original_snapshot = source.snapshot
    original_cache = source.DisparityCache

    def snapshot(paths):
        guarded = dict(paths)
        guarded.update(
            bidirectional_geometry_producer=Path(__file__).resolve(),
            bidirectional_geometry_helper=ROOT / "ego_vio/vio/bidirectional_se3_motion.py",
        )
        return original_snapshot(guarded)

    def combine(forward, reverse):
        return combine_native_geometry(forward, reverse, original_combine)

    with ExitStack() as stack:
        for owner, name, value in (
            (source, "candidate_rows", candidate_rows),
            (source, "snapshot", snapshot),
            # Current40s takes have1199frames; cache avoids redoing identical
            # SGBM work across short/long/multisecond reports. Outputs unchanged.
            (source, "DisparityCache", lambda: original_cache(max_entries=2048)),
            (source.diag, "combine_bidirectional_native", combine),
            (source.diag, "_result_summary", deepcopy),
            (source, "SCHEMA", "umi_bidirectional_se3_source_preflight_v1"),
            (source, "APPENDIX_SCHEMA", "umi_bidirectional_native_geometry_appendix_v1"),
        ):
            stack.enter_context(patch.object(owner, name, value))
        return source.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
