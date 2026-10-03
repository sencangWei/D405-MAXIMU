"""Default-off MASt3R reference-transition telemetry hook.

Enable by placing this directory on PYTHONPATH and setting both:
  MAST3R_REFERENCE_TRANSITION_FRAMES=<frame_id>[,<frame_id>...]
  MAST3R_REFERENCE_TRANSITION_PATH=<new .pt template with {frame_id} and {attempt}>

If MAST3R_EXPORT_SNAPSHOT_PATH is set, the sibling final-export hook is also
loaded.  This hook does not change tracker inputs, outputs, or state; it only
CPU-clones selected telemetry before/after FrameTracker.track calls.
"""
import os
import runpy
from pathlib import Path


_BASE = Path(__file__).resolve().parents[1]


if os.environ.get("MAST3R_EXPORT_SNAPSHOT_PATH") is not None:
    runpy.run_path(str(_BASE / "export_capture_hook" / "sitecustomize.py"))


def _enabled():
    return (
        os.environ.get("MAST3R_REFERENCE_TRANSITION_FRAMES") is not None
        and os.environ.get("MAST3R_REFERENCE_TRANSITION_PATH") is not None
    )


def _graph_enabled():
    return (
        os.environ.get("MAST3R_REFERENCE_TRANSITION_GRAPH_FRAMES") is not None
        and os.environ.get("MAST3R_REFERENCE_TRANSITION_GRAPH_PATH") is not None
    )


def _parse_frame_set(env_name):
    parts = os.environ[env_name].split(",")
    if not parts or any(part.strip() == "" for part in parts):
        raise ValueError(f"{env_name} contains an empty item")
    frames = [int(part) for part in parts]
    if len(set(frames)) != len(frames) or any(frame < 0 for frame in frames):
        raise ValueError(f"{env_name} must be unique non-negative integers")
    return set(frames)


if _enabled() or _graph_enabled():
    import torch


if _graph_enabled():
    from mast3r_slam import global_opt as _global_opt

    def _requested_graph_frames():
        frames = _parse_frame_set("MAST3R_REFERENCE_TRANSITION_GRAPH_FRAMES")
        template = os.environ["MAST3R_REFERENCE_TRANSITION_GRAPH_PATH"]
        if "{frame_id}" not in template:
            raise ValueError("MAST3R_REFERENCE_TRANSITION_GRAPH_PATH must contain {frame_id}")
        return frames, template

    _REQUESTED_GRAPH_FRAMES, _GRAPH_SNAPSHOT_TEMPLATE = _requested_graph_frames()

    def _clone_arg(value):
        return value.detach().cpu().clone() if torch.is_tensor(value) else value

    if not getattr(_global_opt.save_graph_snapshot_if_requested, "_reference_transition_graph_wrapped", False):
        _original_save_graph_snapshot_if_requested = _global_opt.save_graph_snapshot_if_requested

        def _save_graph_snapshot_with_reference_transition(frame_ids, args):
            result = _original_save_graph_snapshot_if_requested(frame_ids, args)
            if frame_ids and int(frame_ids[-1]) in _REQUESTED_GRAPH_FRAMES:
                frozen = tuple(_clone_arg(value) for value in args)
                frame_id = int(frame_ids[-1])
                payload = {
                    "frame_ids": [int(frame_id_value) for frame_id_value in frame_ids],
                    "args": frozen,
                }
                path = _GRAPH_SNAPSHOT_TEMPLATE.format(frame_id=frame_id)
                with open(path, "xb") as stream:
                    torch.save(payload, stream)
            return result

        _save_graph_snapshot_with_reference_transition._reference_transition_graph_wrapped = True
        _save_graph_snapshot_with_reference_transition._reference_transition_graph_original = (
            _original_save_graph_snapshot_if_requested
        )
        _global_opt.save_graph_snapshot_if_requested = _save_graph_snapshot_with_reference_transition


if _enabled():
    from mast3r_slam.tracker import FrameTracker

    def _requested_frames():
        frames = _parse_frame_set("MAST3R_REFERENCE_TRANSITION_FRAMES")
        template = os.environ["MAST3R_REFERENCE_TRANSITION_PATH"]
        if "{frame_id}" not in template or "{attempt}" not in template:
            raise ValueError("MAST3R_REFERENCE_TRANSITION_PATH must contain {frame_id} and {attempt}")
        return frames, template

    _REQUESTED_FRAMES, _SNAPSHOT_TEMPLATE = _requested_frames()
    _ATTEMPTS_BY_FRAME = {}
    _FRAME_FIELDS = (
        "img",
        "img_shape",
        "img_true_shape",
        "uimg",
        "X_canon",
        "C",
        "feat",
        "pos",
    )

    def _clone_tensor(name, value):
        if value is None or not torch.is_tensor(value):
            raise ValueError(f"reference transition missing tensor field {name}")
        clone = value.detach().cpu().clone()
        if not bool(torch.isfinite(clone).all().item()):
            raise ValueError(f"reference transition non-finite tensor field {name}")
        return clone

    def _clone_pose_data(name, pose):
        data = getattr(pose, "data", None)
        clone = _clone_tensor(name, data)
        if tuple(clone.shape) != (1, 8):
            raise ValueError(f"{name} must have shape (1, 8)")
        if abs(float(torch.linalg.vector_norm(clone[0, 3:7])) - 1.0) > 1e-3:
            raise ValueError(f"{name} quaternion is not unit length")
        if float(clone[0, 7]) <= 0.0:
            raise ValueError(f"{name} Sim3 scale must be positive")
        return clone

    def _reference_keyframe(tracker, reference_keyframe_index):
        if reference_keyframe_index is None:
            index = len(tracker.keyframes) - 1
            keyframe = tracker.keyframes.last_keyframe()
        else:
            index = int(reference_keyframe_index)
            keyframe = tracker.keyframes[index]
        return index, keyframe

    def _frame_tensors(prefix, frame, k_source=None):
        payload = {name: _clone_tensor(f"{prefix}.{name}", getattr(frame, name, None))
                   for name in _FRAME_FIELDS}
        source_for_k = frame if k_source is None else k_source
        k_value = source_for_k["K"] if isinstance(source_for_k, dict) else source_for_k.K
        payload.update(
            {
                "T_WC_data": _clone_pose_data(f"{prefix}.T_WC.data", frame.T_WC),
                "N": int(frame.N),
                "N_updates": int(frame.N_updates),
                "frame_id": int(frame.frame_id),
                "K": _clone_tensor(f"{prefix}.K", k_value),
            }
        )
        return payload

    def _reference_numeric(prefix, index, keyframe):
        return {
            "index": int(index),
            "frame_id": int(keyframe.frame_id),
            "T_WC_data": _clone_pose_data(f"{prefix}.T_WC.data", keyframe.T_WC),
        }

    def _keyframe_pose_snapshot(tracker):
        poses = []
        for index in range(len(tracker.keyframes)):
            keyframe = tracker.keyframes[index]
            poses.append(_reference_numeric(f"keyframes[{index}]", index, keyframe))
        return poses

    def _idx_f2k_summary(tracker):
        value = getattr(tracker, "idx_f2k", None)
        if value is None:
            return {"present": False}
        if torch.is_tensor(value):
            clone = _clone_tensor("tracker.idx_f2k", value)
            return {"present": True, "tensor": clone, "shape": tuple(clone.shape)}
        return {"present": True, "repr": repr(value)}

    def _next_attempt(frame_id):
        attempt = _ATTEMPTS_BY_FRAME.get(frame_id, 0)
        _ATTEMPTS_BY_FRAME[frame_id] = attempt + 1
        return attempt

    def _capture_success_fields(frame, reference_before_fields):
        return {
            "frame_after": _frame_tensors("frame", frame, reference_before_fields),
            "reference_before_fields": reference_before_fields,
        }

    def _save_snapshot(frame, attempt, before, after, success_fields):
        payload = {
            "schema": "mast3r_reference_transition_snapshot_v1",
            "requested_frame_id": int(frame.frame_id),
            "attempt": int(attempt),
            "diagnostic_only": True,
            "external_tracker_used": False,
            "production_promoted": False,
            "before": before,
            "after": after,
        }
        if success_fields is not None:
            payload["success_fields"] = success_fields
        path = _SNAPSHOT_TEMPLATE.format(frame_id=int(frame.frame_id), attempt=int(attempt))
        with open(path, "xb") as stream:
            torch.save(payload, stream)

    if not getattr(FrameTracker.track, "_reference_transition_wrapped", False):
        _original_track = FrameTracker.track

        def _track_with_reference_transition(
            self,
            frame,
            diagnostic_depth=None,
            reference_keyframe_index=None,
            update_reference=True,
        ):
            frame_id = int(frame.frame_id)
            selected = frame_id in _REQUESTED_FRAMES
            attempt = _next_attempt(frame_id) if selected else None
            reference_index = None
            reference_before = None
            reference_before_fields = None
            before = None
            if selected:
                reference_index, reference_before = _reference_keyframe(
                    self, reference_keyframe_index
                )
                reference_before_fields = _frame_tensors("reference_before", reference_before)
                before = {
                    "frame_id": frame_id,
                    "reference_keyframe_index_arg": (
                        None if reference_keyframe_index is None
                        else int(reference_keyframe_index)
                    ),
                    "resolved_reference": _reference_numeric(
                        "reference_before", reference_index, reference_before
                    ),
                    "input_frame_pose": _clone_pose_data("frame_before.T_WC.data", frame.T_WC),
                    "keyframe_poses": _keyframe_pose_snapshot(self),
                    "idx_f2k": _idx_f2k_summary(self),
                }
            result = _original_track(
                self,
                frame,
                diagnostic_depth=diagnostic_depth,
                reference_keyframe_index=reference_keyframe_index,
                update_reference=update_reference,
            )
            if selected:
                add_new_kf = bool(result[0])
                try_reloc = bool(result[2])
                after = {
                    "frame_id": frame_id,
                    "resolved_reference": _reference_numeric(
                        "reference_after", reference_index, reference_before
                    ),
                    "output_frame_pose": _clone_pose_data("frame_after.T_WC.data", frame.T_WC),
                    "track_return": {
                        "add_new_kf": add_new_kf,
                        "try_reloc": try_reloc,
                    },
                    "update_reference": bool(update_reference),
                }
                success_fields = None if try_reloc else _capture_success_fields(
                    frame, reference_before_fields
                )
                _save_snapshot(frame, attempt, before, after, success_fields)
            return result

        _track_with_reference_transition._reference_transition_wrapped = True
        _track_with_reference_transition._reference_transition_original = _original_track
        FrameTracker.track = _track_with_reference_transition
