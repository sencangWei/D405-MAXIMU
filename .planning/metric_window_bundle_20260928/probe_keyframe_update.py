#!/usr/bin/env python3
"""Observation-only keyframe update wrapper for the frozen MASt3R frontend.

This adapter deliberately reuses ``probe_frontend_geometry.py`` for the frozen
input preflight, replay entrypoint, trajectory byte-identity checks, and NPZ
writing.  It only swaps in a narrower recorder/hook layer around the native
matcher -> scale_pointmaps -> opt_pose_calib_sim3 -> Frame.update_pointmap
boundary.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import numpy as np
import yaml


BASE_PATH = Path(__file__).with_name("probe_frontend_geometry.py")
FIRST, LAST, MAX_POINTS = 1000, 1120, 2048


def _load_base():
    spec = importlib.util.spec_from_file_location("_probe_frontend_geometry_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load frozen geometry probe from {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base()
_ORIGINAL_BASE_FROZEN_INPUTS = base.frozen_inputs


def _detach_clone(value: Any) -> Any:
    """Clone tensors without changing device/state; copy arrays for tests."""
    if value is None:
        return None
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "clone"):
        return value.clone()
    return np.array(value, copy=True)


def _as_numpy(value: Any) -> np.ndarray:
    if value is None:
        raise ValueError("cannot convert missing capture value")
    return base.as_numpy(value) if hasattr(value, "detach") else np.array(value, copy=True)


def _sim3_numpy(value: Any) -> np.ndarray:
    return _as_numpy(value.data).reshape(-1)


def _frame_id(frame: Any) -> int:
    return int(frame.frame_id)


def _extra_source_paths() -> list[Path]:
    paths = [Path(__file__).resolve()]
    for name in ("keyframe_update_diagnostics.py", "keyframe_update_math.py"):
        optional_pure_math = Path(__file__).with_name(name)
        if optional_pure_math.exists():
            paths.append(optional_pure_math.resolve())
    return paths


def frozen_inputs(frozen, manifest, dataset):
    """Extend the original frozen hash closure without replacing it."""
    if "config" in manifest and "toolchain" in manifest:
        validate_producer_config(manifest["config"], manifest["toolchain"])
    hashes = _ORIGINAL_BASE_FROZEN_INPUTS(frozen, manifest, dataset)
    hashes.update({str(path): base.digest(path) for path in _extra_source_paths()})
    return hashes


def _native_yaml_load(path):
    # Match MASt3R-SLAM's local float resolver without importing/mutating the
    # runtime config module.  This matters for values such as ``1e+1`` in base.
    loader = yaml.SafeLoader
    loader.add_implicit_resolver(
        "tag:yaml.org,2002:float",
        re.compile(
            """^(?:
                [-+]?(?:[0-9][0-9_]*)\\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                |\\.[0-9_]+(?:[eE][-+][0-9]+)?
                |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\\.[0-9_]*
                |[-+]?\\.(?:inf|Inf|INF)
                |\\.(?:nan|NaN|NAN))$""",
            re.X,
        ),
        list("-+0123456789."),
    )
    return yaml.load(Path(path).read_text(), Loader=loader) or {}


def _merge_like_native(parent, child):
    merged = dict(parent)
    for key, value in child.items():
        if isinstance(value, dict):
            merged[key] = _merge_like_native(merged.get(key, {}), value)
        else:
            merged[key] = value
    return merged


def effective_producer_config(path, toolchain):
    path = Path(path).resolve(strict=True)
    toolchain = Path(toolchain).resolve(strict=True)
    config = _native_yaml_load(path)
    inherit = config.get("inherit")
    if inherit != "config/base.yaml":
        raise ValueError(f"unexpected producer config inherit: {inherit!r}")
    inherited = (toolchain / inherit).resolve(strict=True)
    expected_parent = (toolchain / "config/base.yaml").resolve(strict=True)
    if inherited != expected_parent or not inherited.is_relative_to(toolchain):
        raise ValueError("producer config inherit does not resolve to toolchain config/base.yaml")
    parent = _native_yaml_load(inherited)
    return _merge_like_native(parent, config)


def validate_producer_config(path, toolchain):
    config = effective_producer_config(path, toolchain)
    tracking = config.get("tracking") or {}
    expected = {
        "single_thread": True,
        # The frozen wrapper passes --calib explicitly; the yaml key is a known
        # dead default and should stay False for this producer config.
        "use_calib": False,
        "tracking.stereo_pointmap_scale_prior": False,
        "tracking.stereo_pointmap_depth_anchor": False,
        "tracking.stereo_preserve_keyframe_pointmap": False,
        "tracking.filtering_mode": "weighted_pointmap",
    }
    actual = {
        "single_thread": config.get("single_thread"),
        "use_calib": config.get("use_calib"),
        "tracking.stereo_pointmap_scale_prior": tracking.get("stereo_pointmap_scale_prior", False),
        "tracking.stereo_pointmap_depth_anchor": tracking.get("stereo_pointmap_depth_anchor", False),
        "tracking.stereo_preserve_keyframe_pointmap": tracking.get("stereo_preserve_keyframe_pointmap", False),
        "tracking.filtering_mode": tracking.get("filtering_mode"),
    }
    bad = {key: actual[key] for key, value in expected.items() if actual[key] != value}
    if bad:
        raise ValueError(f"unexpected keyframe-update producer config: {bad}")
    return actual


def sample_ids_from_opt_valid(valid) -> np.ndarray:
    valid = _as_numpy(valid).reshape(-1).astype(bool)
    return base.sample_indices(valid, MAX_POINTS)


class Recorder(base.Recorder):
    def __init__(self, dataset, output, priors, provider):
        super().__init__(dataset, output, priors, provider)
        self.current_frame = None
        self._match = None
        self._scaled = None
        self._opt = None
        self._update = None
        self.ignored_current_updates = 0

    def reset_native_capture(self, frame):
        self.current_frame = frame
        self._match = None
        self._scaled = None
        self._opt = None
        self._update = None
        self.ignored_current_updates = 0

    def record_match(self, frame, keyframe, result):
        if self.context is None:
            return
        frame_id, keyframe_id = self.context
        if _frame_id(frame) != frame_id or _frame_id(keyframe) != keyframe_id:
            return
        if self._match is not None:
            self.errors.append(dict(frame_id=frame_id, stage="match", error="duplicate_keyframe_match_capture"))
            return
        self._match = dict(
            raw_Xkf_full=_detach_clone(result[5]),
            raw_Ckf_full=_detach_clone(result[6]),
        )

    def record_scale(self, pointmaps, confidence, result):
        if self.context is None or len(pointmaps) != 2:
            return
        frame_id = self.context[0]
        if self._scaled is not None:
            self.errors.append(dict(frame_id=frame_id, stage="scale", error="duplicate_keyframe_scale_capture"))
            return
        scaled_pointmaps = result[0]
        if len(scaled_pointmaps) != 2:
            self.errors.append(dict(frame_id=frame_id, stage="scale", error="unexpected_scaled_pointmap_count"))
            return
        if self._match is None:
            self.errors.append(dict(frame_id=frame_id, stage="scale", error="scale_seen_before_match"))
            return
        self._scaled = dict(
            working_Xkf_full=_detach_clone(scaled_pointmaps[1]),
            working_Ckf_full=_detach_clone(self._match["raw_Ckf_full"]),
        )

    def record_optimize_result(self, args, result):
        if self.context is None:
            return
        frame_id = self.context[0]
        if self._opt is not None:
            self.errors.append(dict(frame_id=frame_id, stage="optimize", error="duplicate_calib_opt_capture"))
            return
        ids = sample_ids_from_opt_valid(args[5])
        self._opt = dict(
            keyframe_pixel_ids=ids,
            # Kept for diagnostics only; the production update binding is derived
            # independently at Frame.update_pointmap, not claimed as intercepted.
            opt_T_CkCf=_sim3_numpy(result[1]),
        )

    def record_update(self, frame, X, C, original_update):
        if self.context is None:
            return original_update(frame, X, C)

        frame_id, keyframe_id = self.context
        target_id = _frame_id(frame)
        if target_id != keyframe_id:
            if target_id == frame_id:
                self.ignored_current_updates += 1
            return original_update(frame, X, C)

        if self._update is not None:
            self.errors.append(dict(frame_id=frame_id, stage="update", error="duplicate_keyframe_update_capture"))
            return original_update(frame, X, C)
        if self._opt is None:
            self.errors.append(dict(frame_id=frame_id, stage="update", error="keyframe_update_before_calib_opt"))
            return original_update(frame, X, C)

        ids = self._opt["keyframe_pixel_ids"]
        if ids.size == 0:
            self.errors.append(dict(frame_id=frame_id, stage="update", error="empty_optimize_sample"))
        missing = [
            name for name, source in (
                ("match", self._match),
                ("scale", self._scaled),
            )
            if source is None
        ]
        if missing:
            self.errors.append(dict(frame_id=frame_id, stage="update", error=f"missing_{'_'.join(missing)}_capture"))

        old_X = _as_numpy(frame.X_canon[ids])
        old_C = _as_numpy(frame.C[ids])
        proposal_X = _as_numpy(X[ids])
        proposal_C = _as_numpy(C[ids])
        n_before = int(frame.N)
        mode = None
        try:
            from mast3r_slam.config import config
            mode = str(config["tracking"]["filtering_mode"])
        except Exception as exc:  # pragma: no cover - real replay should have config.
            self.errors.append(dict(frame_id=frame_id, stage="update", error=f"filtering_mode_unavailable:{exc!r}"))

        try:
            result = original_update(frame, X, C)
        except Exception as exc:
            self.errors.append(dict(frame_id=frame_id, stage="update", error=f"native_exception:{exc!r}"))
            raise

        boundary = None
        if self.current_frame is not None:
            try:
                boundary = _sim3_numpy(frame.T_WC.inv() * self.current_frame.T_WC)
            except Exception as exc:
                self.errors.append(dict(frame_id=frame_id, stage="update", error=f"boundary_pose_unavailable:{exc!r}"))

        self._update = dict(
            raw_X_old=old_X,
            raw_C_old=old_C,
            raw_X_proposal=proposal_X,
            raw_C_new=proposal_C,
            raw_X_after=_as_numpy(frame.X_canon[ids]),
            raw_C_after=_as_numpy(frame.C[ids]),
            update_N_before=np.array(n_before, dtype=np.int64),
            update_N_after=np.array(int(frame.N), dtype=np.int64),
            update_filtering_mode=np.array(mode or "", dtype="U64"),
            update_T_boundary=np.array([] if boundary is None else boundary, dtype=float),
            update_T_boundary_semantics=np.array(
                "derived_keyframe_T_WC_inv_times_current_T_WC_not_direct_local_variable",
                dtype="U96",
            ),
            ignored_current_update_count=np.array(self.ignored_current_updates, dtype=np.int64),
        )
        return result

    def _sample_full_maps(self, ids: np.ndarray) -> dict[str, np.ndarray]:
        if self._match is None or self._scaled is None:
            raise ValueError("native matcher/scale captures missing")
        return dict(
            raw_Xkf_match=_as_numpy(self._match["raw_Xkf_full"][ids]),
            raw_Ckf_match=_as_numpy(self._match["raw_Ckf_full"][ids]),
            raw_Xkf_working=_as_numpy(self._scaled["working_Xkf_full"][ids]),
            raw_Ckf_working=_as_numpy(self._scaled["working_Ckf_full"][ids]),
        )

    def finish_track(self, tracker, frame, outcome):
        frame_id = self.context[0] if self.context is not None else None
        try:
            if self.captured is None:
                raise ValueError("base calibrated optimize capture missing")
            if self._opt is None:
                raise ValueError("native calib optimize capture missing")
            if self._update is None:
                raise ValueError("native keyframe update capture missing")
            ids = np.asarray(self._opt["keyframe_pixel_ids"], dtype=int)
            if not np.array_equal(np.asarray(self.captured["keyframe_pixel_ids"], dtype=int), ids):
                raise ValueError("base and native optimize sample IDs differ")
            self.captured.update(self._sample_full_maps(ids))
            self.captured.update(self._update)
        except Exception as exc:
            self.errors.append(dict(frame_id=frame_id, stage="finish_keyframe_update", error=repr(exc)))
        return super().finish_track(tracker, frame, outcome)


def install_hooks(tracker_class, recorder):
    """Install in-process observation hooks and return a full restore callback."""
    frame_module = __import__("mast3r_slam.frame", fromlist=["Frame"])
    tracker_module = __import__(tracker_class.__module__, fromlist=["mast3r_match_asymmetric"])

    original_track = tracker_class.track
    original_opt = tracker_class.opt_pose_calib_sim3
    original_scale = tracker_class.scale_pointmaps
    original_update = frame_module.Frame.update_pointmap
    original_matcher = tracker_module.mast3r_match_asymmetric

    def track(self, frame, *args, **kwargs):
        frame_id = _frame_id(frame)
        if not FIRST <= frame_id <= LAST:
            return original_track(self, frame, *args, **kwargs)
        recorder.context = (frame_id, _frame_id(self.keyframes.last_keyframe()))
        recorder.captured = None
        recorder.reset_native_capture(frame)
        try:
            outcome = original_track(self, frame, *args, **kwargs)
            try:
                recorder.finish_track(self, frame, outcome)
            except Exception as exc:
                recorder.errors.append(dict(frame_id=frame_id, stage="finish", error=repr(exc)))
            return outcome
        finally:
            recorder.context = None
            recorder.current_frame = None

    def matcher(model, frame, keyframe, *args, **kwargs):
        result = original_matcher(model, frame, keyframe, *args, **kwargs)
        recorder.record_match(frame, keyframe, result)
        return result

    def scale(self, pointmaps, confidence, metric_depth):
        result = original_scale(self, pointmaps, confidence, metric_depth)
        recorder.record_scale(pointmaps, confidence, result)
        return result

    def optimize(self, *args, **kwargs):
        capture = None
        if recorder.context is not None:
            try:
                capture = recorder.prepare_capture(self, args)
            except Exception as exc:
                recorder.errors.append(dict(frame_id=recorder.context[0], stage="prepare", error=repr(exc)))
        result = original_opt(self, *args, **kwargs)
        if capture is not None:
            try:
                capture["T_post"] = _sim3_numpy(result[1])
                recorder.captured = capture
            except Exception as exc:
                recorder.errors.append(dict(frame_id=recorder.context[0], stage="post", error=repr(exc)))
        recorder.record_optimize_result(args, result)
        return result

    def update(frame, X, C):
        return recorder.record_update(frame, X, C, original_update)

    tracker_class.track = track
    tracker_class.opt_pose_calib_sim3 = optimize
    tracker_class.scale_pointmaps = scale
    frame_module.Frame.update_pointmap = update
    tracker_module.mast3r_match_asymmetric = matcher

    def restore():
        tracker_module.mast3r_match_asymmetric = original_matcher
        frame_module.Frame.update_pointmap = original_update
        tracker_class.scale_pointmaps = original_scale
        tracker_class.opt_pose_calib_sim3 = original_opt
        tracker_class.track = original_track

    return restore


def main():
    original_recorder = base.Recorder
    original_install_hooks = base.install_hooks
    original_frozen_inputs = base.frozen_inputs
    try:
        base.Recorder = Recorder
        base.install_hooks = install_hooks
        base.frozen_inputs = frozen_inputs
        return base.main()
    finally:
        base.frozen_inputs = original_frozen_inputs
        base.install_hooks = original_install_hooks
        base.Recorder = original_recorder


if __name__ == "__main__":
    main()
