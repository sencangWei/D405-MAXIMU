#!/usr/bin/env python3
"""Inspect frozen metric-input to graph-output corrections without external GT."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_slam_ground_truth import load_trajectory  # noqa: E402

JOINT = ROOT / "reports/metric_window_bundle_20260928/seam_graph_full_ten_v1/joint"
OUTPUT = ROOT / "reports/metric_window_bundle_20260928/graph_correction_stereo_census_v1.json"
CASES = ("dev1", "dev2", "fresh1", "fresh2", "fresh3", "fresh4",
         "heldout1", "heldout2", "heldout3", "heldout4")
WINDOWS = {"before": (1000, 1052), "bad_index_band": (1053, 1078),
           "after": (1079, 1120)}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def correction_stats(input_times, input_positions, output_times, output_positions):
    """Require exact frame correspondence; never compare shifted CSV row indices."""
    if (input_positions.shape != output_positions.shape
            or input_times.shape != output_times.shape
            or not np.allclose(input_times, output_times, atol=1e-6, rtol=0)
            or not np.isfinite(input_positions).all()
            or not np.isfinite(output_positions).all()):
        raise ValueError("metric input and graph output are not frame-matched")
    correction_mm = 1000 * (output_positions - input_positions)
    norms_mm = np.linalg.norm(correction_mm, axis=1)
    result = {"frames": len(input_times), "max_mm": float(np.max(norms_mm)),
              "median_mm": float(np.median(norms_mm))}
    for label, (first, last) in WINDOWS.items():
        if last >= len(norms_mm):
            raise ValueError("frozen diagnostic window exceeds trajectory")
        local = correction_mm[first:last + 1]
        result[label] = {
            "count": len(local),
            "max_norm_mm": float(np.max(norms_mm[first:last + 1])),
            "median_norm_mm": float(np.median(norms_mm[first:last + 1])),
            "mean_vector_mm": np.mean(local, axis=0).tolist(),
            "endpoint_change_vector_mm": (local[-1] - local[0]).tolist(),
        }
    return result


def stereo_stats(observations, positions_in, quaternions_in,
                 positions_out, quaternions_out):
    """Evaluate accepted measured edges in each frozen stage's camera frame."""
    accepted = [item for item in observations if item.get("accepted")
                and "metric_displacement_camera_i_m" in item]
    if not accepted:
        return {"count": 0, "windows": {}}
    first = np.asarray([int(item["first_index"]) for item in accepted])
    second = np.asarray([int(item["second_index"]) for item in accepted])
    if np.any(first < 0) or np.any(second <= first) or np.any(second >= len(positions_in)):
        raise ValueError("stereo observation indices outside metric trajectory")
    measurement = np.asarray([item["metric_displacement_camera_i_m"]
                              for item in accepted], dtype=float)
    if measurement.shape != (len(accepted), 3) or not np.isfinite(measurement).all():
        raise ValueError("invalid stereo metric displacement")
    before = positions_in[second] - positions_in[first]
    before -= Rotation.from_quat(quaternions_in[first]).apply(measurement)
    after = positions_out[second] - positions_out[first]
    after -= Rotation.from_quat(quaternions_out[first]).apply(measurement)
    midpoint = 0.5 * (first + second)
    before_mm, after_mm = 1000 * before, 1000 * after
    windows = {}
    for name, (start, end) in WINDOWS.items():
        selected = (midpoint >= start) & (midpoint <= end)
        if not np.any(selected):
            windows[name] = {"count": 0}
            continue
        windows[name] = {
            "count": int(np.count_nonzero(selected)),
            "before_median_norm_mm": float(np.median(np.linalg.norm(before_mm[selected], axis=1))),
            "after_median_norm_mm": float(np.median(np.linalg.norm(after_mm[selected], axis=1))),
            "before_mean_vector_mm": np.mean(before_mm[selected], axis=0).tolist(),
            "after_mean_vector_mm": np.mean(after_mm[selected], axis=0).tolist(),
        }
    return {"count": len(accepted), "windows": windows}


def main():
    if OUTPUT.exists():
        raise FileExistsError("refusing to overwrite frozen diagnostic")
    hashes = {str(Path(__file__).resolve()): digest(__file__)}
    result = {}
    for name in CASES:
        graph_path = JOINT / name / "trajectory_graph.csv"
        report_path = JOINT / name / "graph_fusion_report.json"
        report = json.loads(report_path.read_text())
        input_path = Path(report["inputs"]["trajectory"])
        times_in, positions_in, quaternions_in = load_trajectory(input_path)
        times_out, positions_out, quaternions_out = load_trajectory(graph_path)
        result[name] = correction_stats(times_in, positions_in, times_out,
                                        positions_out)
        stereo_sources = {"bidirectional": Path(report["inputs"]["stereo_report"])}
        for path in report["inputs"]["additional_stereo_reports"]:
            source = Path(path)
            stereo_sources[source.stem.removeprefix("stereo_scale_").removesuffix("_report")] = source
        stereo_sources["full_seam_pair"] = Path(
            report["full_seam_pair_window_integration"]["manifest"])
        result[name]["stereo_observation_consistency"] = {}
        total_edges = 0
        for source_name, path in stereo_sources.items():
            payload = json.loads(path.read_text())
            observations = payload.get("observations", payload.get("factors"))
            if observations is None:
                raise ValueError("stereo report has no observations")
            values = stereo_stats(observations, positions_in, quaternions_in,
                                  positions_out, quaternions_out)
            result[name]["stereo_observation_consistency"][source_name] = values
            total_edges += values["count"]
            hashes[str(path)] = digest(path)
        expected_edges = int(report["stereo_translation_fusion"]["stereo_edges"])
        if total_edges != expected_edges:
            raise ValueError(f"{name}: {total_edges} stereo edges != graph {expected_edges}")
        for path in (graph_path, report_path, input_path):
            hashes[str(path)] = digest(path)
    if any(digest(path) != value for path, value in hashes.items()):
        raise ValueError("frozen input changed during diagnostic")
    with OUTPUT.open("x") as stream:
        json.dump({"external_ground_truth_used": False,
                   "stage": "metric_input_to_graph_output_includes_full_rate_refinement",
                   "limitations": ["same raw frame index is a different motion across cases",
                                   "stage difference alone cannot identify a graph factor",
                                   "stereo consistency is not external ATE",
                                   "input stage orientation predates internal rotation fusion; these are not exact solver residuals",
                                   "overlapping stereo reports and full seam pairs are not independent measurements"],
                   "source_and_input_sha256": hashes, "cases": result},
                  stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(OUTPUT)


if __name__ == "__main__":
    main()
