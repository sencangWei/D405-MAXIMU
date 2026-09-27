"""Evaluation-only orientation/metric displacement diagnostic AFTER estimation.

Never imports the window solver or writes estimation inputs. Diagnostic graph
camera SE3 alignment is independent of final official body ATE and no-scale.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import evaluate_slam_ground_truth as evaluation
import align_mast3r_scale_with_stereo as stereo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite evaluation')
    controls = ROOT / 'reports/metric_window_bundle_20260928/controls_ten_v6_joint_geometry/summary.json'
    cases = json.loads(controls.read_text())['cases']
    paths = [Path(__file__), controls]
    results = []
    for case in cases:
        folder = args.candidate / case['case']
        graph_path = folder / 'graph_fusion_report.json'
        graph = json.loads(graph_path.read_text())
        precision_path = folder / 'official_score/precision.json'
        precision = json.loads(precision_path.read_text())
        trajectory_path = folder / 'trajectory_graph.csv'
        times, positions, quat, _ = stereo.load_trajectory(trajectory_path)
        rotations = Rotation.from_quat(quat)
        reference = Path(precision['ground_truth'])
        gt_times, gt_pos, gt_quat = evaluation.load_trajectory(reference)
        inside, valid, interpolated, quaternions = evaluation.interpolate_ground_truth(
            times, gt_times, gt_pos, gt_quat, precision['max_interpolation_gap_s'])
        source = np.flatnonzero(inside)[valid]
        mapping = np.full(len(times), -1, dtype=int)
        mapping[source] = np.arange(len(source))
        extrinsic = np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])
        body_rotations = Rotation.from_quat(quaternions)
        camera_positions = interpolated[:, 1:] + body_rotations.apply(extrinsic[:3, 3])
        camera_rotations = body_rotations * Rotation.from_matrix(extrinsic[:3, :3])
        alignment, _ = evaluation.rigid_align(positions[source], camera_positions)
        graph_to_reference = Rotation.from_matrix(alignment)
        rows = []
        for window in case['windows']:
            if not window['accepted']:
                continue
            first, last = window['indices'][0], window['indices'][-1]
            i, j = mapping[first], mapping[last]
            if min(i, j) < 0:
                continue
            world_delta = camera_positions[j] - camera_positions[i]
            local_delta = camera_rotations[i].inv().apply(world_delta)
            endpoint = np.asarray(window['endpoint_m'])
            graph_delta = positions[last] - positions[first]
            rows.append(dict(window=window['window'], first_index=first, second_index=last,
                metric_local_reference_error_mm=float(1000 * np.linalg.norm(endpoint - local_delta)),
                graph_local_reference_error_mm=float(1000 * np.linalg.norm(rotations[first].inv().apply(graph_delta) - local_delta)),
                graph_orientation_vs_reference_deg=float(np.degrees((camera_rotations[i].inv() * graph_to_reference * rotations[first]).magnitude())),
                metric_world_reference_error_mm=float(1000 * np.linalg.norm(graph_to_reference.apply(rotations[first].apply(endpoint)) - world_delta)),
                graph_world_reference_error_mm=float(1000 * np.linalg.norm(graph_to_reference.apply(graph_delta) - world_delta)),
                motion_length_mm=float(1000 * np.linalg.norm(world_delta))))
        results.append(dict(case=case['case'], windows=rows))
        paths += [graph_path, precision_path, trajectory_path, reference]
    args.output.write_text(json.dumps(dict(cases=results,
        source_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        external_reference_used_in_estimation=False, external_reference_used_in_evaluation=True,
        interpretation='diagnostic graph camera SE3-no-scale frame consistency; NOT final official ATE or factor admission'), indent=2) + '\n')
    for case in results:
        if case['case'] in {'fresh2', 'fresh4'}:
            print(json.dumps(case, indent=2))


if __name__ == '__main__':
    main()
