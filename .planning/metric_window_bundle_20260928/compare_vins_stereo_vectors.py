#!/usr/bin/env python3
"""Compare recorded VINS camera displacement with independent D405 PnP edges.

Diagnostic only: neither Lighthouse nor robot poses are read or used.
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


def compare(prior_rows, observations, minimum_distance_m=0.01):
    priors = {int(row["input_index"]): row for row in prior_rows if row["valid"] == "1"}
    results = []
    for edge in observations:
        if not edge.get("accepted") or "metric_displacement_camera_i_m" not in edge:
            continue
        first, second = int(edge["first_index"]), int(edge["second_index"])
        if first not in priors or second not in priors:
            continue
        measured = np.asarray(edge["metric_displacement_camera_i_m"], dtype=float)
        measured_distance = float(np.linalg.norm(measured))
        if measured_distance < minimum_distance_m:
            continue
        a, b = priors[first], priors[second]
        position_a = np.asarray([float(a[key]) for key in ("x", "y", "z")])
        position_b = np.asarray([float(b[key]) for key in ("x", "y", "z")])
        rotation_a = Rotation.from_quat([float(a[key]) for key in ("qx", "qy", "qz", "qw")])
        predicted = rotation_a.inv().apply(position_b - position_a)
        predicted_distance = float(np.linalg.norm(predicted))
        if not np.isfinite(predicted).all() or predicted_distance <= 0:
            raise ValueError(f"invalid VINS displacement at edge {first}->{second}")
        cosine = float(np.clip(np.dot(predicted, measured) / (predicted_distance * measured_distance), -1, 1))
        results.append({
            "first_index": first,
            "second_index": second,
            "measured_distance_mm": measured_distance * 1000,
            "predicted_distance_mm": predicted_distance * 1000,
            "vector_residual_mm": float(np.linalg.norm(predicted - measured) * 1000),
            "direction_error_deg": float(np.degrees(np.arccos(cosine))),
            "bidirectional_relative_disagreement": edge.get("bidirectional_relative_disagreement"),
            "pnp_inlier_ratio": edge.get("pnp_inlier_ratio"),
        })
    if not results:
        raise ValueError("no accepted metric edge has two valid VINS camera priors")
    residual = np.asarray([item["vector_residual_mm"] for item in results])
    angle = np.asarray([item["direction_error_deg"] for item in results])
    return {
        "edges": len(results),
        "vector_residual_median_mm": float(np.median(residual)),
        "vector_residual_p95_mm": float(np.percentile(residual, 95)),
        "direction_error_median_deg": float(np.median(angle)),
        "direction_error_p95_deg": float(np.percentile(angle, 95)),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--priors", type=Path, required=True)
    parser.add_argument("--stereo-report", type=Path, required=True)
    parser.add_argument("--minimum-distance-mm", type=float, default=10.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite diagnostic output")
    with args.priors.open(newline="", encoding="utf-8") as stream:
        priors = list(csv.DictReader(stream))
    stereo = json.loads(args.stereo_report.read_text())
    result = compare(priors, stereo["observations"], args.minimum_distance_mm / 1000)
    result.update(schema="vins_vs_independent_stereo_vectors_v1",
                  external_ground_truth_used=False,
                  priors=str(args.priors), stereo_report=str(args.stereo_report),
                  minimum_distance_mm=args.minimum_distance_mm)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
