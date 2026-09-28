#!/usr/bin/env python3
"""Replay one frozen joint graph and capture its existing solver state only."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fuse_mast3r_seam_pair_windows as wrapper  # noqa: E402
import fuse_mast3r_stereo_imu as native  # noqa: E402

JOINT = ROOT / "reports/metric_window_bundle_20260928/seam_graph_full_ten_v1/joint"
TRACE = ROOT / "reports/metric_window_bundle_20260928/native_solver_trace_v2"
CASES = {"dev1", "dev2", "fresh1", "fresh2", "fresh3", "fresh4",
         "heldout1", "heldout2", "heldout3", "heldout4"}
LOCAL_KEYS = ("node_indices", "visual_times_mono", "positions", "body_positions",
              "camera_rotations", "relative_motion_positions_body",
              "relative_motion_weights", "imu_position_weights",
              "imu_velocity_weights", "stereo_weights", "stereo_prior_weights",
              "visual_prior_weights", "position_correction", "scale_delta",
              "scale_basis", "velocities", "gravity", "bias", "preintegrations",
              "accepted", "frame_left", "frame_right", "frame_alpha",
              "correction", "refined")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def with_output_paths(args, output, report):
    values = list(args)
    for flag, path in (("--output", output), ("--report", report)):
        matches = [index for index, value in enumerate(values) if value == flag]
        if len(matches) != 1 or matches[0] + 1 >= len(values):
            raise ValueError(f"frozen command missing single {flag}")
        values[matches[0] + 1] = str(path)
    return values


def native_state(locals_):
    """Re-evaluate final signed factors without modifying any solver values."""
    missing = set(LOCAL_KEYS) - locals_.keys()
    if missing:
        raise ValueError(f"solver state missing {sorted(missing)}")
    nodes = locals_["node_indices"]
    body = locals_["body_positions"]
    times = locals_["visual_times_mono"]
    positions = locals_["positions"]
    pos_corr = locals_["position_correction"]
    scale = float(locals_["scale_delta"])
    total_corr = pos_corr + scale * locals_["scale_basis"][nodes]
    velocities = locals_["velocities"]
    gravity = locals_["gravity"]
    bias = locals_["bias"]
    imu_rows, relative_rows, stereo_rows = [], [], []
    relative = locals_["relative_motion_positions_body"]
    for index, (first, second, preintegration) in enumerate(zip(
            nodes[:-1], nodes[1:], locals_["preintegrations"])):
        first, second = int(first), int(second)
        dt = float(times[second] - times[first])
        dp, dv, jp, jv = preintegration
        displacement = body[second] - body[first] + total_corr[index + 1] - total_corr[index]
        pos_residual = displacement - velocities[index] * dt - 0.5 * gravity * dt * dt - dp - jp @ bias
        vel_residual = velocities[index + 1] - velocities[index] - gravity * dt - dv - jv @ bias
        imu_rows.append({"first": first, "second": second,
                         "position_residual_mm": (1000 * pos_residual).tolist(),
                         "velocity_residual_mps": vel_residual.tolist(),
                         "position_weight": float(locals_["imu_position_weights"][index]),
                         "velocity_weight": float(locals_["imu_velocity_weights"][index])})
        if relative is not None:
            reference = relative[second] - relative[first]
            relative_rows.append({"first": first, "second": second,
                                  "residual_mm": (1000 * (displacement - reference)).tolist(),
                                  "weight": float(locals_["relative_motion_weights"][index])})

    left, right, alpha = (locals_[key] for key in ("frame_left", "frame_right", "frame_alpha"))

    def correction_at(frame):
        return (1 - alpha[frame]) * pos_corr[left[frame]] + alpha[frame] * pos_corr[right[frame]]

    for index, observation in enumerate(locals_["accepted"]):
        first, second = int(observation["first_index"]), int(observation["second_index"])
        target = locals_["camera_rotations"][first].apply(
            np.asarray(observation["metric_displacement_camera_i_m"], float))
        visual_delta = positions[second] - positions[first]
        residual = (visual_delta * (1 + scale) + correction_at(second)
                    - correction_at(first) - target)
        stereo_rows.append({"first": first, "second": second,
                            "residual_mm": (1000 * residual).tolist(),
                            "robust_weight": float(locals_["stereo_weights"][index]),
                            "prior_weight": float(locals_["stereo_prior_weights"][index])})
    return {"node_indices": nodes.tolist(),
            "requested_node_position_correction_mm": (1000 * total_corr).tolist(),
            "applied_interpolated_correction_mm": (1000 * locals_["correction"]).tolist(),
            "visual_prior_weights": locals_["visual_prior_weights"].tolist(),
            "joint_scale_ratio": 1 + scale,
            "weight_semantics": "post-fourth-IRLS-update diagnostics, not re-solved weights",
            "imu_edges": imu_rows, "relative_motion_edges": relative_rows,
            "stereo_edges": stereo_rows}


def main(case):
    if case not in CASES:
        raise ValueError("case must be one of the ten frozen runs")
    source = JOINT / case
    manifest = json.loads((source / "manifest.json").read_text())
    graph_commands = [args for stage, args in manifest["commands"] if stage == "graph"]
    if len(graph_commands) != 1 or not graph_commands[0][1].endswith("fuse_mast3r_seam_pair_windows.py"):
        raise ValueError("unexpected frozen graph command")
    destination = TRACE / case
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite trace {destination}")
    destination.mkdir(parents=True)
    output = destination / "trajectory_graph.csv"
    report = destination / "graph_fusion_report.json"
    args = with_output_paths(graph_commands[0][2:], output, report)
    captured = []
    code = native.refine_positions_visual_inertial.__code__

    def profile(frame, event, _arg):
        if event == "return" and frame.f_code is code:
            captured.append({key: frame.f_locals[key] for key in LOCAL_KEYS})

    try:
        sys.setprofile(profile)
        result = wrapper.main(args)
    finally:
        sys.setprofile(None)
    if result != 0 or len(captured) != 1:
        raise ValueError(f"native solver trace failed: code={result}, captures={len(captured)}")
    frozen_output = source / "trajectory_graph.csv"
    if output.read_bytes() != frozen_output.read_bytes():
        raise ValueError("instrumentation changed frozen trajectory bytes")
    trace = native_state(captured[0])
    expected_edges = json.loads(report.read_text())["stereo_translation_fusion"]["stereo_edges"]
    if len(trace["stereo_edges"]) != expected_edges:
        raise ValueError("captured stereo factor count does not match report")
    trace.update({"case": case, "external_ground_truth_used": False,
                  "frozen_trajectory_byte_identical": True,
                  "source_and_output_sha256": {str(path): digest(path) for path in
                       (Path(__file__), Path(wrapper.__file__), Path(native.__file__),
                        source / "manifest.json", frozen_output, output, report)}})
    (destination / "solver_trace.json").write_text(json.dumps(trace, indent=2, allow_nan=False) + "\n")
    print(destination / "solver_trace.json")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: trace_native_solver_state.py CASE")
    main(sys.argv[1])
