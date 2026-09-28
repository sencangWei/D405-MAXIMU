#!/usr/bin/env python3
"""Reuse frozen NPZs for same-ray propagation and independent right-image LK."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from map_depth_propagation import compare_continuity, compare_update
from right_temporal_consistency import summarize_right_temporal_consistency
from summarize_frontend_geometry_probe import CASES, ROOT, validate_trace


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def right_points(sample, metadata):
    camera = metadata["camera_info"]
    stereo = metadata["stereo_depth_source"]
    if (camera != stereo["right_camera_info"] or any(camera["coeffs"])
            or stereo["left_focal_length_px"] != camera["fx"]):
        raise ValueError("diagnostic requires recorded common rectified pinhole intrinsics")
    baseline = float(stereo["baseline_m"])
    if not np.isfinite(baseline) or baseline <= 0:
        raise ValueError("invalid recorded stereo baseline")
    h, w = sample["image_shape"]
    sx, sy = camera["width"]/w, camera["height"]/h
    expected_K = np.array([[camera["fx"]/sx, 0, camera["ppx"]/sx],
                           [0, camera["fy"]/sy, camera["ppy"]/sy], [0, 0, 1]])
    if not np.allclose(sample["K"], expected_K, rtol=0, atol=1e-4):
        raise ValueError("sample K does not bind uncropped recorded factory grid")
    zf, zk = sample["depth_current_m"], sample["depth_keyframe_m"]
    valid = np.asarray(sample["valid"])
    if valid.dtype != np.bool_ or valid.shape != zf.shape or zf.shape != zk.shape:
        raise ValueError("invalid stereo support mask")
    mask = valid & np.isfinite(zf) & np.isfinite(zk) & (zf > 0) & (zk > 0)
    key, current = [], []
    for pixels, depth, dest in ((sample["pixel_keyframe"], zk, key),
                                (sample["pixel_current"], zf, current)):
        pixels = np.asarray(pixels)
        if (pixels.shape != (len(mask), 2) or not np.isfinite(pixels).all()
                or (pixels < 0).any() or (pixels[:, 0] >= w).any() or (pixels[:, 1] >= h).any()):
            raise ValueError("invalid saved correspondence pixel grid")
        # This is the actual native depth lookup location for INTER_NEAREST,
        # not a claimed subpixel recovery of the LANCZOS-resized feature image.
        xy = np.floor(pixels[mask]*np.array([sx, sy])).astype(float)
        xy[:, 0] -= camera["fx"]*baseline/depth[mask]
        dest.append(xy)
    return mask, key[0].astype(np.float32), current[0].astype(np.float32)


def main():
    output = ROOT/"reports/metric_window_bundle_20260928/map_and_right_probe_v1"
    if output.exists():
        raise FileExistsError("refusing to overwrite diagnostic census")
    sources = [Path(__file__).resolve(), *[Path(__file__).with_name(name) for name in
               ("map_depth_propagation.py", "right_temporal_consistency.py", "summarize_frontend_geometry_probe.py")]]
    hashes = {str(p): digest(p) for p in sources}
    inputs = {}
    cases = {}
    cv2.setNumThreads(1)
    output.mkdir()
    for case, trace_path in CASES.items():
        inputs[str(trace_path)] = digest(trace_path)
        trace = json.loads(trace_path.read_text())
        validate_trace(case, trace)
        original = trace
        if case == "fresh4":
            original_path = Path(trace["original_capture_trace"])
            inputs[str(original_path)] = digest(original_path)
            if inputs[str(original_path)] != trace["original_capture_sha256"]:
                raise ValueError("original failed capture trace changed")
            original = json.loads(original_path.read_text())
        dataset = Path(original["dataset"])
        manifest_path = dataset/"dataset_manifest.json"
        inputs[str(manifest_path)] = digest(manifest_path)
        if inputs[str(manifest_path)] != original["source_input_sha256"][str(manifest_path)]:
            raise ValueError("factory/source metadata changed")
        metadata = json.loads(manifest_path.read_text())
        if (metadata["slam_supervision"] is not False or metadata["frames"] != 1199
                or metadata["image_preprocessing"]["crop_bottom_px"] != 0
                or metadata["stereo_depth_source"]["frames"] != 1199):
            raise ValueError("unexpected frozen raw stereo dataset")
        images = {}

        def image(frame_id):
            if frame_id not in images:
                path = dataset/metadata["stereo_depth_source"]["right_directory"]/f"{frame_id:010d}.png"
                inputs[str(path)] = digest(path)
                if inputs[str(path)] != original["source_input_sha256"][str(path)]:
                    raise ValueError("consumed right PNG differs from original capture")
                img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                camera = metadata["camera_info"]
                if img is None or img.shape != (camera["height"], camera["width"]):
                    raise ValueError("native right image unavailable or wrong shape")
                images[frame_id] = img
            return images[frame_id]

        rows, previous = [], None
        sample_dir = output/case
        sample_dir.mkdir()
        for row in trace["rows"]:
            path = Path(row["sample_path"])
            inputs[str(path)] = digest(path)
            if inputs[str(path)] != row["sample_sha256"]:
                raise ValueError("saved geometry sample changed")
            with np.load(path, allow_pickle=False) as saved:
                sample = {key: saved[key].copy() for key in saved.files}
            mask, key, current = right_points(sample, metadata)
            right = summarize_right_temporal_consistency(image(row["keyframe_id"]), image(row["frame_id"]), key, current)
            keys = ("native_bounds_mask", "opencv_forward_success_mask", "opencv_backward_success_mask",
                    "finite_forward_mask", "finite_backward_mask", "forward_in_bounds_mask", "fb_closure_norm_px",
                    "forward_vs_expected_norm_px", "actual_projected_flow_px", "actual_current_points_right")
            raw = {k: np.asarray(right.pop(k)) for k in keys if k in right}
            raw["keyframe_pixel_ids"] = sample["keyframe_pixel_ids"][mask]
            raw_path = sample_dir/f'{row["frame_id"]:04d}.npz'
            np.savez_compressed(raw_path, **raw)
            continuity = (dict(status="UNKNOWN", count=0, reason="first_captured_frame") if previous is None
                          else compare_continuity(previous[1], sample, previous[0]["keyframe_id"], row["keyframe_id"]))
            rows.append(dict(frame_id=row["frame_id"], keyframe_id=row["keyframe_id"],
                             map_update=compare_update(sample), continuity=continuity, right_temporal=right,
                             raw_diagnostic_path=str(raw_path), raw_diagnostic_sha256=digest(raw_path)))
            previous = row, sample
        cases[case] = dict(rows=rows, dataset=str(dataset), decoded_right_images=len(images))
        print(f'{case}: {len(rows)} frames; {len(images)} right images', flush=True)
    if any(digest(p) != v for p, v in {**hashes, **inputs}.items()):
        raise ValueError("source/consumed input changed during census")
    result = dict(diagnostic_only=True, external_ground_truth_used=False, estimator_changed=False,
                  gpu_replay_used=False, fixed_input_scope=[1000, 1120], source_sha256=hashes,
                  input_sha256=inputs, cases=cases, runtime_versions=dict(numpy=np.__version__, opencv=cv2.__version__),
                  limitations=["same input indices are not matched actions", "stereo depth and LK are noisy consistency checks, not ground truth",
                               "NN lookup coordinate differs from resized image footprint; occlusions not certified",
                               "only sampled shared rays, not all landmarks; shape statistics remove uniform scale"])
    with (output/"census.json").open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
