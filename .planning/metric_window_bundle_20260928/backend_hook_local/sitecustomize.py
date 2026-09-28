"""Add local keyframe match samples to the read-only long-edge observer."""
import hashlib
import json
import os
from pathlib import Path
import runpy

import numpy as np


if os.environ.get("MAST3R_BACKEND_TRACE"):
    other = Path(__file__).resolve().parents[1] / "backend_hook_matches/sitecustomize.py"
    namespace = runpy.run_path(str(other))
    sample = namespace["sample_correspondences"]
    from mast3r_slam.global_opt import FactorGraph

    original_add = FactorGraph.add_factors
    match_dir = Path(os.environ["MAST3R_BACKEND_MATCH_DIR"])
    manifest = match_dir / "local_match_manifest.jsonl"

    def add_with_local_matches(self, ii, jj, min_match_frac, is_reloc=False):
        first_new = len(self.ii)
        result = original_add(self, ii, jj, min_match_frac, is_reloc=is_reloc)
        threshold = float(self.cfg["Q_conf"])
        for item in range(first_new, len(self.ii)):
            first_kf, second_kf = int(self.ii[item]), int(self.jj[item])
            frame_i, frame_j = self.frames[first_kf], self.frames[second_kf]
            first_raw, second_raw = int(frame_i.frame_id), int(frame_j.frame_id)
            if not (1000 <= second_raw <= 1120 and second_raw-first_raw < 30):
                continue

            def array(value):
                return value.detach().cpu().numpy()

            forward_target, forward_source = sample(
                array(self.idx_ii2jj[item]), array(self.valid_match_j[item]),
                array(self.Q_ii2jj[item]), threshold)
            reverse_target, reverse_source = sample(
                array(self.idx_jj2ii[item]), array(self.valid_match_i[item]),
                array(self.Q_jj2ii[item]), threshold)
            target = match_dir / f"local_{first_raw}_{second_raw}_{item:04d}.npz"
            if target.exists():
                raise FileExistsError(f"refusing to overwrite {target}")
            np.savez_compressed(
                target, first_raw=np.int32(first_raw), second_raw=np.int32(second_raw),
                first_shape=np.asarray(frame_i.img.shape[-2:], dtype=np.int32),
                second_shape=np.asarray(frame_j.img.shape[-2:], dtype=np.int32),
                K=array(self.K), forward_target=forward_target,
                forward_source=forward_source, reverse_target=reverse_target,
                reverse_source=reverse_source)
            record = dict(first_raw=first_raw, second_raw=second_raw,
                          keyframes=[first_kf, second_kf],
                          forward_samples=len(forward_target),
                          reverse_samples=len(reverse_target), npz=str(target),
                          sha256=hashlib.sha256(target.read_bytes()).hexdigest())
            with manifest.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        return result

    FactorGraph.add_factors = add_with_local_matches
