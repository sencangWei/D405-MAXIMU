"""Post-freeze relative window geometry evaluation, never estimator supervision.

All eight non-anchor centers per accepted nine-state pair are compared with
the unchanged official camera reference. These are NOT full trajectory ATE.
"""
import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate_slam_ground_truth as evaluation
import run_shape_window_controls as controls_adapter


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_hashes(hashes):
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('nonempty frozen hashes required')
    for raw, expected in hashes.items():
        path = Path(raw)
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f'frozen source changed or missing: {path}')


def add_hashes(current, incoming):
    for path, expected in incoming.items():
        if path in current and current[path] != expected:
            raise ValueError(f'frozen hash binding conflict: {path}')
        current[path] = expected


def snapshot(path, hashes):
    payload = Path(path).read_bytes()
    add_hashes(hashes, {str(path): hashlib.sha256(payload).hexdigest()})
    return payload


def json_snapshot(path, hashes):
    return json.loads(snapshot(path, hashes))


def pose_snapshot(path, hashes):
    rows = list(csv.DictReader(io.StringIO(snapshot(path, hashes).decode())))
    values = np.asarray([[float(row[k]) for k in ('t_sec', 'x', 'y', 'z', 'qx', 'qy', 'qz', 'qw')] for row in rows])
    if (values.ndim != 2 or values.shape[1] != 8 or not np.isfinite(values).all()
            or np.any(np.diff(values[:, 0]) <= 0)):
        raise ValueError('finite strictly timestamped pose snapshot required')
    return values[:, 0], values[:, 1:4], values[:, 4:]


def pair_factor(pair, windows):
    accepted = pair.get('accepted')
    if not isinstance(accepted, bool) or len(windows) != 2 or [w.get('accepted') for w in windows] != [accepted] * 2:
        raise ValueError('pair/endpoint acceptance mismatch')
    if not accepted:
        return None
    factors = [w.get('diagnostics', {}).get('stereo_window_shape_factor') for w in windows]
    if factors[0] != factors[1]:
        raise ValueError('paired diagnostics differ')
    factor = factors[0]
    if not factor or factor.get('available') is not True:
        return None
    metadata = factor.get('metadata', {})
    if (factor.get('diagnostic_only') is not True or factor.get('not_admissible_for_graph') is not True
            or factor.get('solver_accepted') is not True or metadata.get('available_for_graph') is not False
            or metadata.get('calibrated_covariance') is not False
            or metadata.get('statistical_independence_claimed') is not False):
        raise ValueError('diagnostic-only factor contract invalid')
    centers = np.asarray(factor['optimized_centers_m'])
    rotvecs = np.asarray(factor['optimized_rotvecs_camera_to_window'])
    if (centers.shape != (9, 3) or rotvecs.shape != (9, 3) or not np.isfinite(centers).all()
            or not np.isfinite(rotvecs).all() or np.linalg.norm(centers[0]) > 1e-12
            or np.linalg.norm(rotvecs[0]) > 1e-12):
        raise ValueError('nine-state first-camera gauge mismatch')
    return factor


def relative_errors(positions, rotations, reference, reference_rotations):
    positions, reference = np.asarray(positions), np.asarray(reference)
    if (positions.shape != (9, 3) or reference.shape != (9, 3)
            or not np.isfinite(positions).all() or not np.isfinite(reference).all()
            or len(rotations) != 9 or len(reference_rotations) != 9):
        raise ValueError('nine finite poses required')
    predicted = rotations[0].inv().apply(positions - positions[0])
    truth = reference_rotations[0].inv().apply(reference - reference[0])
    return 1000 * np.linalg.norm(predicted[1:] - truth[1:], axis=1)


def camera_reference(body_positions, body_rotations, extrinsic):
    extrinsic = np.asarray(extrinsic, dtype=float)
    if (extrinsic.shape != (4, 4) or not np.isfinite(extrinsic).all()
            or not np.allclose(extrinsic[3], [0, 0, 0, 1], atol=1e-12, rtol=0)
            or not np.allclose(extrinsic[:3, :3].T @ extrinsic[:3, :3], np.eye(3), atol=1e-10, rtol=0)
            or not np.isclose(np.linalg.det(extrinsic[:3, :3]), 1, atol=1e-10, rtol=0)):
        raise ValueError('proper rigid body-to-camera extrinsic required')
    return (body_positions + body_rotations.apply(extrinsic[:3, 3]),
            body_rotations * Rotation.from_matrix(extrinsic[:3, :3]))


def stats(values):
    return dict(count=len(values), mean_mm=float(np.mean(values)),
                median_mm=float(np.median(values)), p95_mm=float(np.percentile(values, 95)),
                max_mm=float(np.max(values))) if values else dict(count=0)


def score(controls_path, graph_root):
    hashes = {str(Path(__file__)): digest(Path(__file__)), str(Path(evaluation.__file__)): digest(Path(evaluation.__file__))}
    controls = json_snapshot(controls_path, hashes)
    cases = controls.get('cases', [])
    if len(cases) != 10 or {c['case'] for c in cases} != controls_adapter.EXPECTED_CASES:
        raise ValueError('exact ten cases must freeze before GT scoring')
    adapter = controls.get('shape_window_adapter', {})
    if (controls.get('external_reference_used') is not False
            or controls.get('production_modified') is not False
            or adapter.get('diagnostic_only') is not True
            or adapter.get('used_for_graph_or_selection') is not False):
        raise ValueError('frozen UMI-only diagnostic contract required')
    add_hashes(hashes, controls['source_sha256'])
    # Verify every case and input before opening any official GT files.
    for case in cases:
        controls_adapter._validate_case(case)
        add_hashes(hashes, case['input_sha256'])
        for number, pair in enumerate(case['pairs']):
            pair_factor(pair, case['windows'][2 * number:2 * number + 2])
    verify_hashes(hashes)
    result, ba_all, graph_all = [], [], []
    for case in cases:
        folder = graph_root / case['case']
        graph_path, pose_path = folder / 'graph_fusion_report.json', folder / 'trajectory_graph.csv'
        precision_path = folder / 'official_score/precision.json'
        graph = json_snapshot(graph_path, hashes)
        precision = json_snapshot(precision_path, hashes)
        original_paths = [Path(p) for p in case['input_sha256'] if Path(p).name == 'graph_fusion_report.json']
        if len(original_paths) != 1:
            raise ValueError('single bound original graph required')
        original = json_snapshot(original_paths[0], hashes)
        extrinsic = np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])
        if (graph['inputs']['session'] != original['inputs']['session']
                or not np.array_equal(extrinsic, original['camera_extrinsics']['effective_body_T_trajectory_camera'])
                or graph['camera_extrinsics']['trajectory_observation_frame'] != 'infrared_left_camera_i'
                or precision['alignment'] != 'SE3_estimate_to_external_ground_truth_no_scale'
                or Path(precision['estimate']).resolve() != (folder / 'trajectory_fused.csv').resolve()):
            raise ValueError('recording/frame/extrinsic/official contract mismatch')
        snapshot(Path(precision['estimate']), hashes)
        times, positions, quats = pose_snapshot(pose_path, hashes)
        if len(times) != controls_adapter.EXPECTED_RAW_COUNTS[case['case']]:
            raise ValueError('full graph raw pose count differs from census')
        stereo = json_snapshot(Path(original['inputs']['stereo_report']), hashes)
        input_times, _, _ = pose_snapshot(Path(stereo['trajectory']), hashes)
        if not np.array_equal(times, input_times):
            raise ValueError('graph absolute timestamps differ from raw census input')
        reference_path = Path(precision['ground_truth'])
        gt_times, gt_pos, gt_quats = pose_snapshot(reference_path, hashes)
        inside, valid, reference, attitudes = evaluation.interpolate_ground_truth(
            times, gt_times, gt_pos, gt_quats, precision['max_interpolation_gap_s'])
        mapping = np.full(len(times), -1, dtype=int)
        mapping[np.flatnonzero(inside)[valid]] = np.arange(len(reference))
        camera_pos, camera_rot = camera_reference(reference[:, 1:], Rotation.from_quat(attitudes), extrinsic)
        graph_rot = Rotation.from_quat(quats)
        rows = []
        for number, pair in enumerate(case['pairs']):
            row = dict(pair=number + 1, indices=pair['indices'], accepted=pair['accepted'], reason=pair['reason'], scored=False)
            windows = case['windows'][2 * number:2 * number + 2]
            elapsed = windows[0]['elapsed_s'] + windows[1]['elapsed_s'][1:]
            if not np.allclose(times[np.asarray(pair['indices'])] - times[0], elapsed, atol=1e-9, rtol=0):
                raise ValueError('graph timestamps differ from bound census schedule')
            if pair['accepted']:
                factor = pair_factor(pair, windows)
                if factor is None:
                    row['score_reason'] = 'shape_diagnostic_missing_or_unavailable'
                else:
                    indices = np.asarray(pair['indices'], dtype=int)
                    selected = mapping[indices]
                    if np.any(selected < 0):
                        row['score_reason'] = 'outside_unchanged_reference_coverage'
                    else:
                        centers = np.asarray(factor['optimized_centers_m'])
                        rotations = Rotation.from_rotvec(factor['optimized_rotvecs_camera_to_window'])
                        truth, truth_rot = camera_pos[selected], camera_rot[selected]
                        ba = relative_errors(centers, rotations, truth, truth_rot)
                        baseline = relative_errors(positions[indices], graph_rot[indices], truth, truth_rot)
                        row.update(scored=True, ba_local_node_errors_mm=ba.tolist(),
                                   current_joint_graph_local_node_errors_mm=baseline.tolist())
                        ba_all.extend(ba.tolist())
                        graph_all.extend(baseline.tolist())
            rows.append(row)
        result.append(dict(case=case['case'], pairs=rows))
    verify_hashes(hashes)
    return dict(cases=result, ba_local_geometry=stats(ba_all), current_graph_local_geometry=stats(graph_all),
                provenance_sha256=hashes, external_reference_used_in_estimation=False,
                external_reference_used_in_evaluation=True, used_for_selection_or_graph=False,
                warning='All non-anchor local relative centers, NOT full trajectory ATE; no fit or estimator selection; reference uncertainty remains')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--controls', type=Path, required=True)
    parser.add_argument('--graphs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('refuse to overwrite geometry evaluation')
    report = score(args.controls / 'summary.json', args.graphs)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('ba_local_geometry', 'current_graph_local_geometry')}, indent=2))


if __name__ == '__main__':
    main()
