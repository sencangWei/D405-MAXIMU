"""Read-only diagnostic for camera-fixed self-occlusion stereo tracks."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import align_mast3r_scale_with_stereo as stereo
import run_joint_window_controls as joint
from prepare_stereo_window_observations import track_stereo_window


POLYGON_NORMALIZED = np.asarray(
    [[0.525, 0.63], [0.605, 0.63], [0.680, 1.0], [0.450, 1.0]], dtype=float
)
EXPECTED_CASES = {
    "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4",
    "fresh1", "fresh2", "fresh3", "fresh4",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def polygon_pixels(shape: tuple[int, int]) -> np.ndarray:
    height, width = shape[:2]
    return np.rint(POLYGON_NORMALIZED * np.array([width, height])).astype(float)


def inside_polygon(points: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    polygon = polygon_pixels(shape)
    x, y = points[:, 0], points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    boundary = np.zeros(len(points), dtype=bool)
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        dx, dy = xj - xi, yj - yi
        cross = (x - xi) * dy - (y - yi) * dx
        within = (
            (np.minimum(xi, xj) <= x) & (x <= np.maximum(xi, xj))
            & (np.minimum(yi, yj) <= y) & (y <= np.maximum(yi, yj))
        )
        boundary |= within & np.isclose(cross, 0.0, atol=1e-9)
        if yi != yj:
            x_intersect = xi + (y - yi) * (xj - xi) / (yj - yi)
            crosses = ((yi > y) != (yj > y)) & (x < x_intersect)
            inside ^= crosses
        j = i
    return inside & ~boundary


def median_motion(source: np.ndarray, endpoint: np.ndarray, mask: np.ndarray):
    if not np.any(mask):
        return None
    return float(np.median(np.linalg.norm(endpoint[mask] - source[mask], axis=1)))


def median_values(values: np.ndarray, mask: np.ndarray):
    if not np.any(mask):
        return None
    return float(np.median(values[mask]))


def summarize_tracks(data: dict, image_shape: tuple[int, int]) -> dict:
    if not data.get("accepted"):
        return {"accepted": False, "reason": data.get("reason", "unknown")}
    valid = np.asarray(data["valid"], dtype=bool)
    observations = np.asarray(data["observations"], dtype=float)
    source_valid = valid[0]
    endpoint_valid = valid[-1]
    both = source_valid & endpoint_valid
    source_left = observations[0, :, :2]
    endpoint_left = observations[-1, :, :2]
    inside_source = inside_polygon(source_left, image_shape)
    outside_source = ~inside_source
    disparity = observations[0, :, 0] - observations[0, :, 2]
    depths = np.asarray(data["initial_points"], dtype=float)[:, 2] if "initial_points" in data else np.full(len(source_valid), np.nan)
    inside_source_count = int(np.count_nonzero(source_valid & inside_source))
    outside_source_count = int(np.count_nonzero(source_valid & outside_source))
    inside_endpoint_count = int(np.count_nonzero(endpoint_valid & inside_polygon(endpoint_left, image_shape)))
    outside_endpoint_count = int(np.count_nonzero(endpoint_valid & ~inside_polygon(endpoint_left, image_shape)))
    inside_survive = int(np.count_nonzero(both & inside_source))
    outside_survive = int(np.count_nonzero(both & outside_source))
    return {
        "accepted": True,
        "reason": "ok",
        "tracked_landmarks": int(valid.shape[1]),
        "source_valid_count": int(np.count_nonzero(source_valid)),
        "endpoint_valid_count": int(np.count_nonzero(endpoint_valid)),
        "source_inside_polygon_count": inside_source_count,
        "source_outside_polygon_count": outside_source_count,
        "endpoint_inside_polygon_count": inside_endpoint_count,
        "endpoint_outside_polygon_count": outside_endpoint_count,
        "inside_survival_fraction": None if inside_source_count == 0 else inside_survive / inside_source_count,
        "outside_survival_fraction": None if outside_source_count == 0 else outside_survive / outside_source_count,
        "median_left_pixel_motion_inside_source_mask_px": median_motion(source_left, endpoint_left, both & inside_source),
        "median_left_pixel_motion_outside_source_mask_px": median_motion(source_left, endpoint_left, both & outside_source),
        "median_source_depth_inside_m": median_values(depths, source_valid & inside_source),
        "median_source_depth_outside_m": median_values(depths, source_valid & outside_source),
        "median_source_disparity_inside_px": median_values(disparity, source_valid & inside_source),
        "median_source_disparity_outside_px": median_values(disparity, source_valid & outside_source),
        "inside_to_outside_source_count_ratio": None if outside_source_count == 0 else inside_source_count / outside_source_count,
    }


def case_sources():
    registry = ROOT / ".planning/joint_metric_scale_20260927/run_cached_regression.py"
    cases = list(load_module("self_track_cases", registry).CASES)
    cases += [(f"fresh{i}", f"joint_scale_independent_four_20260927/take{i}/fusion/{'rescue' if i == 2 else 'baseline'}", "") for i in range(1, 5)]
    if len(cases) != 10 or {name for name, _, _ in cases} != EXPECTED_CASES:
        raise ValueError("exact all-ten registry required")
    return cases, registry


def run_case(name: str, graph_path: Path) -> dict:
    graph = json.loads(graph_path.read_text())
    inputs = graph["inputs"]
    report = json.loads(Path(inputs["stereo_report"]).read_text())
    calibration = report["factory_stereo_calibration"]
    trajectory = Path(report["trajectory"])
    timestamps = stereo.load_trajectory(trajectory)[0]
    selection = joint.pair_windows(len(timestamps))[-1][4:]
    dense = np.arange(int(selection[0]), int(selection[-1]) + 1)
    session = Path(inputs["session"])
    left_numbers, right_numbers, _ = stereo.match_trajectory_to_stereo_frames(
        session / "d405_frames.csv", timestamps, trajectory_frame="infrared_left"
    )
    left, right = stereo.load_selected_prepared_stereo_images(
        trajectory.parent / "dataset",
        session / "d405_frames.csv",
        {int(left_numbers[i]) for i in dense},
        {int(right_numbers[i]) for i in dense},
    )
    left_images = [left[int(left_numbers[i])] for i in dense]
    right_images = [right[int(right_numbers[i])] for i in dense]
    result = track_stereo_window(left_images, right_images, calibration, initialize_poses=False)
    manifest_path = trajectory.parent / "dataset/dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    preprocessing = manifest.get("image_preprocessing", {})
    row = {
        "case": name,
        "indices": selection.tolist(),
        "raw_frame_indices": dense.tolist(),
        "left_frame_numbers": [int(left_numbers[i]) for i in dense],
        "right_frame_numbers": [int(right_numbers[i]) for i in dense],
        "dataset_manifest_image_preprocessing": preprocessing,
        "input_sha256": {
            str(graph_path): sha(graph_path),
            str(Path(inputs["stereo_report"])): sha(Path(inputs["stereo_report"])),
            str(trajectory): sha(trajectory),
            str(manifest_path): sha(manifest_path),
            str(session / "d405_frames.csv"): sha(session / "d405_frames.csv"),
        },
        "decoded_grayscale_frame_sha256": {
            f"left:{number}": hashlib.sha256(left[number].tobytes()).hexdigest()
            for number in [int(left_numbers[i]) for i in dense]
        } | {
            f"right:{number}": hashlib.sha256(right[number].tobytes()).hexdigest()
            for number in [int(right_numbers[i]) for i in dense]
        },
    }
    row.update(summarize_tracks(result, left_images[0].shape))
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports/metric_window_bundle_20260928/self_track_diagnostic_v2.json")
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError(f"refuse to overwrite output: {args.output}")
    cases, registry = case_sources()
    sources = [Path(__file__), registry, Path(joint.__file__), ROOT / "scripts/prepare_stereo_window_observations.py",
               ROOT / "scripts/prepare_mast3r_slam_dataset.py", ROOT / "scripts/align_mast3r_scale_with_stereo.py"]
    source_hashes = {str(path.resolve()): sha(path.resolve()) for path in sources}
    rows = [run_case(name, ROOT / "reports" / source / "mast3r/graph_fusion_report.json") for name, source, _ in cases]
    for path, expected in source_hashes.items():
        if sha(Path(path)) != expected:
            raise ValueError(f"source changed during diagnostic: {path}")
    report = {
        "schema": "self_track_diagnostic_v2",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "solver_or_estimator_run": False,
        "mask_applied": False,
        "hypothesis": "camera-fixed foreground contamination evidence only; not an accuracy or root-cause proof",
        "polygon_policy": {
            "source": "prepare_mast3r_slam_dataset.mask_fixed_self_occlusion normalized polygon",
            "normalized_xy": POLYGON_NORMALIZED.tolist(),
            "applied_to_images": False,
        },
        "source_sha256": source_hashes,
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"cases": len(rows), "accepted": sum(row.get("accepted", False) for row in rows), "output": str(args.output)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
