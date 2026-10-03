"""Diagnostic-only GN replay with a copied native dense-frame interval."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_dense_native_graph as base  # noqa: E402


def interval_frame_ids(first, last, parent, next_frame):
    if first > last:
        raise ValueError("first must be <= last")
    if first != parent + 1 or next_frame != last + 1:
        raise ValueError("dense interval must be exactly parent+1..next-1")
    return [parent, *range(first, last + 1), next_frame]


def _validate_sim3_array(torch, name, value, expected_rows):
    value = base._finite_tensor(torch, name, value)
    if tuple(value.shape) != (expected_rows, 8):
        raise ValueError(f"{name} must have shape ({expected_rows}, 8)")
    if not bool((value[:, 7] > 0).all().item()):
        raise ValueError(f"{name} Sim3 scales must be positive")
    quat_error = torch.abs(torch.linalg.vector_norm(value[:, 3:7], dim=1) - 1.0)
    if float(quat_error.max()) > 1e-3:
        raise ValueError(f"{name} quaternion is not unit length")
    return value


def _load_graph(torch, graph_path, solve, parent, next_frame, dense_ids):
    graph = torch.load(graph_path, map_location="cpu", weights_only=True)
    if graph.get("frame_ids", [])[-1:] != [solve] or len(graph.get("args", ())) != 19:
        raise ValueError("expected calibrated-GN graph snapshot for solve frame")
    frame_ids = [int(value) for value in graph["frame_ids"]]
    if len(frame_ids) != len(set(frame_ids)):
        raise ValueError("graph frame IDs must be unique")
    for frame_id in (parent, next_frame, solve):
        if frame_id not in frame_ids:
            raise ValueError(f"graph snapshot missing frame {frame_id}")
    overlap = sorted(set(frame_ids).intersection(dense_ids))
    if overlap:
        raise ValueError(f"graph already contains dense frames {overlap}")
    base._validate_graph_args(torch, graph["args"])
    _validate_sim3_array(torch, "graph.args[0]", graph["args"][0], len(frame_ids))
    return graph


def _load_dense(torch, path, frame_id, parent, K):
    dense = torch.load(path, map_location="cpu", weights_only=True)
    if dense.get("schema") != "mast3r_dense_frame_snapshot_v1":
        raise ValueError("unexpected dense snapshot schema")
    if int(dense["frame"]["frame_id"]) != frame_id or int(dense["requested_frame_id"]) != frame_id:
        raise ValueError("dense snapshot frame ID mismatch")
    if int(dense["reference"]["frame_id"]) != parent:
        raise ValueError("dense snapshot reference parent mismatch")
    if dense.get("track_return", {}).get("add_new_kf") is not False:
        raise ValueError("dense snapshot must be ordinary non-keyframe")
    if dense.get("track_return", {}).get("try_reloc") is not False:
        raise ValueError("dense snapshot must be non-reloc success")
    for name in ("img", "X_canon", "C", "feat", "pos", "K", "T_WC_data"):
        base._finite_tensor(torch, f"dense{frame_id}.{name}", dense["frame"][name])
    if int(dense["frame"]["N"]) <= 0:
        raise ValueError("dense frame N must be positive")
    _validate_sim3_array(torch, "dense.reference.T_WC_data", dense["reference"]["T_WC_data"], 1)
    _validate_sim3_array(torch, "dense.frame.T_WC_data", dense["frame"]["T_WC_data"], 1)
    if not torch.equal(dense["frame"]["K"], K):
        raise ValueError("dense K differs from graph intrinsics")
    return dense


def load_dense_window(torch, template, dense_ids, parent, K):
    if "{frame_id}" not in template and len(dense_ids) > 1:
        raise ValueError("dense template must contain {frame_id} for an interval")
    return {
        frame_id: _load_dense(torch, template.format(frame_id=frame_id), frame_id, parent, K)
        for frame_id in dense_ids
    }


def _append_chain_edges(torch, args, frame_ids, edge_matches):
    if not edge_matches or any(not match["accepted"][0] for _, _, match in edge_matches):
        return list(args), False
    args = list(args)
    ii = torch.as_tensor([frame_ids.index(i) for i, _, _ in edge_matches], device=args[4].device)
    jj = torch.as_tensor([frame_ids.index(j) for _, j, _ in edge_matches], device=args[5].device)
    args[4] = torch.cat([args[4], ii, jj])
    args[5] = torch.cat([args[5], jj, ii])
    args[6] = torch.cat([
        args[6],
        *[m["idx_i2j"].detach().cpu().clone().to(args[6].device) for _, _, m in edge_matches],
        *[m["idx_j2i"].detach().cpu().clone().to(args[6].device) for _, _, m in edge_matches],
    ])
    args[7] = torch.cat([
        args[7],
        *[m["valid_j"].detach().cpu().clone().to(args[7].device) for _, _, m in edge_matches],
        *[m["valid_i"].detach().cpu().clone().to(args[7].device) for _, _, m in edge_matches],
    ])
    args[8] = torch.cat([
        args[8],
        *[m["Qj"].detach().cpu().clone().to(args[8].device) for _, _, m in edge_matches],
        *[m["Qi"].detach().cpu().clone().to(args[8].device) for _, _, m in edge_matches],
    ])
    return args, True


def _match_one_edge(torch, mast3r_utils, config, model, left, right):
    out = mast3r_utils.mast3r_match_symmetric(
        model, left.feat, left.pos, right.feat, right.pos,
        [left.img_true_shape], [right.img_true_shape],
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
    frac_j = valid_j.sum(dim=(1, 2)) / (valid_j.shape[1] * valid_j.shape[2])
    frac_i = valid_i.sum(dim=(1, 2)) / (valid_i.shape[1] * valid_i.shape[2])
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


def _public_edges(edge_matches):
    return [
        {"first": i, "second": j, **base._public_match_report(match)}
        for i, j, match in edge_matches
    ]


def _pose_states(torch, lietorch, parent, frame_ids, dense_ids, variant_ids, args_cpu, dense_by_id,
                 base_after, var_after):
    parent_idx = frame_ids.index(parent)
    states = {}
    for frame_id in frame_ids:
        idx = frame_ids.index(frame_id)
        states[str(frame_id)] = {
            "initial": args_cpu[0][idx].tolist(),
            "baseline_after": base_after[idx].tolist(),
            "variant_after": var_after[variant_ids.index(frame_id)].tolist() if var_after is not None else None,
        }
    for frame_id in dense_ids:
        dense = dense_by_id[frame_id]
        initial = base._transport_dense_pose(
            torch, lietorch, args_cpu[0][parent_idx],
            base._pose_row(torch, "dense.reference.T_WC_data", dense["reference"]["T_WC_data"])[0],
            base._pose_row(torch, "dense.frame.T_WC_data", dense["frame"]["T_WC_data"])[0],
        )[0]
        baseline = base._transport_dense_pose(
            torch, lietorch, base_after[parent_idx],
            base._pose_row(torch, "dense.reference.T_WC_data", dense["reference"]["T_WC_data"])[0],
            base._pose_row(torch, "dense.frame.T_WC_data", dense["frame"]["T_WC_data"])[0],
        )[0]
        states[str(frame_id)] = {
            "initial": initial.tolist(),
            "baseline_after": baseline.tolist(),
            "variant_after": var_after[variant_ids.index(frame_id)].tolist() if var_after is not None else None,
        }
    return states


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
    window_ids = interval_frame_ids(paths.first, paths.last, paths.parent, paths.next)
    dense_ids = window_ids[1:-1]
    source_manifest_path, source_manifest = base.load_source_run(
        paths.source_run, paths.dataset, paths.config, paths.checkpoint
    )
    graph = _load_graph(torch, paths.graph, paths.solve, paths.parent, paths.next, dense_ids)
    args_cpu = tuple(graph["args"])
    graph_ids = [int(v) for v in graph["frame_ids"]]
    dense_by_id = load_dense_window(torch, paths.dense_template, dense_ids, paths.parent, args_cpu[3])

    load_config(paths.config)
    dataset = load_dataset(paths.dataset)
    dataset.subsample(config["dataset"]["subsample"])
    calib_path = Path(paths.source_run) / "dataset" / "calibration.yaml"
    with calib_path.open("r", encoding="utf-8") as stream:
        intrinsics = yaml.load(stream, Loader=yaml.SafeLoader)
    config["use_calib"] = True
    dataset.use_calibration = True
    dataset.camera_intrinsics = Intrinsics.from_calib(
        dataset.img_size, intrinsics["width"], intrinsics["height"], intrinsics["calibration"]
    )
    model = load_mast3r(path=paths.checkpoint, device=paths.device)
    if hasattr(model, "eval"):
        model.eval()
    try:
        class DenseFrame:
            pass

        frames = {}
        with torch.inference_mode():
            for frame_id in (paths.parent, paths.next):
                _, image = dataset[frame_id]
                frame = create_frame(
                    frame_id, image,
                    lietorch.Sim3(args_cpu[0][graph_ids.index(frame_id)].reshape(1, 8).to(paths.device)),
                    img_size=dataset.img_size, device=paths.device,
                )
                frame.feat, frame.pos, _ = model._encode_image(frame.img, frame.img_true_shape)
                frames[frame_id] = frame
            for frame_id, dense in dense_by_id.items():
                _, image = dataset[frame_id]
                check = create_frame(
                    frame_id, image, lietorch.Sim3.Identity(1, device=paths.device),
                    img_size=dataset.img_size, device=paths.device,
                )
                if not torch.equal(check.img.detach().cpu(), dense["frame"]["img"]):
                    raise ValueError(f"dataset frame{frame_id} image does not match capture")
                frame = DenseFrame()
                frame.feat = dense["frame"]["feat"].to(paths.device)
                frame.pos = dense["frame"]["pos"].to(paths.device)
                frame.img_true_shape = dense["frame"]["img_true_shape"].to(paths.device)
                frames[frame_id] = frame

            edge_matches = [
                (i, j, _match_one_edge(torch, mast3r_utils, config, model, frames[i], frames[j]))
                for i, j in zip(window_ids[:-1], window_ids[1:])
            ]

        variant_ids = graph_ids + dense_ids
        variant_cpu = list(args_cpu)
        parent_idx = graph_ids.index(paths.parent)
        dense_poses, dense_Xs, dense_Cs = [], [], []
        for frame_id in dense_ids:
            dense = dense_by_id[frame_id]
            dense_poses.append(base._transport_dense_pose(
                torch, lietorch, args_cpu[0][parent_idx],
                base._pose_row(torch, "dense.reference.T_WC_data", dense["reference"]["T_WC_data"])[0],
                base._pose_row(torch, "dense.frame.T_WC_data", dense["frame"]["T_WC_data"])[0],
            ))
            dense_Xs.append(constrain_points_to_ray(
                (int(args_cpu[9]), int(args_cpu[10])), dense["frame"]["X_canon"][None], args_cpu[3]
            ).squeeze(0))
            dense_Cs.append(dense["frame"]["C"] / int(dense["frame"]["N"]))
        variant_cpu[0] = torch.cat([args_cpu[0], *dense_poses])
        variant_cpu[1] = torch.cat([args_cpu[1], torch.stack(dense_Xs)])
        variant_cpu[2] = torch.cat([args_cpu[2], torch.stack(dense_Cs)])
        variant_args, added = _append_chain_edges(torch, variant_cpu, variant_ids, edge_matches)
        del frames, model
        if paths.device.startswith("cuda") and torch.cuda.is_available():
            torch.cuda.empty_cache()

        def solve(args):
            args = base._clone_args(torch, args, paths.device)
            before = args[0].detach().cpu().clone()
            with torch.no_grad():
                mast3r_slam_backends.gauss_newton_calib(*args)
            if paths.device.startswith("cuda") and torch.cuda.is_available():
                torch.cuda.synchronize()
            after = args[0].detach().cpu().clone()
            return before, after

        _, base_after = solve(args_cpu)
        _validate_sim3_array(torch, "baseline GN poses", base_after, len(graph_ids))
        if added:
            var_after = solve(variant_args)[1]
            _validate_sim3_array(torch, "variant GN poses", var_after, len(variant_ids))
            status = "DENSE_WINDOW_SOLVED"
        else:
            var_after = None
            status = "REJECTED_DENSE_EDGES"

        edge_sha = {str(i): base._tensor_sha256(torch, args_cpu[i]) for i in range(4, 9)}
        states = _pose_states(
            torch, lietorch, paths.parent, graph_ids, dense_ids, variant_ids,
            args_cpu, dense_by_id, base_after, var_after,
        )
        report = {
            "schema": "dense_window_native_graph_probe_v1",
            "status": status,
            "diagnostic_only": True,
            "external_ground_truth_used": False,
            "external_tracker_used": False,
            "mast3r_tracking_reference_pose_used_for_dense_transport": True,
            "production_promoted": False,
            "precision_pass": False,
            "original_graph_frame_ids": graph_ids,
            "window_frame_ids": window_ids,
            "dense_edge_matches": _public_edges(edge_matches),
            "dense_edges_added": bool(added),
            "pose_states": states,
            "adjacent_native_unit_steps": [
                {
                    "first": i,
                    "second": j,
                    "initial": base._distance(
                        torch, variant_cpu[0][variant_ids.index(i)], variant_cpu[0][variant_ids.index(j)]
                    ),
                    "baseline_after": base._distance(
                        torch,
                        torch.tensor(states[str(i)]["baseline_after"]),
                        torch.tensor(states[str(j)]["baseline_after"]),
                    ),
                    "variant_after": (
                        base._distance(torch, var_after[variant_ids.index(i)], var_after[variant_ids.index(j)])
                        if var_after is not None else None
                    ),
                }
                for i, j in zip(window_ids[:-1], window_ids[1:])
            ],
            "original_edge_tensor_sha256": edge_sha,
            "original_edges_unchanged_in_variant": {
                str(i): bool(torch.equal(args_cpu[i], variant_args[i][: args_cpu[i].shape[0]]))
                for i in range(4, 9)
            },
            "inputs": {
                "source_run": str(Path(paths.source_run).resolve()),
                "source_manifest": str(source_manifest_path.resolve()),
                "source_manifest_sha256": base._file_sha256(source_manifest_path),
                "dataset": str(Path(paths.dataset).resolve()),
                "dataset_manifest": str((Path(paths.dataset) / "dataset_manifest.json").resolve()),
                "dataset_manifest_sha256": base._file_sha256(Path(paths.dataset) / "dataset_manifest.json"),
                "config": str(Path(paths.config).resolve()),
                "config_sha256": base._file_sha256(paths.config),
                "checkpoint": str(Path(paths.checkpoint).resolve()),
                "checkpoint_sha256": base._file_sha256(paths.checkpoint),
                "calib": str(calib_path.resolve()),
                "calib_sha256": base._file_sha256(calib_path),
                "graph": str(Path(paths.graph).resolve()),
                "graph_sha256": base._file_sha256(paths.graph),
                "source_run_toolchain_commit": source_manifest.get("toolchain_commit"),
                "dense_snapshots": {
                    str(frame_id): {
                        "path": str(Path(paths.dense_template.format(frame_id=frame_id)).resolve()),
                        "sha256": base._file_sha256(paths.dense_template.format(frame_id=frame_id)),
                    }
                    for frame_id in dense_ids
                },
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
    parser.add_argument("--dense-template", required=True)
    parser.add_argument("--first", type=int, required=True)
    parser.add_argument("--last", type=int, required=True)
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--next", type=int, required=True)
    parser.add_argument("--solve", type=int, required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--output", required=True)
    return run_probe(parser.parse_args(argv))


if __name__ == "__main__":
    main()
