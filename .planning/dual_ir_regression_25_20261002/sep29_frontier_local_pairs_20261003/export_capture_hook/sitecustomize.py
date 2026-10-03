"""Default-off final export snapshot hook for MASt3R native full trajectories.

Enable with this directory, the toolchain root, and its thirdparty/mast3r
directory on PYTHONPATH, and setting:
  MAST3R_EXPORT_SNAPSHOT_PATH=<new .pt path>

Before replay, use the toolchain Python to assert that save_full_traj has
_export_snapshot_wrapped=True. Python can silently ignore sitecustomize import
failures; the native runtime smoke check is required in addition to unit tests.
"""
import hashlib
import os
from pathlib import Path


def _enabled():
    return os.environ.get("MAST3R_EXPORT_SNAPSHOT_PATH") is not None


if _enabled():
    import torch
    import mast3r_slam.evaluate as _evaluate

    def _file_sha256(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _clone_sim3_row(name, data):
        if not torch.is_tensor(data):
            raise ValueError(f"{name} must be a tensor")
        if tuple(data.shape) != (1, 8):
            raise ValueError(f"{name} must have shape (1, 8)")
        clone = data.detach().cpu().clone()
        if not bool(torch.isfinite(clone).all().item()):
            raise ValueError(f"{name} contains non-finite values")
        if abs(float(torch.linalg.vector_norm(clone[0, 3:7])) - 1.0) > 1e-3:
            raise ValueError(f"{name} quaternion is not unit length")
        if float(clone[0, 7]) <= 0.0:
            raise ValueError(f"{name} Sim3 scale must be positive")
        return clone

    def _export_path(logdir, logfile):
        return Path(logdir) / logfile

    def _snapshot_payload(logdir, logfile, timestamps, frames, tracked_poses):
        export_path = _export_path(logdir, logfile)
        keyframes = []
        seen = set()
        for index in range(len(frames)):
            frame = frames[index]
            frame_id = int(frame.frame_id)
            if frame_id in seen:
                raise ValueError("duplicate keyframe frame_id")
            seen.add(frame_id)
            keyframes.append(
                {
                    "index": int(index),
                    "frame_id": frame_id,
                    "T_WC_data": _clone_sim3_row(
                        f"keyframes[{index}].T_WC.data", frame.T_WC.data
                    ),
                    "pose_device": str(frame.T_WC.data.device),
                    "pose_dtype": str(frame.T_WC.data.dtype),
                }
            )
        tracked = []
        tracked_frame_ids = set()
        for row_index, (frame_id, anchor_idx, relative_pose_data) in enumerate(tracked_poses):
            frame_id = int(frame_id)
            anchor_idx = int(anchor_idx)
            if frame_id in tracked_frame_ids:
                raise ValueError("duplicate tracked pose frame_id")
            tracked_frame_ids.add(frame_id)
            if anchor_idx < 0 or anchor_idx >= len(keyframes):
                raise ValueError("tracked pose anchor index out of bounds")
            if frame_id < 0 or frame_id >= len(timestamps):
                raise ValueError("tracked pose frame_id out of timestamp bounds")
            timestamp_float = float(timestamps[frame_id])
            if not torch.isfinite(torch.as_tensor(timestamp_float)):
                raise ValueError("tracked pose timestamp is non-finite")
            tracked.append(
                {
                    "frame_id": frame_id,
                    "anchor_idx": anchor_idx,
                    "anchor_frame_id": int(keyframes[anchor_idx]["frame_id"]),
                    "timestamp_text": f"{timestamps[frame_id]}",
                    "timestamp_float": timestamp_float,
                    "relative_pose_data": _clone_sim3_row(
                        f"tracked_poses[{row_index}].relative", relative_pose_data
                    ),
                    "relative_device": str(relative_pose_data.device),
                    "relative_dtype": str(relative_pose_data.dtype),
                }
            )
        return {
            "schema": "mast3r_native_full_export_snapshot_v1",
            "original_export_path": str(export_path),
            "original_export_sha256": _file_sha256(export_path),
            "keyframes": keyframes,
            "tracked_poses": tracked,
            "keyframe_count": int(len(keyframes)),
            "tracked_pose_count": int(len(tracked)),
        }

    if not getattr(_evaluate.save_full_traj, "_export_snapshot_wrapped", False):
        _original_save_full_traj = _evaluate.save_full_traj

        def _save_full_traj_with_snapshot(logdir, logfile, timestamps, frames, tracked_poses):
            result = _original_save_full_traj(logdir, logfile, timestamps, frames, tracked_poses)
            payload = _snapshot_payload(logdir, logfile, timestamps, frames, tracked_poses)
            with open(os.environ["MAST3R_EXPORT_SNAPSHOT_PATH"], "xb") as stream:
                torch.save(payload, stream)
            return result

        _save_full_traj_with_snapshot._export_snapshot_wrapped = True
        _save_full_traj_with_snapshot._export_snapshot_original = _original_save_full_traj
        _evaluate.save_full_traj = _save_full_traj_with_snapshot
