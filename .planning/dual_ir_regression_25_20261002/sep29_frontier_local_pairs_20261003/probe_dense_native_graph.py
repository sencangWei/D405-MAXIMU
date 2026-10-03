"""Diagnostic-only GN replay with one explicit dense frame-808 state.

This consumes captured MASt3R native tensors only; it does not score, tune, or
promote a production trajectory.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path


FRAME_ID = 808
PARENT_ID = 791
NEXT_ID = 809
SOLVE_ID = 877


def _tensor_sha256(torch, tensor) -> str:
    stream = io.BytesIO()
    torch.save(tensor.detach().cpu(), stream)
    return hashlib.sha256(stream.getvalue()).hexdigest()


def _file_sha256(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_tensor(torch, name, value):
    if value is None or not torch.is_tensor(value):
        raise ValueError(f"{name} must be a tensor")
    if not bool(torch.isfinite(value).all().item()):
        raise ValueError(f"{name} contains non-finite values")
    return value


def _pose_row(torch, name, value):
    value = _finite_tensor(torch, name, value).reshape(-1, 8)
    if value.shape[0] != 1:
        raise ValueError(f"{name} must contain exactly one Sim3 row")
    quat_norm = torch.linalg.vector_norm(value[0, 3:7])
    if not bool(torch.isfinite(quat_norm).item()) or abs(float(quat_norm) - 1.0) > 1e-3:
        raise ValueError(f"{name} quaternion is not unit length")
    return value


def _validate_graph_args(torch, args):
    for index in range(9):
        _finite_tensor(torch, f"graph.args[{index}]", args[index])
    poses, points = args[0], args[1]
    if not bool((poses[:, 7] > 0).all().item()):
        raise ValueError("graph Sim3 scales must be positive")
    quat_error = torch.abs(torch.linalg.vector_norm(poses[:, 3:7], dim=1) - 1.0)
    if float(quat_error.max()) > 1e-3:
        raise ValueError("graph pose quaternion is not unit length")
    edge_count = int(args[4].numel())
    for index in (5, 6, 7, 8):
        if int(args[index].shape[0]) != edge_count:
            raise ValueError("graph edge tensor leading dimensions disagree")
    if not bool(((args[4] >= 0) & (args[4] < poses.shape[0])).all().item()):
        raise ValueError("graph edge source index out of range")
    if not bool(((args[5] >= 0) & (args[5] < poses.shape[0])).all().item()):
        raise ValueError("graph edge target index out of range")
    if not bool(((args[6] >= 0) & (args[6] < points.shape[1])).all().item()):
        raise ValueError("graph match index out of range")


def load_snapshots(torch, dense_path, graph_path):
    dense = torch.load(dense_path, map_location="cpu", weights_only=True)
    graph = torch.load(graph_path, map_location="cpu", weights_only=True)
    if dense.get("schema") != "mast3r_dense_frame_snapshot_v1":
        raise ValueError("unexpected dense snapshot schema")
    if int(dense["frame"]["frame_id"]) != FRAME_ID:
        raise ValueError("dense snapshot is not frame 808")
    if int(dense["reference"]["frame_id"]) != PARENT_ID:
        raise ValueError("dense snapshot parent is not frame 791")
    if graph.get("frame_ids", [])[-1:] != [SOLVE_ID] or len(graph.get("args", ())) != 19:
        raise ValueError("expected graph877 calibrated-GN snapshot with 19 args")
    frame_ids = [int(v) for v in graph["frame_ids"]]
    if len(frame_ids) != len(set(frame_ids)) or FRAME_ID in frame_ids:
        raise ValueError("graph frame IDs must be unique and exclude dense 808")
    for frame_id in (PARENT_ID, NEXT_ID):
        if frame_id not in frame_ids:
            raise ValueError(f"graph snapshot missing frame {frame_id}")
    _validate_graph_args(torch, graph["args"])
    for name in ("X_canon", "C", "feat", "pos", "K", "T_WC_data"):
        _finite_tensor(torch, f"dense.frame.{name}", dense["frame"][name])
    if int(dense["frame"]["N"]) <= 0:
        raise ValueError("dense frame N must be positive")
    if dense.get("track_return", {}).get("add_new_kf") is not False:
        raise ValueError("dense snapshot must come from an ordinary non-keyframe track")
    if dense.get("track_return", {}).get("try_reloc") is not False:
        raise ValueError("dense snapshot must come from successful non-reloc tracking")
    _pose_row(torch, "dense.reference.T_WC_data", dense["reference"]["T_WC_data"])
    _pose_row(torch, "dense.frame.T_WC_data", dense["frame"]["T_WC_data"])
    if not torch.equal(dense["frame"]["K"], graph["args"][3]):
        raise ValueError("dense reference K differs from graph intrinsics")
    return dense, graph


def load_source_run(source_run, dataset, config_path, checkpoint_path):
    run_dir = Path(source_run).resolve()
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "umi_mast3r_run_v1":
        raise ValueError("unexpected source-run manifest schema")
    run_dataset = (run_dir / "dataset").resolve()
    if Path(dataset).resolve() != run_dataset:
        raise ValueError("dataset must resolve to source-run dataset")
    if _file_sha256(config_path) != manifest.get("config_sha256"):
        raise ValueError("config SHA does not match source-run manifest")
    if _file_sha256(checkpoint_path) != manifest.get("checkpoint_sha256"):
        raise ValueError("checkpoint SHA does not match source-run manifest")
    return manifest_path, manifest


def _clone_args(torch, args, device):
    return [
        value.detach().clone().to(device) if torch.is_tensor(value) else value
        for value in args
    ]


def _match_dense_edges(torch, mast3r_utils, config, model, frames_by_id):
    left = [frames_by_id[PARENT_ID], frames_by_id[FRAME_ID]]
    right = [frames_by_id[FRAME_ID], frames_by_id[NEXT_ID]]
    out = mast3r_utils.mast3r_match_symmetric(
        model,
        torch.cat([frame.feat for frame in left]),
        torch.cat([frame.pos for frame in left]),
        torch.cat([frame.feat for frame in right]),
        torch.cat([frame.pos for frame in right]),
        [frame.img_true_shape for frame in left],
        [frame.img_true_shape for frame in right],
    )
    idx_i2j, idx_j2i, valid_j_raw, valid_i_raw, Qii, Qjj, Qji, Qij = out
    batch = torch.arange(idx_i2j.shape[0], device=idx_i2j.device)[:, None].repeat(
        1, idx_i2j.shape[1]
    )
    Qj = torch.sqrt(Qii[batch, idx_i2j] * Qji)
    Qi = torch.sqrt(Qjj[batch, idx_j2i] * Qij)
    cfg = config["local_opt"]
    valid_j = valid_j_raw & (Qj > cfg["Q_conf"])
    valid_i = valid_i_raw & (Qi > cfg["Q_conf"])
    denom_j = valid_j.shape[1] * valid_j.shape[2]
    denom_i = valid_i.shape[1] * valid_i.shape[2]
    frac_j = valid_j.sum(dim=(1, 2)) / denom_j
    frac_i = valid_i.sum(dim=(1, 2)) / denom_i
    accepted = torch.minimum(frac_j, frac_i) >= cfg["min_match_frac"]
    return {
        "idx_i2j": idx_i2j,
        "idx_j2i": idx_j2i,
        "valid_j": valid_j_raw,
        "valid_i": valid_i_raw,
        "Qj": Qj,
        "Qi": Qi,
        "match_fraction_i_to_j": [float(v) for v in frac_j.detach().cpu()],
        "match_fraction_j_to_i": [float(v) for v in frac_i.detach().cpu()],
        "accepted": [bool(v) for v in accepted.detach().cpu()],
        "threshold": float(cfg["min_match_frac"]),
    }


def _public_match_report(match):
    return {
        "match_fraction_i_to_j": match["match_fraction_i_to_j"],
        "match_fraction_j_to_i": match["match_fraction_j_to_i"],
        "accepted": match["accepted"],
        "threshold": match["threshold"],
    }


def _append_dense_edges(torch, args, frame_ids, match):
    if not all(match["accepted"]):
        return list(args), False
    parent_index = frame_ids.index(PARENT_ID)
    next_index = frame_ids.index(NEXT_ID)
    dense_index = len(frame_ids) - 1
    forward_ii = torch.as_tensor([parent_index, dense_index], device=args[4].device)
    forward_jj = torch.as_tensor([dense_index, next_index], device=args[5].device)
    args = list(args)
    args[4] = torch.cat([args[4], forward_ii, forward_jj])
    args[5] = torch.cat([args[5], forward_jj, forward_ii])
    args[6] = torch.cat([
        args[6],
        match["idx_i2j"].detach().cpu().clone().to(args[6].device),
        match["idx_j2i"].detach().cpu().clone().to(args[6].device),
    ])
    args[7] = torch.cat([
        args[7],
        match["valid_j"].detach().cpu().clone().to(args[7].device),
        match["valid_i"].detach().cpu().clone().to(args[7].device),
    ])
    args[8] = torch.cat([
        args[8],
        match["Qj"].detach().cpu().clone().to(args[8].device),
        match["Qi"].detach().cpu().clone().to(args[8].device),
    ])
    return args, True


def _distance(torch, a, b):
    return float(torch.linalg.vector_norm(b[:3] - a[:3]).item())


def _transport_dense_pose(torch, lietorch, parent_pose, dense_reference, dense_pose):
    parent = lietorch.Sim3(parent_pose.reshape(1, 8))
    reference = lietorch.Sim3(dense_reference.reshape(1, 8))
    dense = lietorch.Sim3(dense_pose.reshape(1, 8))
    return (parent * (reference.inv() * dense)).data.reshape(1, 8)


def run_probe(paths):
    import torch
    import lietorch
    import mast3r_slam_backends
    from mast3r_slam.config import config, load_config
    from mast3r_slam.dataloader import Intrinsics, load_dataset
    from mast3r_slam.frame import create_frame
    from mast3r_slam.geometry import constrain_points_to_ray
    from mast3r_slam.mast3r_utils import load_mast3r
    import mast3r_slam.mast3r_utils as mast3r_utils
    import yaml

    output_path = Path(paths.output)
    if output_path.exists():
        raise FileExistsError(output_path)
    source_manifest_path, source_manifest = load_source_run(
        paths.source_run, paths.dataset, paths.config, paths.checkpoint
    )
    dense, graph = load_snapshots(torch, paths.dense, paths.graph)
    load_config(paths.config)
    dataset = load_dataset(paths.dataset)
    dataset.subsample(config["dataset"]["subsample"])
    calib_path = Path(paths.calib) if paths.calib else Path(paths.source_run) / "dataset" / "calibration.yaml"
    with calib_path.open("r", encoding="utf-8") as stream:
        intrinsics = yaml.load(stream, Loader=yaml.SafeLoader)
    config["use_calib"] = True
    dataset.use_calibration = True
    dataset.camera_intrinsics = Intrinsics.from_calib(
        dataset.img_size,
        intrinsics["width"],
        intrinsics["height"],
        intrinsics["calibration"],
    )
    _, dense_image = dataset[FRAME_ID]
    dense_check = create_frame(
        FRAME_ID,
        dense_image,
        lietorch.Sim3.Identity(1, device=paths.device),
        img_size=dataset.img_size,
        device=paths.device,
    )
    if not torch.equal(dense_check.img.detach().cpu(), dense["frame"]["img"]):
        raise ValueError("dataset frame808 image does not match captured dense frame")
    model = load_mast3r(path=paths.checkpoint, device=paths.device)
    if hasattr(model, "eval"):
        model.eval()
    try:
        args_cpu = tuple(graph["args"])
        frame_ids = [int(v) for v in graph["frame_ids"]]
        parent_idx = frame_ids.index(PARENT_ID)
        next_idx = frame_ids.index(NEXT_ID)
        edge_sha = {str(i): _tensor_sha256(torch, args_cpu[i]) for i in range(4, 9)}

        dense_pose = _transport_dense_pose(
            torch,
            lietorch,
            args_cpu[0][parent_idx],
            _pose_row(torch, "dense.reference.T_WC_data", dense["reference"]["T_WC_data"])[0],
            _pose_row(torch, "dense.frame.T_WC_data", dense["frame"]["T_WC_data"])[0],
        )
        dense_X = constrain_points_to_ray(
            (int(args_cpu[9]), int(args_cpu[10])),
            dense["frame"]["X_canon"][None],
            args_cpu[3],
        ).squeeze(0)
        dense_C = dense["frame"]["C"] / int(dense["frame"]["N"])

        variant_cpu = list(args_cpu)
        variant_cpu[0] = torch.cat([args_cpu[0], dense_pose])
        variant_cpu[1] = torch.cat([args_cpu[1], dense_X[None]])
        variant_cpu[2] = torch.cat([args_cpu[2], dense_C[None]])
        variant_frame_ids = frame_ids + [FRAME_ID]

        class DenseFrame:
            pass

        frames = {}
        with torch.inference_mode():
            for frame_id in (PARENT_ID, NEXT_ID):
                _, image = dataset[frame_id]
                frame = create_frame(
                    frame_id,
                    image,
                    lietorch.Sim3(
                        args_cpu[0][frame_ids.index(frame_id)]
                        .reshape(1, 8)
                        .to(paths.device)
                    ),
                    img_size=dataset.img_size,
                    device=paths.device,
                )
                frame.feat, frame.pos, _ = model._encode_image(frame.img, frame.img_true_shape)
                frames[frame_id] = frame
            dense_frame = DenseFrame()
            dense_frame.feat = dense["frame"]["feat"].to(paths.device)
            dense_frame.pos = dense["frame"]["pos"].to(paths.device)
            dense_frame.img_true_shape = dense["frame"]["img_true_shape"].to(paths.device)
            frames[FRAME_ID] = dense_frame

            match = _match_dense_edges(torch, mast3r_utils, config, model, frames)
        variant_args, added = _append_dense_edges(
            torch, variant_cpu, variant_frame_ids, match
        )
        del frames, dense_frame, model
        if paths.device.startswith("cuda") and torch.cuda.is_available():
            torch.cuda.empty_cache()

        def solve(args):
            args = _clone_args(torch, args, paths.device)
            before = args[0].detach().cpu().clone()
            with torch.no_grad():
                mast3r_slam_backends.gauss_newton_calib(*args)
            if paths.device.startswith("cuda") and torch.cuda.is_available():
                torch.cuda.synchronize()
            return before, args[0].detach().cpu().clone()

        base_before, base_after = solve(args_cpu)
        if added:
            var_before, var_after = solve(variant_args)
            status = "DENSE_EDGES_SOLVED"
        else:
            var_before, var_after = None, None
            status = "REJECTED_DENSE_EDGES"
        solve_idx = frame_ids.index(SOLVE_ID)
        baseline_transport = _transport_dense_pose(
            torch,
            lietorch,
            base_after[parent_idx],
            _pose_row(torch, "dense.reference.T_WC_data", dense["reference"]["T_WC_data"])[0],
            _pose_row(torch, "dense.frame.T_WC_data", dense["frame"]["T_WC_data"])[0],
        )[0]

        report = {
            "schema": "dense808_native_graph_probe_v1",
            "status": status,
            "diagnostic_only": True,
            "external_ground_truth_used": False,
            "external_tracker_used": False,
            "mast3r_tracking_reference_pose_used_for_dense_transport": True,
            "production_promoted": False,
            "precision_pass": False,
            "inputs": {
                "source_run": str(Path(paths.source_run).resolve()),
                "source_manifest": str(source_manifest_path.resolve()),
                "source_manifest_sha256": _file_sha256(source_manifest_path),
                "dataset": str(Path(paths.dataset).resolve()),
                "dataset_manifest": str((Path(paths.dataset) / "dataset_manifest.json").resolve()),
                "dataset_manifest_sha256": _file_sha256(Path(paths.dataset) / "dataset_manifest.json"),
                "config": str(Path(paths.config).resolve()),
                "config_sha256": _file_sha256(paths.config),
                "checkpoint": str(Path(paths.checkpoint).resolve()),
                "checkpoint_sha256": _file_sha256(paths.checkpoint),
                "calib": str(calib_path.resolve()),
                "calib_sha256": _file_sha256(calib_path),
                "dense": str(Path(paths.dense).resolve()),
                "dense_sha256": _file_sha256(paths.dense),
                "graph": str(Path(paths.graph).resolve()),
                "graph_sha256": _file_sha256(paths.graph),
                "source_run_toolchain_commit": source_manifest.get("toolchain_commit"),
            },
            "frame_ids": {"parent": PARENT_ID, "dense": FRAME_ID, "next": NEXT_ID, "solve": SOLVE_ID},
            "dense_edges_added": bool(added),
            "dense_edge_match": _public_match_report(match),
            "native_unit_distance_808_to_809": {
                "before": _distance(torch, variant_cpu[0][-1], variant_cpu[0][next_idx]),
                "baseline_transport_after": _distance(torch, baseline_transport, base_after[next_idx]),
                "explicit_after": (
                    _distance(torch, var_after[-1], var_after[next_idx]) if added else None
                ),
            },
            "pose_states": {
                "parent791": {
                    "baseline_after": base_after[parent_idx].tolist(),
                    "variant_after": var_after[parent_idx].tolist() if added else None,
                },
                "dense808": {
                    "initial": variant_cpu[0][-1].tolist(),
                    "variant_after": var_after[-1].tolist() if added else None,
                },
                "next809": {
                    "baseline_after": base_after[next_idx].tolist(),
                    "variant_after": var_after[next_idx].tolist() if added else None,
                },
                "solve877": {
                    "baseline_after": base_after[solve_idx].tolist(),
                    "variant_after": var_after[solve_idx].tolist() if added else None,
                },
            },
            "original_edge_tensor_sha256": edge_sha,
            "original_edges_unchanged_in_variant": {
                str(i): bool(torch.equal(args_cpu[i], variant_args[i][: args_cpu[i].shape[0]]))
                for i in range(4, 9)
            },
            "unit_quaternion_max_abs_error": {
                "baseline": float(torch.abs(torch.linalg.vector_norm(base_after[:, 3:7], dim=1) - 1.0).max()),
                "variant": (
                    float(torch.abs(torch.linalg.vector_norm(var_after[:, 3:7], dim=1) - 1.0).max())
                    if added else None
                ),
            },
        }
        with open(output_path, "x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        return report
    finally:
        if "model" in locals():
            del model


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense", required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--calib", default=None)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--output", required=True)
    return run_probe(parser.parse_args(argv))


if __name__ == "__main__":
    main()
