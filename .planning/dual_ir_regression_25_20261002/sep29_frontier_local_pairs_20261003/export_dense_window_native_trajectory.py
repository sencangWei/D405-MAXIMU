"""Export full native trajectories from a dense-window GN diagnostic.

This is diagnostic-only glue.  It replays the captured MASt3R native
``save_full_traj`` path for the original export and for a copied dense-window
variant without changing solver/source artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA = "mast3r_native_full_export_snapshot_v1"
PROBE_SCHEMA = "dense_window_native_graph_probe_v1"
OUTPUT_SCHEMA = "dense_window_native_trajectory_export_v1"
DEFAULT_EXPECTED_FRAME_COUNT = 1199
DEFAULT_EXPECTED_WINDOW_IDS = list(range(791, 810))


def _file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _load_torch_snapshot(torch: Any, path: Path) -> dict[str, Any]:
    return torch.load(path, map_location="cpu", weights_only=True)


def _sim3_row(torch: Any, name: str, value: Any, *, allow_vector: bool = False) -> Any:
    if not torch.is_tensor(value):
        value = torch.as_tensor(value)
    if allow_vector and tuple(value.shape) == (8,):
        value = value.reshape(1, 8)
    if tuple(value.shape) != (1, 8):
        raise ValueError(f"{name} must have shape (1, 8)")
    row = value.detach().cpu().clone()
    if not bool(torch.isfinite(row).all().item()):
        raise ValueError(f"{name} contains non-finite values")
    if abs(float(torch.linalg.vector_norm(row[0, 3:7])) - 1.0) > 1e-3:
        raise ValueError(f"{name} quaternion is not unit length")
    if float(row[0, 7]) <= 0.0:
        raise ValueError(f"{name} Sim3 scale must be positive")
    return row


def _sim3_identity(torch: Any, dtype: Any) -> Any:
    row = torch.zeros(1, 8, dtype=dtype)
    row[0, 6] = 1.0
    row[0, 7] = 1.0
    return row


def _strict_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def _strict_source_run(probe: dict[str, Any]) -> Path:
    value = probe.get("inputs", {}).get("source_run")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("probe inputs.source_run must be a non-empty string")
    source_run = Path(value).resolve()
    if not source_run.is_dir():
        raise ValueError("probe inputs.source_run must be an existing directory")
    return source_run


def validate_snapshot(
    torch: Any,
    snapshot: dict[str, Any],
    *,
    expected_frame_count: int = DEFAULT_EXPECTED_FRAME_COUNT,
) -> dict[str, Any]:
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("unexpected snapshot schema")
    keyframes = snapshot.get("keyframes")
    tracked = snapshot.get("tracked_poses")
    if not isinstance(keyframes, list) or not keyframes:
        raise ValueError("snapshot keyframes must be a non-empty list")
    if not isinstance(tracked, list) or not tracked:
        raise ValueError("snapshot tracked_poses must be a non-empty list")
    if int(snapshot.get("keyframe_count", -1)) != len(keyframes):
        raise ValueError("snapshot keyframe_count mismatch")
    if int(snapshot.get("tracked_pose_count", -1)) != len(tracked):
        raise ValueError("snapshot tracked_pose_count mismatch")
    if expected_frame_count is not None and len(tracked) != int(expected_frame_count):
        raise ValueError(f"snapshot tracked pose count must be {expected_frame_count}")

    keyframe_ids = []
    for expected_index, row in enumerate(keyframes):
        if int(row.get("index", -1)) != expected_index:
            raise ValueError("snapshot keyframe indices must be ordered")
        frame_id = int(row["frame_id"])
        keyframe_ids.append(frame_id)
        row["T_WC_data"] = _sim3_row(torch, f"keyframes[{expected_index}].T_WC_data", row["T_WC_data"])
    if len(keyframe_ids) != len(set(keyframe_ids)):
        raise ValueError("snapshot keyframe frame IDs must be unique")

    tracked_ids = []
    timestamp_floats = []
    for row_index, row in enumerate(tracked):
        frame_id = int(row["frame_id"])
        tracked_ids.append(frame_id)
        if row.get("timestamp_text") is None:
            raise ValueError("tracked pose missing timestamp_text")
        timestamp_text_float = float(row["timestamp_text"])
        if not bool(torch.isfinite(torch.as_tensor(timestamp_text_float)).item()):
            raise ValueError("tracked pose timestamp is non-finite")
        if "timestamp_float" not in row:
            raise ValueError("tracked pose missing timestamp_float")
        timestamp_float = float(row["timestamp_float"])
        if not bool(torch.isfinite(torch.as_tensor(timestamp_float)).item()):
            raise ValueError("tracked pose timestamp is non-finite")
        # RGBFiles uses numpy.float32 timestamps. Its shortest native writer
        # text round-trips at float32 precision, not necessarily Python float64.
        # Preserve that exact text for export; do not shift source timestamps.
        if (
            timestamp_text_float != timestamp_float
            and float(torch.tensor(timestamp_text_float, dtype=torch.float32)) != timestamp_float
        ):
            raise ValueError("tracked pose timestamp_text and timestamp_float differ")
        timestamp_floats.append(timestamp_float)
        anchor_idx = int(row["anchor_idx"])
        if anchor_idx < 0 or anchor_idx >= len(keyframes):
            raise ValueError("tracked pose anchor index out of bounds")
        if int(row["anchor_frame_id"]) != keyframe_ids[anchor_idx]:
            raise ValueError("tracked pose anchor_frame_id mismatch")
        row["relative_pose_data"] = _sim3_row(
            torch, f"tracked_poses[{row_index}].relative_pose_data", row["relative_pose_data"]
        )
    if tracked_ids != list(range(len(tracked_ids))):
        raise ValueError("tracked frame IDs must be chronological full 0..N-1")
    if any(b <= a for a, b in zip(timestamp_floats, timestamp_floats[1:])):
        raise ValueError("tracked timestamps must be strictly increasing")
    return snapshot


def validate_probe(
    torch: Any,
    probe: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    expected_window_ids: list[int] | None = DEFAULT_EXPECTED_WINDOW_IDS,
) -> dict[str, Any]:
    if probe.get("schema") != PROBE_SCHEMA:
        raise ValueError("unexpected probe schema")
    if probe.get("status") != "DENSE_WINDOW_SOLVED":
        raise ValueError("probe must be DENSE_WINDOW_SOLVED")
    if probe.get("diagnostic_only") is not True:
        raise ValueError("probe diagnostic_only must be true")
    for flag in ("external_ground_truth_used", "external_tracker_used", "production_promoted", "precision_pass"):
        if probe.get(flag) is not False:
            raise ValueError(f"probe {flag} must be false")
    unchanged = probe.get("original_edges_unchanged_in_variant")
    expected_edge_keys = {str(i) for i in range(4, 9)}
    if not isinstance(unchanged, dict) or set(unchanged) != expected_edge_keys:
        raise ValueError("probe original edge prefix must include exactly keys 4..8")
    if not all(value is True for value in unchanged.values()):
        raise ValueError("probe original edge prefix must be exactly true")

    original_graph_ids = [int(v) for v in probe.get("original_graph_frame_ids", [])]
    keyframe_ids = [int(row["frame_id"]) for row in snapshot["keyframes"]]
    if original_graph_ids != keyframe_ids:
        raise ValueError("probe original_graph_frame_ids differ from snapshot keyframes")

    source_run = _strict_source_run(probe)
    export_path = Path(snapshot["original_export_path"]).resolve()
    if not source_run or not _is_relative_to(export_path, source_run):
        raise ValueError("probe source_run is not an ancestor of snapshot original export")
    if not export_path.is_file():
        raise ValueError("snapshot original export path does not exist")
    if _file_sha256(export_path) != snapshot["original_export_sha256"]:
        raise ValueError("snapshot original export SHA does not match path")

    window_ids = [_strict_int(f"window_frame_ids[{idx}]", value) for idx, value in enumerate(probe.get("window_frame_ids", []))]
    if expected_window_ids is not None and window_ids != [int(v) for v in expected_window_ids]:
        raise ValueError("probe window_frame_ids do not match expected diagnostic window")
    if len(window_ids) < 3:
        raise ValueError("probe window_frame_ids must contain at least parent, dense, next")
    if len(window_ids) != len(set(window_ids)) or any(b != a + 1 for a, b in zip(window_ids, window_ids[1:])):
        raise ValueError("probe window_frame_ids must be strictly contiguous unique integers")
    if window_ids[0] not in keyframe_ids or window_ids[-1] not in keyframe_ids:
        raise ValueError("probe window endpoints must be graph keyframes")
    interior_ids = window_ids[1:-1]
    if any(frame_id in keyframe_ids for frame_id in interior_ids):
        raise ValueError("probe dense window interior must not already be graph keyframes")
    if any(frame_id < 0 or frame_id >= len(snapshot["tracked_poses"]) for frame_id in interior_ids):
        raise ValueError("probe dense window interior must exist in tracked timeline")

    dense_snapshots = probe.get("inputs", {}).get("dense_snapshots")
    if not isinstance(dense_snapshots, dict):
        raise ValueError("probe inputs.dense_snapshots must be present")
    for frame_id in interior_ids:
        entry = dense_snapshots.get(str(frame_id))
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("sha256"):
            raise ValueError(f"probe missing dense snapshot binding for frame {frame_id}")
        dense_path = Path(entry["path"])
        if not dense_path.is_file():
            raise ValueError(f"probe dense snapshot path missing for frame {frame_id}")
        if _file_sha256(dense_path) != entry["sha256"]:
            raise ValueError(f"probe dense snapshot SHA mismatch for frame {frame_id}")

    if probe.get("dense_edges_added") is not True:
        raise ValueError("probe dense_edges_added must be true")
    edge_matches = probe.get("dense_edge_matches")
    expected_pairs = list(zip(window_ids[:-1], window_ids[1:]))
    if not isinstance(edge_matches, list) or len(edge_matches) != len(expected_pairs):
        raise ValueError("probe dense_edge_matches must cover every adjacent window pair")
    for idx, (match, (first, second)) in enumerate(zip(edge_matches, expected_pairs)):
        if int(match.get("first", -1)) != first or int(match.get("second", -1)) != second:
            raise ValueError("probe dense edge match pair order mismatch")
        accepted = match.get("accepted")
        if not isinstance(accepted, list) or len(accepted) != 1 or accepted[0] is not True:
            raise ValueError(f"probe dense edge match {idx} must be accepted exactly [True]")

    pose_states = probe.get("pose_states")
    if not isinstance(pose_states, dict):
        raise ValueError("probe pose_states must be a dict")
    max_delta = 0.0
    for idx, frame_id in enumerate(keyframe_ids):
        state = pose_states.get(str(frame_id))
        if not isinstance(state, dict):
            raise ValueError(f"probe missing pose state for frame {frame_id}")
        baseline = _sim3_row(
            torch, f"pose_states[{frame_id}].baseline_after", state.get("baseline_after"),
            allow_vector=True,
        )
        if not torch.equal(baseline, snapshot["keyframes"][idx]["T_WC_data"]):
            delta = torch.max(torch.abs(baseline - snapshot["keyframes"][idx]["T_WC_data"]))
            raise ValueError(f"probe baseline_after differs from snapshot keyframe {frame_id}: {float(delta)}")
        max_delta = max(max_delta, float(torch.max(torch.abs(baseline - snapshot["keyframes"][idx]["T_WC_data"]))))
        _sim3_row(
            torch, f"pose_states[{frame_id}].variant_after", state.get("variant_after"),
            allow_vector=True,
        )
    for frame_id in interior_ids:
        state = pose_states.get(str(frame_id))
        if not isinstance(state, dict):
            raise ValueError(f"probe missing dense window pose state {frame_id}")
        _sim3_row(
            torch, f"pose_states[{frame_id}].variant_after", state.get("variant_after"),
            allow_vector=True,
        )
    probe["_validated_max_baseline_pose_delta"] = max_delta
    return probe


class ExportFrame:
    def __init__(self, frame_id: int, T_WC: Any):
        self.frame_id = frame_id
        self.T_WC = T_WC


def _timestamps(snapshot: dict[str, Any]) -> list[str]:
    # Native save_full_traj uses f-string formatting, which differs from str()
    # for numpy.float32. Reconstruct that exact representation from the captured
    # scalar, including snapshots made before the hook's formatting correction.
    return [f"{row['timestamp_float']}" for row in snapshot["tracked_poses"]]


def _make_frames(sim3_factory: Any, keyframes: list[dict[str, Any]]) -> list[ExportFrame]:
    return [
        ExportFrame(
            int(row["frame_id"]),
            sim3_factory(row["T_WC_data"].clone().to(row.get("pose_device", "cpu"))),
        )
        for row in keyframes
    ]


def _baseline_inputs(snapshot: dict[str, Any], sim3_factory: Any) -> tuple[list[str], list[ExportFrame], list[tuple[int, int, Any]]]:
    tracked = [
        (int(row["frame_id"]), int(row["anchor_idx"]), row["relative_pose_data"].clone())
        for row in snapshot["tracked_poses"]
    ]
    return _timestamps(snapshot), _make_frames(sim3_factory, snapshot["keyframes"]), tracked


def _variant_keyframes(torch: Any, snapshot: dict[str, Any], probe: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, int]]:
    states = probe["pose_states"]
    keyframes = []
    frame_to_new_anchor = {}
    for index, row in enumerate(snapshot["keyframes"]):
        frame_id = int(row["frame_id"])
        keyframes.append({
            "index": index,
            "frame_id": frame_id,
            "pose_device": row.get("pose_device", "cpu"),
            "T_WC_data": _sim3_row(
                torch, f"variant keyframe {frame_id}", states[str(frame_id)]["variant_after"],
                allow_vector=True,
            ).to(dtype=row["T_WC_data"].dtype),
        })
        frame_to_new_anchor[frame_id] = index
    dense_dtype = snapshot["keyframes"][0]["T_WC_data"].dtype
    for frame_id in [int(v) for v in probe.get("window_frame_ids", [])[1:-1]]:
        if frame_id in frame_to_new_anchor:
            continue
        state = states.get(str(frame_id))
        if not isinstance(state, dict):
            raise ValueError(f"probe missing dense window pose state {frame_id}")
        index = len(keyframes)
        keyframes.append({
            "index": index,
            "frame_id": frame_id,
            "pose_device": snapshot["keyframes"][
                snapshot["tracked_poses"][frame_id]["anchor_idx"]
            ].get("pose_device", "cpu"),
            "T_WC_data": _sim3_row(
                torch, f"dense variant keyframe {frame_id}", state.get("variant_after"),
                allow_vector=True,
            ).to(dtype=dense_dtype),
        })
        frame_to_new_anchor[frame_id] = index
    return keyframes, frame_to_new_anchor


def _variant_inputs(
    torch: Any, snapshot: dict[str, Any], probe: dict[str, Any], sim3_factory: Any
) -> tuple[list[str], list[ExportFrame], list[tuple[int, int, Any]], list[int]]:
    keyframes, frame_to_new_anchor = _variant_keyframes(torch, snapshot, probe)
    dense_ids = sorted(set(frame_to_new_anchor) - {int(row["frame_id"]) for row in snapshot["keyframes"]})
    if snapshot["tracked_poses"]:
        dtype = snapshot["tracked_poses"][0]["relative_pose_data"].dtype
    else:
        dtype = keyframes[0]["T_WC_data"].dtype
    tracked = []
    for row in snapshot["tracked_poses"]:
        frame_id = int(row["frame_id"])
        if frame_id in dense_ids:
            tracked.append((frame_id, frame_to_new_anchor[frame_id], _sim3_identity(torch, dtype)))
        else:
            tracked.append((frame_id, int(row["anchor_idx"]), row["relative_pose_data"].clone()))
    return _timestamps(snapshot), _make_frames(sim3_factory, keyframes), tracked, dense_ids


def write_exports(
    *,
    torch: Any,
    save_full_traj: Any,
    sim3_factory: Any,
    snapshot: dict[str, Any],
    probe: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    baseline_path = output_dir / "baseline_dataset_full.txt"
    variant_path = output_dir / "variant_dataset_full.txt"
    report_path = output_dir / "dense_window_native_trajectory_report.json"
    output_dir.mkdir(parents=True)

    timestamps, frames, tracked = _baseline_inputs(snapshot, sim3_factory)
    save_full_traj(output_dir, baseline_path.name, timestamps, frames, tracked)
    baseline_sha = _file_sha256(baseline_path)
    if baseline_sha != snapshot["original_export_sha256"]:
        raise ValueError("baseline replay does not match snapshot original_export_sha256")

    timestamps, frames, tracked, dense_ids = _variant_inputs(torch, snapshot, probe, sim3_factory)
    save_full_traj(output_dir, variant_path.name, timestamps, frames, tracked)
    variant_sha = _file_sha256(variant_path)

    report = {
        "schema": OUTPUT_SCHEMA,
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "external_tracker_used": False,
        "production_promoted": False,
        "precision_pass": False,
        "snapshot_original_export_path": str(Path(snapshot["original_export_path"]).resolve()),
        "snapshot_original_export_sha256": snapshot["original_export_sha256"],
        "baseline_dataset_full": str(baseline_path.resolve()),
        "baseline_dataset_full_sha256": baseline_sha,
        "variant_dataset_full": str(variant_path.resolve()),
        "variant_dataset_full_sha256": variant_sha,
        "keyframe_count": len(snapshot["keyframes"]),
        "tracked_pose_count": len(snapshot["tracked_poses"]),
        "dense_anchor_frame_ids": dense_ids,
        "dense_anchor_count": len(dense_ids),
        "max_baseline_pose_delta": float(probe.get("_validated_max_baseline_pose_delta", 0.0)),
    }
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
    return report


def run(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    import lietorch
    import mast3r_slam.evaluate as evaluate

    snapshot_path = Path(args.snapshot)
    probe_path = Path(args.probe)
    snapshot = validate_snapshot(torch, _load_torch_snapshot(torch, snapshot_path))
    with probe_path.open("r", encoding="utf-8") as stream:
        probe = validate_probe(torch, json.load(stream), snapshot)

    report = write_exports(
        torch=torch,
        save_full_traj=evaluate.save_full_traj,
        sim3_factory=lambda row: lietorch.Sim3(row.clone()),
        snapshot=snapshot,
        probe=probe,
        output_dir=Path(args.output_dir),
    )
    report.update({
        "snapshot_path": str(snapshot_path.resolve()),
        "snapshot_sha256": _file_sha256(snapshot_path),
        "probe_path": str(probe_path.resolve()),
        "probe_sha256": _file_sha256(probe_path),
    })
    report_path = Path(args.output_dir) / "dense_window_native_trajectory_report.json"
    with report_path.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
    return report


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--output-dir", required=True)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
