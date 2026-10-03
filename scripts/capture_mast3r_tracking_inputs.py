#!/usr/bin/env python3
"""Process-local MASt3R tracking input capture.

This is a partial diagnostic wrapper, not a full frontend or ATE acceptance run.
It monkey-patches methods in the current Python process only, writes bounded CPU
clones for selected actual frame ids, and restores the methods on exit.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import runpy
import sys
import traceback
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "mast3r_tracking_input_capture_v1"
_ACTIVE_ATTR = "_mast3r_tracking_input_capture_active_record"


def _torch():
    import torch

    return torch


def _is_tensor(value: Any) -> bool:
    try:
        return bool(_torch().is_tensor(value))
    except Exception:
        return False


def _snapshot(value: Any) -> Any:
    """Return a bounded, torch.save-friendly snapshot without mutating inputs."""
    if _is_tensor(value):
        return value.detach().cpu().clone()
    data = getattr(value, "data", None)
    if _is_tensor(data):
        return {"class": type(value).__name__, "data": data.detach().cpu().clone()}
    if isinstance(value, dict):
        return {str(k): _snapshot(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_snapshot(v) for v in value)
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return repr(value)


def _frame_id(frame: Any) -> int | None:
    value = getattr(frame, "frame_id", None)
    try:
        return None if value is None else int(value)
    except Exception:
        return None


def _error_payload(error: BaseException) -> dict[str, Any]:
    return {
        "type": type(error).__name__,
        "message": str(error),
        "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
    }


class CaptureWriter:
    def __init__(self, capture_dir: os.PathLike[str] | str, frame_ids: Iterable[int]):
        self.capture_dir = Path(capture_dir)
        self.frame_ids = {int(frame_id) for frame_id in frame_ids}
        self.attempts = {frame_id: 0 for frame_id in self.frame_ids}
        self.seen: set[int] = set()
        self.captures: list[dict[str, Any]] = []

    def next_path(self, frame_id: int) -> Path:
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        attempt = self.attempts.get(frame_id, 0) + 1
        self.attempts[frame_id] = attempt
        path = self.capture_dir / f"mast3r_tracking_frame{frame_id:06d}_attempt{attempt:03d}.pt"
        if path.exists():
            raise FileExistsError(path)
        return path

    def attempt_for(self, frame_id: int) -> int:
        return self.attempts.get(frame_id, 0)

    def missing_path(self, frame_id: int) -> Path:
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.capture_dir / f"mast3r_tracking_frame{frame_id:06d}_missing.pt"
        if path.exists():
            raise FileExistsError(path)
        return path

    def save(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("xb") as stream:
            _torch().save(payload, stream)

    def record_capture(self, frame_id: int, path: Path, payload: dict[str, Any]) -> None:
        status = "ERROR" if payload.get("status") == "error" else "CAPTURED"
        entry: dict[str, Any] = {
            "frame_id": int(frame_id),
            "attempt": self.attempt_for(frame_id),
            "path": path.name,
            "status": status,
        }
        if status == "ERROR":
            entry["error"] = payload.get("error", {})
        self.captures.append(entry)

    def _has_valid_opt_capture(self, payload_name: str) -> bool:
        if Path(payload_name).name != payload_name:
            return False
        payload_path = self.capture_dir / payload_name
        try:
            payload = _torch().load(payload_path, map_location="cpu", weights_only=True)
        except TypeError:
            payload = _torch().load(payload_path, map_location="cpu")
        except Exception:
            return False
        opt = payload.get("opt_pose_calib_sim3")
        return (
            payload.get("status") == "ok"
            and isinstance(opt, dict)
            and opt.get("return") is not None
        )

    def write_summary(self) -> None:
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        summary_path = self.capture_dir / "summary.json"
        if summary_path.exists():
            raise FileExistsError(summary_path)
        ok_frame_ids = {
            entry["frame_id"]
            for entry in self.captures
            if entry.get("status") == "CAPTURED"
            and self._has_valid_opt_capture(entry["path"])
        }
        any_error = any(entry.get("status") == "ERROR" for entry in self.captures)
        missing_frame_ids = sorted(self.frame_ids - ok_frame_ids)
        status = (
            "CAPTURED_NOT_SCORED"
            if not any_error and not missing_frame_ids
            else "INCOMPLETE"
        )
        import json

        summary = {
            "schema": "umi_mast3r_tracking_input_capture_v1",
            "requested_frame_ids": sorted(self.frame_ids),
            "captures": list(self.captures),
            "missing_frame_ids": missing_frame_ids,
            "status": status,
            "external_ground_truth_used": False,
            "precision_pass": False,
            "production_promoted": False,
        }
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_missing(self) -> None:
        for frame_id in sorted(self.frame_ids - self.seen):
            self.save(
                self.missing_path(frame_id),
                {"schema": SCHEMA, "status": "missing", "frame_id": frame_id},
            )


def _reference_frame(tracker: Any, reference_keyframe_index: Any) -> tuple[Any, Any]:
    keyframes = getattr(tracker, "keyframes", None)
    if keyframes is None:
        return None, None
    try:
        reference = (
            keyframes.last_keyframe()
            if reference_keyframe_index is None
            else keyframes[reference_keyframe_index]
        )
        return reference, _frame_id(reference)
    except Exception:
        return None, None


def _track_entry(tracker: Any, frame: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    reference_keyframe_index = kwargs.get("reference_keyframe_index")
    if len(args) >= 2 and reference_keyframe_index is None:
        reference_keyframe_index = args[1]
    reference, reference_frame_id = _reference_frame(tracker, reference_keyframe_index)
    return {
        "frame_id": _frame_id(frame),
        "frame_pose": _snapshot(getattr(frame, "T_WC", None)),
        "reference_keyframe_index": reference_keyframe_index,
        "reference_frame_id": reference_frame_id,
        "reference_pose": _snapshot(getattr(reference, "T_WC", None)),
        "update_reference": kwargs.get("update_reference", True),
        "cfg": _snapshot(getattr(tracker, "cfg", {})),
    }


def _track_return(result: Any) -> dict[str, Any]:
    if isinstance(result, tuple) and len(result) >= 3:
        return {
            "add_new_kf": _snapshot(result[0]),
            "try_reloc": _snapshot(result[2]),
        }
    return {"raw": _snapshot(result)}


def _active_record(tracker: Any) -> dict[str, Any] | None:
    record = getattr(tracker, _ACTIVE_ATTR, None)
    return record if isinstance(record, dict) else None


@contextlib.contextmanager
def capture_mast3r_tracking_inputs(
    frame_tracker_cls: type | None = None,
    *,
    capture_dir: os.PathLike[str] | str | None,
    frame_ids: Iterable[int],
    enabled: bool = True,
):
    """Wrap FrameTracker methods in-process and write selected frame snapshots."""
    frame_ids = {int(frame_id) for frame_id in frame_ids}
    if not enabled or capture_dir is None or not frame_ids:
        yield None
        return

    if frame_tracker_cls is None:
        from mast3r_slam.tracker import FrameTracker

        frame_tracker_cls = FrameTracker

    writer = CaptureWriter(capture_dir, frame_ids)
    originals = {
        "track": frame_tracker_cls.track,
        "get_points_poses": frame_tracker_cls.get_points_poses,
        "opt_pose_calib_sim3": frame_tracker_cls.opt_pose_calib_sim3,
        "solve_pose_increment": frame_tracker_cls.solve_pose_increment,
    }
    active_stack: list[dict[str, Any]] = []
    try:
        import mast3r_slam.mast3r_utils as mast3r_utils
    except Exception:
        mast3r_utils = None
    original_asymmetric_inference = (
        getattr(mast3r_utils, "mast3r_asymmetric_inference", None)
        if mast3r_utils is not None
        else None
    )

    def track_wrapper(self, frame, *args, **kwargs):
        frame_id = _frame_id(frame)
        if frame_id not in writer.frame_ids:
            return originals["track"](self, frame, *args, **kwargs)
        writer.seen.add(frame_id)
        path = writer.next_path(frame_id)
        record = {
            "schema": SCHEMA,
            "status": "started",
            "track_entry": _track_entry(self, frame, args, kwargs),
            "get_points_poses": None,
            "opt_pose_calib_sim3": None,
            "solve_pose_increment": [],
            "mast3r_asymmetric_inference": [],
        }
        previous = getattr(self, _ACTIVE_ATTR, None)
        setattr(self, _ACTIVE_ATTR, record)
        active_stack.append(record)
        try:
            result = originals["track"](self, frame, *args, **kwargs)
            record["track_return"] = _track_return(result)
            record["final_frame_pose"] = _snapshot(getattr(frame, "T_WC", None))
            opt_error = (record.get("opt_pose_calib_sim3") or {}).get("error")
            if opt_error is not None:
                record["status"] = "error"
                record["error"] = opt_error
            else:
                record["status"] = "ok"
            writer.save(path, record)
            writer.record_capture(frame_id, path, record)
            return result
        except BaseException as error:
            record["status"] = "error"
            record["error"] = _error_payload(error)
            writer.save(path, record)
            writer.record_capture(frame_id, path, record)
            raise
        finally:
            if active_stack and active_stack[-1] is record:
                active_stack.pop()
            elif record in active_stack:
                active_stack.remove(record)
            if previous is None:
                with contextlib.suppress(AttributeError):
                    delattr(self, _ACTIVE_ATTR)
            else:
                setattr(self, _ACTIVE_ATTR, previous)

    def get_points_poses_wrapper(self, frame, keyframe, idx_f2k, img_size, use_calib, K=None):
        result = originals["get_points_poses"](self, frame, keyframe, idx_f2k, img_size, use_calib, K)
        record = _active_record(self)
        if record is not None:
            record["get_points_poses"] = {
                "frame_id": _frame_id(frame),
                "keyframe_id": _frame_id(keyframe),
                "idx_f2k": _snapshot(idx_f2k),
                "img_size": _snapshot(img_size),
                "use_calib": bool(use_calib),
                "K": _snapshot(K),
                "current": {
                    "X_canon": _snapshot(getattr(frame, "X_canon", None)),
                    "confidence": _snapshot(frame.get_average_conf() if hasattr(frame, "get_average_conf") else None),
                    "T_WC": _snapshot(getattr(frame, "T_WC", None)),
                },
                "reference": {
                    "X_canon": _snapshot(getattr(keyframe, "X_canon", None)),
                    "confidence": _snapshot(keyframe.get_average_conf() if hasattr(keyframe, "get_average_conf") else None),
                    "T_WC": _snapshot(getattr(keyframe, "T_WC", None)),
                },
                "return": _snapshot(result),
            }
        return result

    def opt_pose_calib_sim3_wrapper(
        self, Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w, meas_k, valid_meas_k, K, img_size,
        metric_translation_target=None, metric_world_scale=1.0,
    ):
        record = _active_record(self)
        if record is None:
            return originals["opt_pose_calib_sim3"](
                self, Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w, meas_k, valid_meas_k,
                K, img_size, metric_translation_target, metric_world_scale,
            )
        section = {
            "Xf": _snapshot(Xf),
            "Xk": _snapshot(Xk),
            "T_WCf": _snapshot(T_WCf),
            "T_WCk": _snapshot(T_WCk),
            "Qk": _snapshot(Qk),
            "valid": _snapshot(valid),
            "conf_w": _snapshot(conf_w),
            "meas": _snapshot(meas_k),
            "valid_meas": _snapshot(valid_meas_k),
            "K": _snapshot(K),
            "img_size": _snapshot(img_size),
            "metric_translation_target": _snapshot(metric_translation_target),
            "metric_world_scale": float(metric_world_scale),
        }
        record["opt_pose_calib_sim3"] = section
        try:
            result = originals["opt_pose_calib_sim3"](
                self, Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w, meas_k, valid_meas_k, K,
                img_size, metric_translation_target, metric_world_scale,
            )
        except BaseException as error:
            section["error"] = _error_payload(error)
            raise
        section["return"] = {
            "T_WCf": _snapshot(result[0] if isinstance(result, tuple) and result else None),
            "T_CkCf": _snapshot(result[1] if isinstance(result, tuple) and len(result) > 1 else None),
        }
        return result

    def solve_pose_increment_wrapper(
        self, sqrt_info, r, J, visual_row_count=0, visual_effective_count=1
    ):
        result = originals["solve_pose_increment"](
            self, sqrt_info, r, J,
            visual_row_count=visual_row_count,
            visual_effective_count=visual_effective_count,
        )
        record = _active_record(self)
        if record is not None:
            record.setdefault("solve_pose_increment", []).append(
                {
                    "cost": float(result[1]),
                    "tau": _snapshot(result[0]),
                    "visual_row_count": int(visual_row_count),
                    "visual_effective_count": int(visual_effective_count),
                }
            )
        return result

    def mast3r_asymmetric_inference_wrapper(model, frame_i, frame_j):
        result = original_asymmetric_inference(model, frame_i, frame_j)
        if active_stack:
            record = active_stack[-1]
            record.setdefault("mast3r_asymmetric_inference", []).append(
                {
                    "frame_i": _frame_id(frame_i),
                    "frame_j": _frame_id(frame_j),
                    "X": _snapshot(result[0] if isinstance(result, tuple) and len(result) > 0 else None),
                    "C": _snapshot(result[1] if isinstance(result, tuple) and len(result) > 1 else None),
                    "D": _snapshot(result[2] if isinstance(result, tuple) and len(result) > 2 else None),
                    "Q": _snapshot(result[3] if isinstance(result, tuple) and len(result) > 3 else None),
                }
            )
        return result

    frame_tracker_cls.track = track_wrapper
    frame_tracker_cls.get_points_poses = get_points_poses_wrapper
    frame_tracker_cls.opt_pose_calib_sim3 = opt_pose_calib_sim3_wrapper
    frame_tracker_cls.solve_pose_increment = solve_pose_increment_wrapper
    if mast3r_utils is not None and original_asymmetric_inference is not None:
        mast3r_utils.mast3r_asymmetric_inference = mast3r_asymmetric_inference_wrapper
    try:
        yield writer
    finally:
        frame_tracker_cls.track = originals["track"]
        frame_tracker_cls.get_points_poses = originals["get_points_poses"]
        frame_tracker_cls.opt_pose_calib_sim3 = originals["opt_pose_calib_sim3"]
        frame_tracker_cls.solve_pose_increment = originals["solve_pose_increment"]
        if mast3r_utils is not None and original_asymmetric_inference is not None:
            mast3r_utils.mast3r_asymmetric_inference = original_asymmetric_inference
        writer.write_missing()
        writer.write_summary()


class PrefixDataset:
    """Length-limited proxy that delegates all other dataset behavior."""

    def __init__(self, dataset: Any, prefix_count: int):
        object.__setattr__(self, "_dataset", dataset)
        object.__setattr__(self, "_prefix_count", max(0, int(prefix_count)))

    def __len__(self):
        return min(len(self._dataset), self._prefix_count)

    def __getitem__(self, index):
        if index >= len(self):
            raise IndexError(index)
        return self._dataset[index]

    def __getattr__(self, name):
        return getattr(self._dataset, name)

    def __setattr__(self, name, value):
        if name in {"_dataset", "_prefix_count"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._dataset, name, value)

    def subsample(self, *args, **kwargs):
        return self._dataset.subsample(*args, **kwargs)


@contextlib.contextmanager
def _prefix_load_dataset(prefix_count: int | None):
    if prefix_count is None:
        yield
        return
    import mast3r_slam.dataloader as dataloader

    original = dataloader.load_dataset

    def wrapped_load_dataset(*args, **kwargs):
        dataset = original(*args, **kwargs)
        print(
            f"[capture_mast3r_tracking_inputs] PARTIAL DIAGNOSTIC: "
            f"limiting dataset length to prefix {prefix_count}; not a full frontend/ATE PASS."
        )
        return PrefixDataset(dataset, prefix_count)

    dataloader.load_dataset = wrapped_load_dataset
    try:
        yield
    finally:
        dataloader.load_dataset = original


def _parse_frame_ids(values: Iterable[str]) -> set[int]:
    frame_ids: set[int] = set()
    for value in values:
        frame_id = int(value)
        if frame_id < 0 or frame_id in frame_ids:
            raise argparse.ArgumentTypeError("frame ids must be unique non-negative integers")
        frame_ids.add(frame_id)
    return frame_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", required=True, help="new/empty diagnostic output directory")
    parser.add_argument("--frame-id", action="append", default=[], help="actual frame id to capture; repeatable")
    parser.add_argument("--prefix-count", type=int, default=None, help="optional dataset prefix length for partial diagnostic")
    parser.add_argument("original", nargs=argparse.REMAINDER, help="-- ORIGINAL_MAIN.py args...")
    args = parser.parse_args(argv)
    frame_ids = _parse_frame_ids(args.frame_id)
    if not args.original or args.original[0] != "--" or len(args.original) < 2:
        parser.error("expected: -- ORIGINAL_MAIN.py [args...]")
    original_main = Path(args.original[1]).resolve()
    original_argv = [str(original_main), *args.original[2:]]
    old_argv = sys.argv[:]
    old_sys_path = sys.path[:]
    old_main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    capture_dir = Path(args.capture_dir)
    if capture_dir.exists() and any(capture_dir.iterdir()):
        raise FileExistsError(f"--capture-dir must be new or empty: {capture_dir}")

    original_parent = str(original_main.parent)
    if original_parent not in sys.path:
        sys.path.insert(0, original_parent)
    try:
        with _prefix_load_dataset(args.prefix_count):
            from mast3r_slam.tracker import FrameTracker

            with capture_mast3r_tracking_inputs(
                FrameTracker,
                capture_dir=capture_dir,
                frame_ids=frame_ids,
                enabled=bool(frame_ids),
            ):
                try:
                    sys.argv = original_argv
                    if "__main__" in sys.modules:
                        sys.modules["__main__"].__file__ = str(original_main)
                    runpy.run_path(str(original_main), run_name="__main__")
                finally:
                    sys.argv = old_argv
                    if "__main__" in sys.modules:
                        if old_main_file is None:
                            with contextlib.suppress(AttributeError):
                                delattr(sys.modules["__main__"], "__file__")
                        else:
                            sys.modules["__main__"].__file__ = old_main_file
    finally:
        sys.path[:] = old_sys_path
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
