"""Two-pass UMI-only causal probe: keep edges, mask stereo-discordant pixels.

The input report flags native accepted edges with independently estimated D405
stereo inconsistency. This is deliberately not a production implementation.
"""
import json
import os
from pathlib import Path

import cv2
import numpy as np
import torch


def failed_directions(report):
    if report.get("schema") != "accepted_backend_edge_stereo_check_v1":
        raise ValueError("unexpected stereo report schema")
    return {
        (int(row["first_raw"]), int(row["second_raw"])):
        (not row["forward"].get("accepted", False),
         not row["reverse"].get("accepted", False))
        for row in report["results"] if not row["accepted"]
    }


def pnp_discordant_pixels(mapping, valid, quality, source_depth, source_shape,
                          target_shape, K, quality_threshold, sample_limit=5000):
    source_h, source_w = source_shape
    target_h, target_w = target_shape
    ids = np.flatnonzero(valid.reshape(-1) & (quality.reshape(-1) > quality_threshold))
    src = mapping.reshape(-1)[ids].astype(np.int64)
    inside = (src >= 0) & (src < source_h * source_w) & (ids < target_h * target_w)
    ids, src = ids[inside], src[inside]
    z = source_depth.reshape(-1)[src]
    has_depth = np.isfinite(z) & (z > 0)
    ids, src, z = ids[has_depth], src[has_depth], z[has_depth]
    if len(ids) < 100:
        return np.empty(0, dtype=np.int64), {"reason": "too_few_depth_matches", "count": len(ids)}
    points = np.column_stack(((src % source_w - K[0, 2]) * z / K[0, 0],
                              (src // source_w - K[1, 2]) * z / K[1, 1], z)).astype(np.float32)
    pixels = np.column_stack((ids % target_w, ids // target_w)).astype(np.float32)
    pick = np.arange(len(ids))
    if len(pick) > sample_limit:
        pick = np.linspace(0, len(pick)-1, sample_limit).astype(int)
    cv2.setRNGSeed(0)
    success, rvec, tvec, inliers = cv2.solvePnPRansac(
        points[pick], pixels[pick], np.asarray(K, dtype=np.float64), None,
        iterationsCount=100, reprojectionError=2.0, confidence=0.999,
        flags=cv2.SOLVEPNP_EPNP)
    if not success or inliers is None or len(inliers) < 100:
        return np.empty(0, dtype=np.int64), {"reason": "no_stable_pnp", "count": len(ids)}
    selected = pick[inliers[:, 0]]
    if hasattr(cv2, "solvePnPRefineLM"):
        rvec, tvec = cv2.solvePnPRefineLM(
            points[selected], pixels[selected], np.asarray(K, dtype=np.float64),
            None, rvec, tvec)
    projected, _ = cv2.projectPoints(points, rvec, tvec,
                                    np.asarray(K, dtype=np.float64), None)
    residual = np.linalg.norm(projected.reshape(-1, 2) - pixels, axis=1)
    reject = ids[residual > 4.0]
    return reject, {"reason": "pnp_mask", "count": len(ids),
                    "pnp_inliers": len(selected), "masked": len(reject)}


if os.environ.get("MAST3R_EDGE_FILTER_REPORT"):
    from mast3r_slam.global_opt import FactorGraph
    from mast3r_slam.stereo_depth import StereoDepthProvider

    report = json.loads(Path(os.environ["MAST3R_EDGE_FILTER_REPORT"]).read_text())
    failed = failed_directions(report)
    dataset = Path(os.environ["MAST3R_EDGE_FILTER_DATASET"])
    provider = StereoDepthProvider.from_dataset(dataset)
    if provider is None:
        raise ValueError("candidate requires onboard D405 stereo images")
    original_add = FactorGraph.add_factors
    depth_cache = {}

    def add_with_metric_inlier_filter(self, ii, jj, min_match_frac, is_reloc=False):
        start = len(self.ii)
        result = original_add(self, ii, jj, min_match_frac, is_reloc=is_reloc)
        for edge in range(start, len(self.ii)):
            first = self.frames[int(self.ii[edge])]
            second = self.frames[int(self.jj[edge])]
            pair = (int(first.frame_id), int(second.frame_id))
            if pair not in failed:
                continue
            K = self.K.detach().cpu().numpy()
            for bad, source, target, mapping, valid, quality, direction in (
                (failed[pair][0], first, second, self.idx_ii2jj[edge],
                 self.valid_match_j[edge], self.Q_ii2jj[edge], "forward"),
                (failed[pair][1], second, first, self.idx_jj2ii[edge],
                 self.valid_match_i[edge], self.Q_jj2ii[edge], "reverse"),
            ):
                if not bad:
                    continue
                source_shape = tuple(source.img.shape[-2:])
                target_shape = tuple(target.img.shape[-2:])
                key = (int(source.frame_id), source_shape)
                if key not in depth_cache:
                    depth_cache[key] = provider.get_depth(
                        dataset / f"{key[0]:010d}.png", source_shape)
                reject, diagnostic = pnp_discordant_pixels(
                    mapping.detach().cpu().numpy(), valid.detach().cpu().numpy(),
                    quality.detach().cpu().numpy(), depth_cache[key], source_shape,
                    target_shape, K, float(self.cfg["Q_conf"]))
                if len(reject):
                    valid.reshape(-1)[torch.as_tensor(reject, device=valid.device)] = False
                print(f"STEREO_INLIER_FILTER {pair[0]} {pair[1]} {direction} "
                      f"{json.dumps(diagnostic)}", flush=True)
        return result

    FactorGraph.add_factors = add_with_metric_inlier_filter
