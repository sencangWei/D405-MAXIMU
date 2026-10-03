import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import torch


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "export_dense_window_native_trajectory.py"
)
spec = importlib.util.spec_from_file_location("export_dense_window_native_trajectory", MODULE)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class Pose:
    def __init__(self, data):
        self.data = data


def _pose(x=0.0):
    row = torch.zeros(1, 8, dtype=torch.float64)
    row[0, 0] = x
    row[0, 6] = 1.0
    row[0, 7] = 1.0
    return row


def _hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fake_export_text(timestamps, frames, tracked_poses):
    lines = []
    for frame_id, anchor_idx, relative_pose_data in tracked_poses:
        anchor_frame_id = frames[anchor_idx].frame_id
        x = float(frames[anchor_idx].T_WC.data[0, 0] + relative_pose_data[0, 0])
        lines.append(f"{timestamps[frame_id]} {frame_id} {anchor_idx} {anchor_frame_id} {x:.3f}\n")
    return "".join(lines)


def _fake_save_full_traj(logdir, logfile, timestamps, frames, tracked_poses):
    Path(logdir).mkdir(parents=True, exist_ok=True)
    (Path(logdir) / logfile).write_text(
        _fake_export_text(timestamps, frames, tracked_poses), encoding="utf-8"
    )
    return "unchanged"


def _snapshot(source_run):
    keyframes = [
        {"index": 0, "frame_id": 0, "T_WC_data": _pose(0.0)},
        {"index": 1, "frame_id": 2, "T_WC_data": _pose(2.0)},
        {"index": 2, "frame_id": 4, "T_WC_data": _pose(4.0)},
    ]
    identity = _pose(0.0)
    tracked = [
        {"frame_id": i, "anchor_idx": 0 if i < 2 else (1 if i < 4 else 2),
         "anchor_frame_id": 0 if i < 2 else (2 if i < 4 else 4),
         "timestamp_text": f"{float(i):.1f}", "timestamp_float": float(i),
         "relative_pose_data": identity.clone()}
        for i in range(5)
    ]
    baseline_text = _fake_export_text(
        [row["timestamp_text"] for row in tracked],
        [exporter.ExportFrame(row["frame_id"], Pose(row["T_WC_data"])) for row in keyframes],
        [(row["frame_id"], row["anchor_idx"], row["relative_pose_data"]) for row in tracked],
    )
    original_path = Path(source_run) / "logs/native_full.txt"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text(baseline_text, encoding="utf-8")
    return {
        "schema": exporter.SNAPSHOT_SCHEMA,
        "original_export_path": str(original_path),
        "original_export_sha256": _hash_text(baseline_text),
        "keyframes": keyframes,
        "tracked_poses": tracked,
        "keyframe_count": len(keyframes),
        "tracked_pose_count": len(tracked),
    }


def _probe(source_run):
    dense_path = Path(source_run) / "dense_3.pt"
    dense_path.parent.mkdir(parents=True, exist_ok=True)
    dense_path.write_bytes(b"dense")
    return {
        "schema": exporter.PROBE_SCHEMA,
        "status": "DENSE_WINDOW_SOLVED",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "external_tracker_used": False,
        "production_promoted": False,
        "precision_pass": False,
        "original_graph_frame_ids": [0, 2, 4],
        "window_frame_ids": [2, 3, 4],
        "dense_edges_added": True,
        "dense_edge_matches": [
            {"first": 2, "second": 3, "accepted": [True]},
            {"first": 3, "second": 4, "accepted": [True]},
        ],
        "original_edges_unchanged_in_variant": {"4": True, "5": True, "6": True, "7": True, "8": True},
        "inputs": {
            "source_run": str(Path(source_run).resolve()),
            "dense_snapshots": {
                "3": {"path": str(dense_path), "sha256": hashlib.sha256(b"dense").hexdigest()}
            },
        },
        "pose_states": {
            "0": {"baseline_after": _pose(0.0), "variant_after": _pose(10.0)},
            "2": {"baseline_after": _pose(2.0), "variant_after": _pose(12.0)},
            "3": {"baseline_after": _pose(3.0), "variant_after": _pose(13.0)},
            "4": {"baseline_after": _pose(4.0), "variant_after": _pose(14.0)},
        },
    }


def _validate_snapshot(snap):
    return exporter.validate_snapshot(torch, snap, expected_frame_count=5)


def _validate_probe(probe, snap):
    return exporter.validate_probe(torch, probe, snap, expected_window_ids=[2, 3, 4])


def test_validate_snapshot_rejects_malformed_sim3_shape(tmp_path):
    snap = _snapshot(tmp_path)
    snap["keyframes"][0]["T_WC_data"] = torch.zeros(2, 4)
    with pytest.raises(ValueError, match="shape"):
        _validate_snapshot(snap)
    snap = _snapshot(tmp_path)
    snap["tracked_poses"][0]["relative_pose_data"] = _pose().reshape(8)
    with pytest.raises(ValueError, match="shape"):
        _validate_snapshot(snap)


def test_validate_probe_rejects_wrong_source_run(tmp_path):
    snap = _validate_snapshot(_snapshot(tmp_path / "run_a"))
    probe = _probe(tmp_path / "run_b")
    with pytest.raises(ValueError, match="source_run"):
        _validate_probe(probe, snap)


def test_validate_probe_rejects_baseline_mismatch(tmp_path):
    snap = _validate_snapshot(_snapshot(tmp_path))
    probe = _probe(tmp_path)
    probe["pose_states"]["2"]["baseline_after"] = _pose(99.0)
    with pytest.raises(ValueError, match="baseline_after"):
        _validate_probe(probe, snap)


def test_validate_probe_rejects_truthy_edge_prefix_and_bad_window(tmp_path):
    snap = _validate_snapshot(_snapshot(tmp_path))
    probe = _probe(tmp_path)
    probe["original_edges_unchanged_in_variant"]["4"] = 1
    with pytest.raises(ValueError, match="exactly true"):
        _validate_probe(probe, snap)
    probe = _probe(tmp_path)
    probe["window_frame_ids"] = [2, 4]
    with pytest.raises(ValueError, match="expected diagnostic window"):
        _validate_probe(probe, snap)
    probe = _probe(tmp_path)
    probe["window_frame_ids"] = [2, 4, 5]
    with pytest.raises(ValueError, match="expected diagnostic window"):
        _validate_probe(probe, snap)


def test_validate_probe_rejects_missing_dense_binding_or_unaccepted_chain(tmp_path):
    snap = _validate_snapshot(_snapshot(tmp_path))
    probe = _probe(tmp_path)
    probe["inputs"]["dense_snapshots"] = {}
    with pytest.raises(ValueError, match="dense snapshot binding"):
        _validate_probe(probe, snap)
    probe = _probe(tmp_path)
    probe["dense_edge_matches"][0]["accepted"] = [1]
    with pytest.raises(ValueError, match="accepted exactly"):
        _validate_probe(probe, snap)
    probe = _probe(tmp_path)
    probe["dense_edges_added"] = False
    with pytest.raises(ValueError, match="dense_edges_added"):
        _validate_probe(probe, snap)


def test_validate_snapshot_rejects_timestamp_inconsistency(tmp_path):
    snap = _snapshot(tmp_path)
    snap["tracked_poses"][1]["timestamp_text"] = "0.0"
    with pytest.raises(ValueError, match="differ|increasing"):
        _validate_snapshot(snap)


@pytest.mark.parametrize("timestamp_text", ["0.033333335", "39.933334"])
def test_native_float32_timestamp_text_roundtrip_is_preserved(tmp_path, timestamp_text):
    snap = _snapshot(tmp_path)
    actual_float = float(torch.tensor(float(timestamp_text), dtype=torch.float32))
    snap["tracked_poses"][1]["timestamp_text"] = timestamp_text
    snap["tracked_poses"][1]["timestamp_float"] = actual_float
    for index in range(2, 5):
        timestamp = actual_float + index
        snap["tracked_poses"][index]["timestamp_text"] = str(timestamp)
        snap["tracked_poses"][index]["timestamp_float"] = timestamp
    validated = _validate_snapshot(snap)
    assert exporter._timestamps(validated)[1] == f"{actual_float}"
    snap["tracked_poses"][1]["timestamp_float"] += 0.001
    with pytest.raises(ValueError, match="differ"):
        _validate_snapshot(snap)


def test_cli_defaults_reject_truncated_snapshot_and_reduced_window(tmp_path):
    with pytest.raises(ValueError, match="1199"):
        exporter.validate_snapshot(torch, _snapshot(tmp_path))
    snap = _validate_snapshot(_snapshot(tmp_path))
    with pytest.raises(ValueError, match="expected diagnostic window"):
        exporter.validate_probe(torch, _probe(tmp_path), snap)


def test_write_exports_replays_baseline_and_reanchors_dense_frame(tmp_path):
    snap = _validate_snapshot(_snapshot(tmp_path))
    probe = _validate_probe(_probe(tmp_path), snap)
    out = tmp_path / "out"
    report = exporter.write_exports(
        torch=torch,
        save_full_traj=_fake_save_full_traj,
        sim3_factory=Pose,
        snapshot=snap,
        probe=probe,
        output_dir=out,
    )
    assert report["dense_anchor_frame_ids"] == [3]
    assert report["tracked_pose_count"] == 5
    assert report["max_baseline_pose_delta"] == 0.0
    baseline = (out / "baseline_dataset_full.txt").read_text(encoding="utf-8")
    variant = (out / "variant_dataset_full.txt").read_text(encoding="utf-8")
    assert baseline.splitlines()[1] == "1.0 1 0 0 0.000"  # retry child keeps original anchor
    assert variant.splitlines()[1] == "1.0 1 0 0 10.000"  # same relative pose under changed anchor
    assert variant.splitlines()[3] == "3.0 3 3 3 13.000"  # dense frame becomes identity extra anchor
    assert report["dense_anchor_count"] == 1
    assert report["variant_dataset_full_sha256"]
    saved_report = json.loads((out / "dense_window_native_trajectory_report.json").read_text())
    assert saved_report["production_promoted"] is False
    assert saved_report["precision_pass"] is False
    assert saved_report["dense_anchor_frame_ids"] == [3]


def test_variant_rows_preserve_snapshot_dtype(tmp_path):
    snap = _validate_snapshot(_snapshot(tmp_path))
    probe = _validate_probe(_probe(tmp_path), snap)
    _, frames, tracked, dense_ids = exporter._variant_inputs(torch, snap, probe, Pose)
    assert dense_ids == [3]
    assert all(frame.T_WC.data.dtype == torch.float64 for frame in frames)
    assert tracked[3][2].dtype == snap["tracked_poses"][0]["relative_pose_data"].dtype


def test_write_exports_refuses_existing_output_before_writer(tmp_path):
    snap = _validate_snapshot(_snapshot(tmp_path))
    probe = _validate_probe(_probe(tmp_path), snap)
    out = tmp_path / "out"
    out.mkdir()
    called = False

    def writer(*args, **kwargs):
        nonlocal called
        called = True

    with pytest.raises(FileExistsError):
        exporter.write_exports(
            torch=torch,
            save_full_traj=writer,
            sim3_factory=Pose,
            snapshot=snap,
            probe=probe,
            output_dir=out,
        )
    assert called is False


def test_write_exports_rejects_baseline_hash_mismatch(tmp_path):
    snap = _validate_snapshot(_snapshot(tmp_path))
    probe = _validate_probe(_probe(tmp_path), snap)
    snap["original_export_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="baseline replay"):
        exporter.write_exports(
            torch=torch,
            save_full_traj=_fake_save_full_traj,
            sim3_factory=Pose,
            snapshot=snap,
            probe=probe,
            output_dir=tmp_path / "out",
        )
