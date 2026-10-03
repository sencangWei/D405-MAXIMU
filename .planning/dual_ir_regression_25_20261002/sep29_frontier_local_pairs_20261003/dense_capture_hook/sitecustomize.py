"""Default-off MASt3R tracking snapshot hook for selected dense frames.

Enable by placing this directory on PYTHONPATH and setting both:
  MAST3R_DENSE_SNAPSHOT_FRAME=<frame_id>[,<frame_id>...]
  MAST3R_DENSE_SNAPSHOT_PATH=<new .pt path or template containing {frame_id}>
"""
import os


def _enabled():
    return (
        os.environ.get("MAST3R_DENSE_SNAPSHOT_FRAME") is not None
        and os.environ.get("MAST3R_DENSE_SNAPSHOT_PATH") is not None
    )


if _enabled():
    import torch
    from mast3r_slam.tracker import FrameTracker

    def _requested_frames():
        parts = os.environ["MAST3R_DENSE_SNAPSHOT_FRAME"].split(",")
        if not parts or any(part.strip() == "" for part in parts):
            raise ValueError("MAST3R_DENSE_SNAPSHOT_FRAME contains an empty item")
        frames = [int(part) for part in parts]
        if len(set(frames)) != len(frames) or any(frame < 0 for frame in frames):
            raise ValueError("MAST3R_DENSE_SNAPSHOT_FRAME must be unique non-negative integers")
        path = os.environ["MAST3R_DENSE_SNAPSHOT_PATH"]
        if len(frames) > 1 and "{frame_id}" not in path:
            raise ValueError("MAST3R_DENSE_SNAPSHOT_PATH must contain {frame_id} for multiple frames")
        return set(frames), path

    _REQUESTED_FRAMES, _SNAPSHOT_PATH = _requested_frames()

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
            raise ValueError(f"dense snapshot missing tensor field {name}")
        clone = value.detach().cpu().clone()
        if not bool(torch.isfinite(clone).all().item()):
            raise ValueError(f"dense snapshot non-finite tensor field {name}")
        return clone

    def _clone_pose_data(name, pose):
        data = getattr(pose, "data", None)
        return _clone_tensor(name, data)

    def _reference_keyframe(tracker, reference_keyframe_index):
        if reference_keyframe_index is None:
            index = len(tracker.keyframes) - 1
            keyframe = tracker.keyframes.last_keyframe()
        else:
            index = int(reference_keyframe_index)
            keyframe = tracker.keyframes[index]
        return index, keyframe

    def _capture_snapshot(frame, reference_index, reference_keyframe, result):
        frame_id = int(frame.frame_id)
        frame_payload = {
            name: _clone_tensor(f"frame.{name}", getattr(frame, name, None))
            for name in _FRAME_FIELDS
        }
        frame_payload.update(
            {
                "T_WC_data": _clone_pose_data("frame.T_WC.data", frame.T_WC),
                "N": int(frame.N),
                "N_updates": int(frame.N_updates),
                "frame_id": int(frame.frame_id),
                "K": _clone_tensor("reference_keyframe.K", reference_keyframe.K),
            }
        )
        snapshot = {
            "schema": "mast3r_dense_frame_snapshot_v1",
            "requested_frame_id": frame_id,
            "frame": frame_payload,
            "reference": {
                "index": int(reference_index),
                "frame_id": int(reference_keyframe.frame_id),
                "T_WC_data": _clone_pose_data(
                    "reference_keyframe.T_WC.data", reference_keyframe.T_WC
                ),
            },
            "track_return": {
                "add_new_kf": bool(result[0]),
                "try_reloc": bool(result[2]),
            },
        }
        path = _SNAPSHOT_PATH.format(frame_id=frame_id)
        with open(path, "xb") as stream:
            torch.save(snapshot, stream)

    if not getattr(FrameTracker.track, "_dense_snapshot_wrapped", False):
        _original_track = FrameTracker.track

        def _track_with_dense_snapshot(
            self,
            frame,
            diagnostic_depth=None,
            reference_keyframe_index=None,
            update_reference=True,
        ):
            reference_index, reference_keyframe = _reference_keyframe(
                self, reference_keyframe_index
            )
            result = _original_track(
                self,
                frame,
                diagnostic_depth=diagnostic_depth,
                reference_keyframe_index=reference_keyframe_index,
                update_reference=update_reference,
            )
            if (
                update_reference
                and not bool(result[0])
                and isinstance(result, tuple)
                and len(result) >= 3
                and int(frame.frame_id) in _REQUESTED_FRAMES
                and result[2] is False
            ):
                _capture_snapshot(frame, reference_index, reference_keyframe, result)
            return result

        _track_with_dense_snapshot._dense_snapshot_wrapped = True
        _track_with_dense_snapshot._dense_snapshot_original = _original_track
        FrameTracker.track = _track_with_dense_snapshot
