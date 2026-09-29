#!/usr/bin/env python3
"""Observation-only wrapper around the frozen MASt3R calibrated frontend."""
import argparse
from collections import OrderedDict
import csv
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

import numpy as np
from scipy.spatial.transform import Rotation


FIRST, LAST, MAX_POINTS = 1000, 1120, 2048
PRODUCER_KEYS = ("config_sha256", "toolchain_commit", "toolchain_dirty_diff_sha256",
                 "lietorch_commit", "checkpoint_sha256")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            result.update(chunk)
    return result.hexdigest()


def sample_indices(valid, limit=MAX_POINTS):
    valid = np.asarray(valid)
    if valid.dtype != np.bool_ or valid.ndim != 1 or limit < 1:
        raise ValueError("expected a one-dimensional bool mask and positive limit")
    indices = np.flatnonzero(valid)
    if len(indices) > limit:
        indices = indices[np.linspace(0, len(indices)-1, limit).astype(int)]
    return indices


def dense_matched_ids(valid, index_map, image_shape):
    h, w = image_shape
    valid = np.asarray(valid).reshape(-1).astype(bool)
    index_map = np.asarray(index_map).reshape(-1)
    if valid.size != h*w or index_map.size != h*w:
        raise ValueError("dense correspondence shape differs from calibrated image")
    ids = np.flatnonzero(valid).astype(np.int32)
    mapped = index_map[ids].astype(np.int32)
    if np.any(mapped < 0) or np.any(mapped >= h*w):
        raise ValueError("dense matched pixel IDs outside calibrated image")
    return ids, mapped


def integrated_priors(path, frame_count):
    with Path(path).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if [int(row["input_index"]) for row in rows] != list(range(frame_count)):
        raise ValueError("rotation-prior coverage differs from full-rate input")
    increments = np.array([[float(row[k]) for k in ("qx", "qy", "qz", "qw")]
                           for row in rows])
    if not np.isfinite(increments).all() or not np.allclose(
            np.linalg.norm(increments, axis=1), 1, atol=1e-8, rtol=0):
        raise ValueError("invalid incremental IMU quaternions")
    if Rotation.from_quat(increments[0]).magnitude() > 1e-8:
        raise ValueError("first incremental rotation must be identity")
    current = Rotation.identity()
    absolute = []
    for increment in increments:
        current = current * Rotation.from_quat(increment)
        absolute.append(current.as_quat())
    return np.array(absolute)


def producer_identity(manifest):
    tool = Path(manifest["toolchain"])
    return dict(config_sha256=digest(manifest["config"]),
                checkpoint_sha256=digest(manifest["checkpoint"]),
                toolchain_commit=subprocess.check_output(
                    ["git", "-C", str(tool), "rev-parse", "HEAD"], text=True).strip(),
                toolchain_dirty_diff_sha256=hashlib.sha256(subprocess.check_output(
                    ["git", "-C", str(tool), "diff"])).hexdigest(),
                lietorch_commit=subprocess.check_output(
                    ["git", "-C", str(tool/"thirdparty/lietorch"), "rev-parse", "HEAD"],
                    text=True).strip())


def frozen_inputs(frozen, manifest, dataset):
    tool = Path(manifest["toolchain"])
    paths = [tool/"main.py", *sorted((tool/"mast3r_slam").glob("*.py")),
             Path(manifest["config"]), tool/"config/base.yaml",
             Path(manifest["checkpoint"]), frozen/"run_manifest.json",
             frozen/"trajectory_frames.csv", frozen/"trajectory_online_frames.csv",
             *[dataset/name for name in ("frames.csv", "calibration.yaml",
                                         "dataset_manifest.json", "imu_rotation_priors.csv",
                                         "imu_rotation_priors_report.json")],
             Path(__file__).resolve(), Path(__file__).with_name("frontend_geometry_diagnostics.py")]
    # Bind every actual prepared image, not only a convenient subset of frames.
    paths += sorted(dataset.glob("*.png"))
    paths += sorted((dataset/"stereo_right").glob("*.png"))
    if len(list(dataset.glob("*.png"))) != 1199 or len(list((dataset/"stereo_right").glob("*.png"))) != 1199:
        raise ValueError("prepared left/right PNG coverage must be exactly1199each")
    return {str(path): digest(path) for path in paths}


def as_numpy(value):
    return value.detach().cpu().numpy().copy()


class Recorder:
    def __init__(self, dataset, output, priors, provider):
        self.dataset, self.output, self.priors, self.provider = dataset, output, priors, provider
        self.context = None
        self.rows, self.errors = [], []
        self.depth_cache = OrderedDict()
        self.captured = None
        self.dense_ids = False

    def depth(self, frame_id, shape):
        key = (frame_id, tuple(shape))
        if key not in self.depth_cache:
            self.depth_cache[key] = self.provider.get_depth(
                self.dataset/f"{frame_id:010d}.png", tuple(shape))
        self.depth_cache.move_to_end(key)
        while len(self.depth_cache) > 64:
            self.depth_cache.popitem(last=False)
        return np.array(self.depth_cache[key], copy=True).reshape(-1)

    def prepare_capture(self, tracker, args):
        frame_id, keyframe_id = self.context
        Xf, Xk, T_WCf, T_WCk, _, valid, _, _, _, K, shape = args[:11]
        shape = tuple(int(x) for x in shape)
        ids = sample_indices(as_numpy(valid).reshape(-1).astype(bool))
        full_mapped = as_numpy(tracker.idx_f2k).reshape(-1)
        mapped = full_mapped[ids].astype(int)
        h, w = shape
        if np.any(mapped < 0) or np.any(mapped >= h*w) or len(valid) != h*w:
            raise ValueError("matched pixel IDs or calibrated image shape invalid")
        capture = dict(Xf=as_numpy(Xf[ids]), Xk=as_numpy(Xk[ids]),
                       valid=np.ones(len(ids), dtype=bool), K=as_numpy(K),
                       pixel_current=np.column_stack((mapped % w, mapped // w)),
                       pixel_keyframe=np.column_stack((ids % w, ids // w)),
                       depth_current_m=self.depth(frame_id, shape)[mapped],
                       depth_keyframe_m=self.depth(keyframe_id, shape)[ids],
                       T_pre=as_numpy((T_WCk.inv()*T_WCf).data).reshape(8),
                       quat_current_xyzw=self.priors[frame_id],
                       quat_keyframe_xyzw=self.priors[keyframe_id],
                       image_shape=np.array(shape, dtype=int),
                       pixel_border=float(tracker.cfg["pixel_border"]),
                       depth_eps=float(tracker.cfg["depth_eps"]),
                       full_optimize_valid_count=int(as_numpy(valid).sum()),
                       keyframe_pixel_ids=ids, current_pixel_ids=mapped)
        if self.dense_ids:
            dense_ids, dense_mapped = dense_matched_ids(as_numpy(valid), full_mapped, shape)
            capture["dense_keyframe_pixel_ids"] = dense_ids.astype(np.int32)
            capture["dense_current_pixel_ids"] = dense_mapped
        return capture

    def finish_track(self, tracker, frame, outcome):
        from frontend_geometry_diagnostics import summarize_geometry
        frame_id, keyframe_id = self.context
        if self.captured is None:
            self.rows.append(dict(frame_id=frame_id, keyframe_id=keyframe_id,
                                  captured=False, reason="no_calibrated_opt_capture"))
            return
        capture = self.captured
        keyframe = tracker.keyframes.last_keyframe()
        if int(keyframe.frame_id) != keyframe_id:
            raise ValueError("last keyframe changed within track wrapper")
        capture["T_final"] = as_numpy((keyframe.T_WC.inv()*frame.T_WC).data).reshape(8)
        after = as_numpy(keyframe.X_canon[capture["keyframe_pixel_ids"]])
        xy = capture["pixel_keyframe"]
        K = capture["K"]
        rays = np.column_stack(((xy[:, 0]-K[0, 2])/K[0, 0],
                                (xy[:, 1]-K[1, 2])/K[1, 1], np.ones(len(xy))))
        capture["Xk_after"] = rays * after[:, 2:3]
        sample_path = self.output/"samples"/f"{frame_id:04d}.npz"
        np.savez_compressed(sample_path, **capture)
        self.rows.append(dict(frame_id=frame_id, keyframe_id=keyframe_id,
                              captured=True, new_keyframe_requested=bool(outcome[0]),
                              try_relocalization=bool(outcome[2]),
                              sampled_optimize_points=len(xy),
                              full_optimize_valid_count=int(capture["full_optimize_valid_count"]),
                              sample_sha256=digest(sample_path), sample_path=str(sample_path),
                              diagnostics=summarize_geometry(capture)))


def install_hooks(tracker_class, recorder):
    original_track, original_opt = tracker_class.track, tracker_class.opt_pose_calib_sim3

    def track(self, frame, *args, **kwargs):
        frame_id = int(frame.frame_id)
        if not FIRST <= frame_id <= LAST:
            return original_track(self, frame, *args, **kwargs)
        recorder.context = (frame_id, int(self.keyframes.last_keyframe().frame_id))
        recorder.captured = None
        try:
            outcome = original_track(self, frame, *args, **kwargs)
            try:
                recorder.finish_track(self, frame, outcome)
            except Exception as exc:
                recorder.errors.append(dict(frame_id=frame_id, stage="finish", error=repr(exc)))
            return outcome
        finally:
            recorder.context = None

    def optimize(self, *args, **kwargs):
        capture = None
        if recorder.context is not None:
            try:
                capture = recorder.prepare_capture(self, args)
            except Exception as exc:
                recorder.errors.append(dict(frame_id=recorder.context[0],
                                            stage="prepare", error=repr(exc)))
        result = original_opt(self, *args, **kwargs)
        if capture is not None:
            try:
                capture["T_post"] = as_numpy(result[1].data).reshape(8)
                recorder.captured = capture
            except Exception as exc:
                recorder.errors.append(dict(frame_id=recorder.context[0], stage="post", error=repr(exc)))
        return result

    tracker_class.track, tracker_class.opt_pose_calib_sim3 = track, optimize

    def restore():
        tracker_class.track, tracker_class.opt_pose_calib_sim3 = original_track, original_opt
    return restore


def compare_trajectories(frozen, output):
    result = {}
    for name in ("trajectory_frames.csv", "trajectory_online_frames.csv"):
        a, b = frozen/name, output/name
        old = np.genfromtxt(a, delimiter=",", skip_header=1)
        new = np.genfromtxt(b, delimiter=",", skip_header=1)
        if old.shape != new.shape or old.ndim != 2 or old.shape[1] != 8 or not (
                np.isfinite(old).all() and np.isfinite(new).all()):
            raise ValueError("full-rate trajectory shape/finite identity failure")
        if not np.array_equal(old[:, 0], new[:, 0]):
            raise ValueError("trajectory timestamp identity failure")
        result[name] = dict(rows=len(old), byte_identical=digest(a)==digest(b),
                            array_identical=bool(np.array_equal(old, new)),
                            max_component_delta=float(np.abs(old-new).max()))
    return result


def permitted_producer_difference(mismatched, allow_dirty, allow_revision):
    if not mismatched:
        return True
    if allow_revision and set(mismatched) <= {
            "toolchain_commit", "toolchain_dirty_diff_sha256"}:
        return True
    return allow_dirty and mismatched == ["toolchain_dirty_diff_sha256"]


def main():
    global FIRST, LAST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first", type=int, default=FIRST)
    parser.add_argument("--last", type=int, default=LAST)
    parser.add_argument("--dense-ids", action="store_true")
    parser.add_argument("--allow-dirty-diff-change", action="store_true")
    parser.add_argument("--allow-producer-revision-change", action="store_true",
                        help="permit commit/diff provenance changes only when both full trajectories reproduce byte-for-byte")
    args = parser.parse_args()
    FIRST, LAST = args.first, args.last
    if not 0 <= FIRST <= LAST < 1199:
        raise ValueError("capture scope must be inside the 1199 prepared frames")
    frozen, output = args.frozen.resolve(strict=True), args.output.resolve()
    if output.exists():
        raise FileExistsError("refusing to overwrite geometry probe")
    manifest = json.loads((frozen/"run_manifest.json").read_text())
    dataset = (frozen/"dataset").resolve(strict=True)
    before = producer_identity(manifest)
    mismatched = [key for key in PRODUCER_KEYS if before[key] != manifest[key]]
    if not permitted_producer_difference(
            mismatched, args.allow_dirty_diff_change, args.allow_producer_revision_change):
        raise ValueError("producer differs from frozen frontend")
    hashes = frozen_inputs(frozen, manifest, dataset)
    with (dataset/"frames.csv").open(newline="") as stream:
        frames = list(csv.DictReader(stream))
    if [int(row["input_index"]) for row in frames] != list(range(1199)):
        raise ValueError("expected exact frozen 1199 full-rate inputs")
    priors = integrated_priors(dataset/"imu_rotation_priors.csv", len(frames))
    prior_report = json.loads((dataset/"imu_rotation_priors_report.json").read_text())
    if prior_report.get("td_s") != -0.009109323 or prior_report.get("estimate_td") != 0:
        raise ValueError("prepared IMU prior does not bind the fixed Docker2 time convention")
    output.mkdir(parents=True)
    (output/"samples").mkdir()
    (output/"dataset").symlink_to(dataset, target_is_directory=True)
    os.environ["MAST3R_MATCH_LOG"] = str(output/"match_log.csv")
    os.environ["MAST3R_FRONTEND_LOG"] = str(output/"frontend_log.csv")
    tool = Path(manifest["toolchain"])
    sys.path.insert(0, str(tool))
    from mast3r_slam.stereo_depth import StereoDepthProvider
    from mast3r_slam.tracker import FrameTracker
    provider = StereoDepthProvider.from_dataset(dataset)
    if provider is None:
        raise ValueError("frozen dataset lacks independent stereo depth")
    recorder = Recorder(dataset, output, priors, provider)
    recorder.dense_ids = args.dense_ids
    restore = install_hooks(FrameTracker, recorder)
    old_argv, old_cwd = sys.argv, Path.cwd()
    started = time.monotonic()
    try:
        os.chdir(tool)
        sys.argv = [str(tool/"main.py"), "--dataset", str(output/"dataset"),
                    "--config", manifest["config"], "--checkpoint", manifest["checkpoint"],
                    "--calib", str(dataset/"calibration.yaml"),
                    "--save-as", str(output/"mast3r_logs"), "--no-viz", "--no-reconstruction"]
        runpy.run_path(str(tool/"main.py"), run_name="__main__")
    finally:
        restore()
        sys.argv = old_argv
        os.chdir(old_cwd)
    root = Path(__file__).resolve().parents[2]
    for source, target in (("dataset_full.txt", "trajectory_frames.csv"),
                           ("dataset_online.txt", "trajectory_online_frames.csv")):
        subprocess.run([sys.executable, str(root/"scripts/convert_mast3r_slam_trajectory.py"),
                        "--trajectory", str(output/"mast3r_logs"/source),
                        "--frames", str(dataset/"frames.csv"), "--output", str(output/target)], check=True)
    after = producer_identity(manifest)
    if before != after or any(digest(path) != value for path, value in hashes.items()):
        raise ValueError("producer or prepared input changed during geometry replay")
    identities = compare_trajectories(frozen, output)
    result = dict(diagnostic_only=True, external_ground_truth_used=False,
                  estimator_changed=False, fixed_input_scope=[FIRST, LAST],
                  dense_correspondence_ids_saved=args.dense_ids,
                  producer_difference_from_frozen=mismatched,
                  frozen_producer_identity={key: manifest[key] for key in PRODUCER_KEYS},
                  frame_index_semantics="zero-based full-rate input; not hardware source_frame_number",
                  imu_prior_semantics="cumulative right-composition of per-frame camera increments",
                  elapsed_s=time.monotonic()-started, frozen=str(frozen), dataset=str(dataset),
                  source_input_sha256=hashes, producer_identity=before,
                  trajectory_identity=identities, rows=recorder.rows, errors=recorder.errors,
                  limitations=["observational consistency is not absolute pose accuracy or unique causation",
                               "dense samples are correlated, not independent landmarks",
                               "stereo depth .15..65m support may be missing; never treat it as zero"])
    with (output/"geometry_trace.json").open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if recorder.errors or [row["frame_id"] for row in recorder.rows] != list(range(FIRST, LAST+1)):
        raise ValueError("geometry capture errors or incomplete fixed frame scope")
    if not all(info["byte_identical"] for info in identities.values()):
        raise ValueError("logging replay does not reproduce original full/online trajectories")
    print(json.dumps(dict(output=str(output), rows=len(recorder.rows), identity=identities)))


if __name__ == "__main__":
    main()
