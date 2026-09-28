"""Causal probe: drop only stereo-rejected accepted edges from a frozen replay.

This is a two-pass diagnostic, not a production gate. The report comes solely
from onboard stereo and accepted MASt3R matches, never external ground truth.
"""
import json
import os
from pathlib import Path


def rejected_pairs(report):
    if report.get("schema") == "selected_backend_edge_causal_probe_v1":
        if report.get("external_reference_used") is not False:
            raise ValueError("selected edge probe must exclude external reference")
        return {(int(row["first_raw"]), int(row["second_raw"]))
                for row in report["selected_pairs"]}
    if report.get("schema") != "accepted_backend_edge_stereo_check_v1":
        raise ValueError("unexpected edge report schema")
    return {(int(row["first_raw"]), int(row["second_raw"]))
            for row in report["results"] if not row["accepted"]}


if os.environ.get("MAST3R_EDGE_DROP_REPORT"):
    from mast3r_slam.global_opt import FactorGraph

    report_path = Path(os.environ["MAST3R_EDGE_DROP_REPORT"])
    failed = rejected_pairs(json.loads(report_path.read_text()))
    original_add = FactorGraph.add_factors
    dropped_count = 0

    def add_with_stereo_drop(self, ii, jj, min_match_frac, is_reloc=False):
        global dropped_count
        first_new = len(self.ii)
        original_result = original_add(self, ii, jj, min_match_frac,
                                       is_reloc=is_reloc)
        if len(self.ii) == first_new:
            return original_result
        keep = [True] * len(self.ii)
        for index in range(first_new, len(self.ii)):
            frame_i = self.frames[int(self.ii[index])]
            frame_j = self.frames[int(self.jj[index])]
            pair = (int(frame_i.frame_id), int(frame_j.frame_id))
            if pair in failed:
                keep[index] = False
                dropped_count += 1
                print(f"STEREO_EDGE_DROP {pair[0]} {pair[1]}", flush=True)
        if all(keep):
            return original_result
        for name in ("ii", "jj", "idx_ii2jj", "idx_jj2ii", "valid_match_j",
                     "valid_match_i", "Q_ii2jj", "Q_jj2ii"):
            value = getattr(self, name)
            if len(value) != len(keep):
                raise ValueError(f"factor storage length mismatch: {name}")
            setattr(self, name, value[keep])
        return any(keep[first_new:])

    FactorGraph.add_factors = add_with_stereo_drop
