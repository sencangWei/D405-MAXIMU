"""Save bounded accepted MASt3R graph correspondences without changing them."""
import hashlib
import json
import os
from pathlib import Path
import runpy

import numpy as np


def sample_correspondences(index_map, valid_match, confidence, threshold, limit=5000):
    """Uniform deterministic subset of valid, Q-gated target-to-source matches."""
    mapping = np.asarray(index_map).reshape(-1)
    valid = np.asarray(valid_match).reshape(-1).astype(bool)
    quality = np.asarray(confidence).reshape(-1)
    if mapping.shape != valid.shape or valid.shape != quality.shape or limit < 1:
        raise ValueError("match tensor shapes or sample limit invalid")
    target = np.flatnonzero(valid & (quality > threshold)
                               & (mapping >= 0) & (mapping < len(mapping)))
    if len(target) > limit:
        target = target[np.linspace(0, len(target) - 1, limit).astype(int)]
    return target.astype(np.int32), mapping[target].astype(np.int32)


if os.environ.get("MAST3R_BACKEND_TRACE"):
    # Reuse the already verified edge/pose observer; this file only adds
    # sampled correspondences after the original add_factors call has returned.
    previous_hook = Path(__file__).resolve().parents[1] / "backend_hook/sitecustomize.py"
    runpy.run_path(str(previous_hook))
    from mast3r_slam.global_opt import FactorGraph

    match_dir = Path(os.environ["MAST3R_BACKEND_MATCH_DIR"])
    if not match_dir.is_dir():
        raise ValueError("precreated match capture directory required")
    original_add = FactorGraph.add_factors
    manifest = match_dir / "match_manifest.jsonl"

    def add_with_matches(self, ii, jj, min_match_frac, is_reloc=False):
        first_new = len(self.ii)
        result = original_add(self, ii, jj, min_match_frac, is_reloc=is_reloc)
        threshold = float(self.cfg["Q_conf"])
        for item in range(first_new, len(self.ii)):
            first_kf, second_kf = int(self.ii[item]), int(self.jj[item])
            frame_i, frame_j = self.frames[first_kf], self.frames[second_kf]
            first_raw, second_raw = int(frame_i.frame_id), int(frame_j.frame_id)
            if second_raw - first_raw < 30:
                continue
            # Stored forward map is target j -> source i, as in the native
            # metric_loop_gate. Reverse map is target i -> source j.
            def array(value):
                return value.detach().cpu().numpy()

            forward_target, forward_source = sample_correspondences(
                array(self.idx_ii2jj[item]), array(self.valid_match_j[item]),
                array(self.Q_ii2jj[item]), threshold)
            reverse_target, reverse_source = sample_correspondences(
                array(self.idx_jj2ii[item]), array(self.valid_match_i[item]),
                array(self.Q_jj2ii[item]), threshold)
            target = match_dir / f"edge_{first_raw}_{second_raw}_{item:04d}.npz"
            if target.exists():
                raise FileExistsError(f"refusing to overwrite match capture {target}")
            np.savez_compressed(
                target, first_raw=np.int32(first_raw), second_raw=np.int32(second_raw),
                first_shape=np.asarray(frame_i.img.shape[-2:], dtype=np.int32),
                second_shape=np.asarray(frame_j.img.shape[-2:], dtype=np.int32),
                K=array(self.K),
                forward_target=forward_target, forward_source=forward_source,
                reverse_target=reverse_target, reverse_source=reverse_source,
            )
            record = {"first_raw": first_raw, "second_raw": second_raw,
                      "keyframes": [first_kf, second_kf],
                      "forward_samples": len(forward_target),
                      "reverse_samples": len(reverse_target),
                      "npz": str(target),
                      "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
            with manifest.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        return result

    FactorGraph.add_factors = add_with_matches
