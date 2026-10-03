"""Default-off offline calibrated-GN adapter; onboard stereo only.

This keeps the retained source-trial objective intact. Only immutable raw
stereo depths are cached; metric factors are rebuilt for current pointmaps.
It is an experimental frontend entry, not a production acceptance claim.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import torch


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from joint_metric_native_solver import array_hash, solve_joint  # noqa: E402
from metric_relative_pose_factor import prepare_factors  # noqa: E402
from probe_stereo_depth_shape_native_graph import load_depths, pose_check, sha  # noqa: E402
from probe_dense_native_graph import _validate_graph_args  # noqa: E402


SCHEMA = "umi_metric_relative_joint_frontend_context_v1"
TOOL = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
CODE_PATHS = {
    "adapter": Path(__file__),
    **{name: BASE / (filename + ".py") for name, filename in (
        ("metric_factor", "metric_relative_pose_factor"), ("joint_solver", "joint_metric_native_solver"),
        ("depth_loader", "probe_stereo_depth_shape_native_graph"), ("depth_shape", "condition_native_pointmap_depth_shape"),
        ("graph_helper", "probe_dense_native_graph"), ("pnp_mask", "pnp_supported_outlier_mask"),
        ("pnp_audit", "audit_pnp_match_partition"),
    )},
    **{name: BASE / filename for name, filename in (
        ("build_backend", "native_derivative_audit_v1/build_backend.py"),
        ("pinned_build", "fixed_scale_native_backend_v1/build_backend.py"),
        ("inspect_cpp", "native_derivative_audit_v1/inspect.cpp"),
        ("inspect_cu", "native_derivative_audit_v1/inspect.cu"),
    )},
    **{name: TOOL / filename for name, filename in (
        ("main", "main.py"), ("global_opt", "mast3r_slam/global_opt.py"),
        ("tracker", "mast3r_slam/tracker.py"), ("stereo_depth", "mast3r_slam/stereo_depth.py"),
        ("mast3r_utils", "mast3r_slam/mast3r_utils.py"),
        ("gn_cpp", "mast3r_slam/backend/src/gn.cpp"), ("gn_cu", "mast3r_slam/backend/src/gn_kernels.cu"),
        ("gn_h", "mast3r_slam/backend/include/gn.h"),
        ("native_backend", "mast3r_slam_backends.cpython-310-x86_64-linux-gnu.so"),
    )},
}
_runtime = None


def context_for_source(dataset: Path, paired_left_dataset: Path, eye: str):
    """Bind a new source-only experiment to exact current files/code."""
    native, paired = dataset.resolve(strict=True), paired_left_dataset.resolve(strict=True)
    bindings = {
        prefix + key: directory / filename
        for prefix, directory in (("native_", native), ("paired_", paired))
        for key, filename in (("manifest", "dataset_manifest.json"), ("frames", "frames.csv"), ("calibration", "calibration.yaml"))
    }
    return {
        "schema": SCHEMA, "external_ground_truth_used": False,
        "dataset": str(native), "paired_left_dataset": str(paired), "eye": eye,
        "input_sha256": {name: sha(path) for name, path in bindings.items()},
        "code_sha256": {name: sha(path) for name, path in CODE_PATHS.items()},
    }


class Runtime:
    def __init__(self, context_path: Path, log_path: Path):
        self.context_path = context_path.resolve(strict=True)
        self.context = json.loads(self.context_path.read_text())
        required = {"schema", "external_ground_truth_used", "dataset", "paired_left_dataset", "eye", "input_sha256", "code_sha256"}
        if set(self.context) != required or self.context["schema"] != SCHEMA:
            raise ValueError("joint frontend context fields/schema mismatch")
        if self.context["external_ground_truth_used"] is not False or self.context["eye"] not in ("left", "right"):
            raise ValueError("joint frontend context must be onboard-only and eye-labelled")
        self.source = self.context
        native = Path(self.source["dataset"]).resolve(strict=True)
        paired = Path(self.source["paired_left_dataset"]).resolve(strict=True)
        self.bindings = {
            "native_manifest": native / "dataset_manifest.json",
            "paired_manifest": paired / "dataset_manifest.json",
            "native_frames": native / "frames.csv",
            "paired_frames": paired / "frames.csv",
            "native_calibration": native / "calibration.yaml",
            "paired_calibration": paired / "calibration.yaml",
        }
        if set(self.source["input_sha256"]) != set(self.bindings):
            raise ValueError("joint frontend source bindings missing/extra")
        if set(self.source["code_sha256"]) != set(CODE_PATHS):
            raise ValueError("joint frontend code bindings missing/extra")
        self.context_sha256 = sha(self.context_path)
        self.verify_bindings()
        with self.bindings["native_frames"].open() as stream:
            self.rows = list(csv.DictReader(stream))
        self.native, self.paired = native, paired
        self.right_directory = json.loads(self.bindings["paired_manifest"].read_text())["stereo_depth_source"]["right_directory"]
        self.depth_cache = {}
        self.image_identities = {}
        self.image_geometry = None
        self.backend = None
        # A new experiment gets a new log; never append to a previous recording.
        self.log = log_path.open("x", encoding="utf-8")

    def verify_bindings(self):
        if sha(self.context_path) != self.context_sha256:
            raise ValueError("joint frontend context changed")
        for name, path in self.bindings.items():
            if sha(path) != self.source["input_sha256"][name]:
                raise ValueError("joint frontend source changed: " + name)
        for name, path in CODE_PATHS.items():
            if sha(path) != self.source["code_sha256"][name]:
                raise ValueError("joint frontend code changed: " + name)

    def image_identity(self, fid):
        row = self.rows[fid]
        if int(row["input_index"]) != fid:
            raise ValueError("joint frontend source input index mismatch")
        paths = (self.native / row["image"], self.paired / row["image"], self.paired / self.right_directory / row["image"])
        identities = []
        for path in paths:
            stat = path.stat()
            identities.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return tuple(identities)

    def depths(self, frame_ids, args):
        geometry = (int(args[9]), int(args[10]), array_hash(args[3].detach().cpu().numpy()))
        if self.image_geometry is not None and self.image_geometry != geometry:
            raise ValueError("joint frontend image shape/K changed")
        identities = {fid: self.image_identity(fid) for fid in frame_ids}
        for fid in frame_ids:
            if fid in self.image_identities and identities[fid] != self.image_identities[fid]:
                raise ValueError("joint frontend cached source image changed: " + str(fid))
        missing = [fid for fid in frame_ids if fid not in self.depth_cache]
        if missing:
            depth, _, _ = load_depths(self.source, {"frame_ids": missing, "args": args})
            if any(self.image_identity(fid) != identities[fid] for fid in missing):
                raise ValueError("joint frontend source image changed during depth load")
            self.depth_cache.update(zip(missing, depth))
            self.image_identities.update({fid: identities[fid] for fid in missing})
        self.image_geometry = geometry
        return torch.stack([self.depth_cache[fid] for fid in frame_ids])

    def inspection_backend(self):
        if self.backend is None:
            path = BASE / "native_derivative_audit_v1/build_backend.py"
            spec = importlib.util.spec_from_file_location("umi_joint_native_inspection_build", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.backend = module.build_extension()
        return self.backend

    def solve(self, frame_ids, args):
        started = time.monotonic()
        if len(args) != 19 or len(frame_ids) != int(args[0].shape[0]):
            raise ValueError("joint frontend graph dimensions differ")
        if len(frame_ids) < 2 or sorted(set(frame_ids)) != list(frame_ids):
            raise ValueError("joint frontend frame ids must be unique and ordered")
        self.verify_bindings()
        _validate_graph_args(torch, args)
        frozen = tuple(value.detach().cpu().clone() if isinstance(value, torch.Tensor) else value for value in args)
        initial = frozen[0].clone()
        depth = self.depths(frame_ids, frozen)
        depth_elapsed = time.monotonic() - started
        factors, measurements = prepare_factors({"frame_ids": list(frame_ids), "args": frozen}, depth)
        prepared = time.monotonic()
        if not factors:
            raise ValueError("joint frontend has no accepted metric pairs; candidate solve unavailable")
        poses, trace = solve_joint(frozen, factors, self.inspection_backend())
        mode = "JOINT_METRIC_NATIVE"
        pose_check(poses)
        if not torch.equal(poses[0], initial[0]):
            raise ValueError("joint frontend pin moved")
        self.verify_bindings()
        record = {
            "schema": "umi_metric_relative_joint_frontend_solve_v1",
            "external_ground_truth_used": False, "precision_pass": False,
            "mode": mode, "eye": self.source["eye"], "frame_ids": list(frame_ids),
            "context_sha256": self.context_sha256, "adapter_sha256": sha(__file__),
            "code_sha256": self.source["code_sha256"],
            "pose_before_sha256": array_hash(initial.numpy()), "pose_after_sha256": array_hash(poses.numpy()),
            "factor_count": len(factors), "accepted_pair_count": measurements["accepted_pair_count"],
            "rejected_pair_count": measurements["rejected_pair_count"],
            "cached_depth_frames": len(self.depth_cache), "depth_s": depth_elapsed,
            "prepare_s": prepared - started - depth_elapsed,
            "solve_s": time.monotonic() - prepared, "iteration_trace": trace,
        }
        self.log.write(json.dumps(record, allow_nan=False) + "\n")
        self.log.flush()
        return poses


def solve_calibrated(frame_ids, args):
    global _runtime
    if _runtime is None:
        context = os.environ.get("MAST3R_METRIC_RELATIVE_JOINT_CONTEXT")
        log = os.environ.get("MAST3R_METRIC_RELATIVE_JOINT_LOG")
        if not context or not log:
            raise ValueError("joint frontend requires explicit source context and new solve log")
        _runtime = Runtime(Path(context), Path(log))
    return _runtime.solve(frame_ids, args)
