"""Inject accepted stereo-window metric factors into native MASt3R fusion."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fuse_mast3r_stereo_imu as native


METRIC_SOURCE = "stereo_window_bundle_v1"
STEREO_FRAME = "infrared_left_camera_i"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_hashes(hashes: dict[str, str], label: str) -> None:
    for raw_path, expected in hashes.items():
        path = Path(raw_path)
        if not path.exists():
            raise ValueError(f"{label} file missing: {path}")
        actual = file_sha256(path)
        if actual != expected:
            raise ValueError(f"{label} input hash changed: {path}")


def selected_case(summary: dict, name: str) -> dict:
    if summary.get("external_reference_used") is not False:
        raise ValueError("metric-window controls used external reference")
    matches = [case for case in summary.get("cases", []) if case.get("case") == name]
    if len(matches) != 1:
        raise ValueError(f"metric-window controls case match count is {len(matches)}")
    return matches[0]


def source_report_from_case(case: dict) -> dict:
    input_hashes = case.get("input_sha256", {})
    verify_hashes(input_hashes, "metric-window case")
    candidates = []
    for raw_path in input_hashes:
        path = Path(raw_path)
        if path.suffix != ".json":
            continue
        try:
            report = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict):
            continue
        inputs = report.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        stereo_path = inputs.get("stereo_report")
        if stereo_path and str(Path(stereo_path).resolve()) in input_hashes:
            candidates.append(Path(stereo_path).resolve())
    if len(candidates) != 1:
        raise ValueError(f"metric-window source stereo report match count is {len(candidates)}")
    requested_stereo_report = candidates[0]
    source_report = load_json(requested_stereo_report)
    if source_report.get("schema") != "umi_mast3r_stereo_scale_v2":
        raise ValueError("metric-window source stereo report has unexpected schema")
    return source_report


def compare_source_report(primary: dict, source: dict) -> None:
    for key in ("session", "trajectory", "observation_frame"):
        if primary.get(key) != source.get(key):
            raise ValueError(f"primary stereo report {key} differs from controls source")
    if primary.get("observation_frame") != STEREO_FRAME:
        raise ValueError("metric-window factors require infrared_left camera frame")
    if primary.get("factory_stereo_calibration") != source.get("factory_stereo_calibration"):
        raise ValueError("primary factory stereo calibration differs from controls source")


def trajectory_times(path: Path) -> np.ndarray:
    return np.asarray(native.load_trajectory(path)[0], dtype=float)


def validate_timestamps(native_trajectory: Path, source_report: dict) -> np.ndarray:
    native_times = trajectory_times(native_trajectory)
    source_times = trajectory_times(Path(source_report["trajectory"]))
    if native_times.shape != source_times.shape or not np.allclose(
        native_times, source_times, atol=1e-9, rtol=0.0
    ):
        raise ValueError("native graph trajectory timestamps differ from controls source")
    return native_times


def validate_elapsed(row: dict, times: np.ndarray) -> None:
    raw_indices = row.get("indices")
    if (
        not isinstance(raw_indices, list)
        or len(raw_indices) != 5
        or any(isinstance(value, bool) or not isinstance(value, int) for value in raw_indices)
    ):
        raise ValueError("metric-window indices must be five integers")
    indices = np.asarray(raw_indices, dtype=int)
    if indices.shape != (5,):
        raise ValueError("metric-window indices must have length five")
    if np.any(np.diff(indices) <= 0):
        raise ValueError("metric-window indices must be strictly increasing")
    if indices[0] < 0 or indices[-1] >= len(times):
        raise ValueError("metric-window indices outside trajectory range")
    expected = times[indices] - times[0]
    actual = np.asarray(row["elapsed_s"], dtype=float)
    if actual.shape != (5,) or not np.allclose(actual, expected, atol=1e-6, rtol=0.0):
        raise ValueError("metric-window elapsed_s does not match native trajectory")


def validate_accepted_row(row: dict, times: np.ndarray) -> dict | None:
    if not row.get("accepted"):
        return None
    diagnostics = row.get("diagnostics", {})
    if diagnostics.get("diagnostic_only") is not False:
        raise ValueError("metric-window accepted row is diagnostic-only")
    if diagnostics.get("solver_success") is not True:
        raise ValueError("metric-window solver did not succeed")
    pixel_p95 = float(diagnostics.get("pixel_p95_px", float("inf")))
    pixel_inliers = float(diagnostics.get("pixel_inlier_fraction", 0.0))
    pixel_ok = (np.isfinite(pixel_p95) and np.isfinite(pixel_inliers)
                and pixel_p95 >= 0.0 and 0.0 <= pixel_inliers <= 1.0
                and (pixel_p95 <= 2.0 or pixel_inliers >= 0.95))
    if not pixel_ok:
        raise ValueError("metric-window pixel guard failed")
    gyro_p95 = float(diagnostics.get("gyro_p95_rad", float("inf")))
    if not np.isfinite(gyro_p95) or gyro_p95 > np.radians(5.0):
        raise ValueError("metric-window gyro guard failed")
    bias_abs_max = float(diagnostics.get("gyro_bias_component_abs_max_rad_s", float("inf")))
    if not np.isfinite(bias_abs_max) or bias_abs_max > 0.01:
        raise ValueError("metric-window bias guard failed")
    if "minimum_depth_m" in diagnostics:
        minimum_depth = float(diagnostics["minimum_depth_m"])
        if not np.isfinite(minimum_depth) or minimum_depth <= 0.0:
            raise ValueError("metric-window minimum depth must be finite positive")
    endpoint = np.asarray(row.get("endpoint_m"), dtype=float)
    if endpoint.shape != (3,) or not np.all(np.isfinite(endpoint)):
        raise ValueError("metric-window endpoint_m must be finite xyz")
    validate_elapsed(row, times)
    indices = [int(index) for index in row["indices"]]
    return {
        "accepted": True,
        "metric_source": METRIC_SOURCE,
        "metric_displacement_frame": STEREO_FRAME,
        "first_index": indices[0],
        "second_index": indices[-1],
        "sample_hop": indices[-1] - indices[0],
        "metric_displacement_camera_i_m": endpoint.tolist(),
        "window": int(row["window"]),
        "source_window": int(row["window"]),
        "diagnostic_only": False,
        "stereo_sigma_m": 0.004,
    }


def build_factors(case: dict, times: np.ndarray) -> list[dict]:
    factors = []
    for row in case.get("windows", []):
        factor = validate_accepted_row(row, times)
        if factor is not None:
            factors.append(factor)
    return factors


def metric_window_confidence_dispatch(original):
    def confidence(observation: dict, reference_scale: float) -> float:
        if observation.get("metric_source") == METRIC_SOURCE:
            return 1.0
        return original(observation, reference_scale)

    return confidence


def manifest_path(report_path: Path) -> Path:
    return report_path.with_name(f"{report_path.stem}_metric_window_factors.json")


def write_manifest(path: Path, controls: Path, case_name: str, factors: list[dict]) -> dict:
    payload = {
        "schema": "mast3r_metric_window_factor_manifest_v1",
        "metric_source": METRIC_SOURCE,
        "controls": str(controls.resolve()),
        "controls_sha256": file_sha256(controls),
        "wrapper_sha256": file_sha256(Path(__file__).resolve()),
        "native_fusion_sha256": file_sha256(Path(native.__file__).resolve()),
        "case": case_name,
        "factor_count": len(factors),
        "factors": factors,
        "factors_sha256": canonical_sha256(factors),
        "calibrated_sigma": False,
        "external_ground_truth_used": False,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def augment_report(report: dict, manifest: dict, path: Path) -> dict:
    augmented = dict(report)
    augmented["metric_window_integration"] = {
        "schema": manifest["schema"],
        "metric_source": METRIC_SOURCE,
        "case": manifest["case"],
        "factor_count": manifest["factor_count"],
        "factors_sha256": manifest["factors_sha256"],
        "manifest": str(path.resolve()),
        "stereo_sigma_m": 0.004,
        "calibrated_sigma": False,
        "external_ground_truth_used": False,
    }
    return augmented


def parse_native_paths(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--stereo-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--stream", default="color")
    args, _ = parser.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--metric-window-controls", type=Path, required=True)
    parser.add_argument("--metric-window-case", required=True)
    wrapper_args, native_argv = parser.parse_known_args(argv)
    native_paths = parse_native_paths(native_argv)
    if native_paths.stream != "infrared_left":
        raise ValueError("metric-window fusion requires --stream infrared_left")
    if native_paths.output.exists():
        raise ValueError(f"refuse to overwrite output: {native_paths.output}")
    if native_paths.report.exists():
        raise ValueError(f"refuse to overwrite report: {native_paths.report}")
    sidecar = manifest_path(native_paths.report)
    if sidecar.exists():
        raise ValueError(f"refuse to overwrite metric-window manifest: {sidecar}")

    controls = load_json(wrapper_args.metric_window_controls)
    verify_hashes(controls.get("source_sha256", {}), "metric-window source")
    case = selected_case(controls, wrapper_args.metric_window_case)
    if case.get("external_reference_used") not in (None, False):
        raise ValueError("metric-window case used external reference")
    source_report = source_report_from_case(case)
    times = validate_timestamps(native_paths.trajectory, source_report)
    factors = build_factors(case, times)

    original_merge = native.merge_stereo_reports
    original_confidence = native.stereo_observation_confidence
    original_run = native.run
    original_argv = sys.argv[:]

    def merge_with_metric_windows(primary: dict, additions: list[dict]) -> dict:
        compare_source_report(primary, source_report)
        merged = original_merge(primary, additions)
        merged["observations"] = list(merged.get("observations", [])) + factors
        return merged

    def run_with_metric_manifest(args: argparse.Namespace) -> dict:
        report = original_run(args)
        manifest = write_manifest(sidecar, wrapper_args.metric_window_controls, wrapper_args.metric_window_case, factors)
        augmented = augment_report(report, manifest, sidecar)
        args.report.write_text(
            json.dumps(augmented, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return augmented

    try:
        native.merge_stereo_reports = merge_with_metric_windows
        native.stereo_observation_confidence = metric_window_confidence_dispatch(
            original_confidence
        )
        native.run = run_with_metric_manifest
        sys.argv = [str(Path(native.__file__).resolve())] + native_argv
        code = native.main()
        verify_hashes(controls.get("source_sha256", {}), "metric-window source")
        verify_hashes(case.get("input_sha256", {}), "metric-window case")
        return code
    finally:
        native.merge_stereo_reports = original_merge
        native.stereo_observation_confidence = original_confidence
        native.run = original_run
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
