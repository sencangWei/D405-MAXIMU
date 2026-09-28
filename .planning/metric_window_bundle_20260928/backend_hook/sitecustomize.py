"""Observation-only MASt3R backend trace loaded by the spawned worker process."""
import json
import os
from pathlib import Path

TRACE_PATH = os.environ.get("MAST3R_BACKEND_TRACE")
if TRACE_PATH:
    from mast3r_slam.global_opt import FactorGraph

    _path = Path(TRACE_PATH)
    _original_add = FactorGraph.add_factors
    _original_calib = FactorGraph.solve_GN_calib
    _original_rays = FactorGraph.solve_GN_rays

    def _write(record):
        record["pid"] = os.getpid()
        with _path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")

    def _frame_id(graph, index):
        return int(graph.frames[int(index)].frame_id)

    def _poses(graph):
        indices = graph.get_unique_kf_idx().detach().cpu().tolist()
        return [(int(index), _frame_id(graph, index),
                 graph.frames[int(index)].T_WC.data.detach().cpu().reshape(-1).tolist())
                for index in indices]

    def _add_factors(self, ii, jj, min_match_frac, is_reloc=False):
        requested = [[int(i), int(j), _frame_id(self, i), _frame_id(self, j)]
                     for i, j in zip(ii, jj)]
        old_count = len(self.ii)
        outcome = _original_add(self, ii, jj, min_match_frac, is_reloc=is_reloc)
        new_ii = self.ii[old_count:].detach().cpu().tolist()
        new_jj = self.jj[old_count:].detach().cpu().tolist()
        accepted = []
        for offset, (i, j) in enumerate(zip(new_ii, new_jj)):
            item = old_count + offset
            accepted.append({
                "keyframes": [int(i), int(j)],
                "source_frames": [_frame_id(self, i), _frame_id(self, j)],
                "valid_match_fraction_i": float(self.valid_match_i[item].float().mean()),
                "valid_match_fraction_j": float(self.valid_match_j[item].float().mean()),
                "confidence_median_i": float(self.Q_jj2ii[item].median()),
                "confidence_median_j": float(self.Q_ii2jj[item].median()),
            })
        _write({"event": "add_factors", "requested": requested,
                "accepted": accepted, "is_relocalization": bool(is_reloc),
                "minimum_match_fraction": float(min_match_frac)})
        return outcome

    def _solve(original, mode, self):
        before = _poses(self)
        outcome = original(self)
        after = _poses(self)
        if [row[:2] for row in before] != [row[:2] for row in after]:
            raise ValueError("backend keyframe set changed during solve")
        _write({"event": "solve", "mode": mode, "before": before,
                "after": after, "factor_count": int(len(self.ii))})
        return outcome

    def _solve_calib(self):
        return _solve(_original_calib, "calibrated", self)

    def _solve_rays(self):
        return _solve(_original_rays, "rays", self)

    FactorGraph.add_factors = _add_factors
    FactorGraph.solve_GN_calib = _solve_calib
    FactorGraph.solve_GN_rays = _solve_rays
