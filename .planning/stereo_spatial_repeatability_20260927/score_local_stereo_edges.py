"""Score frozen stereo observations against existing reference AFTER estimation.

Never writes measurements, trajectories, calibration, weights or selection rules.
Use the same preselected166pairs; exported reference includes accepted fixed
Tracker/body extrinsics and time alignment, not a new fitted transformation.
"""
import importlib.util
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def camera_reference(body_positions, body_rotations, body_t_camera):
    extrinsic_rotation = Rotation.from_matrix(body_t_camera[:3, :3])
    return body_positions + body_rotations.apply(body_t_camera[:3, 3]), body_rotations * extrinsic_rotation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    out = parser.parse_args().output
    if out.exists():
        raise ValueError('Refuse overwrite')
    out.mkdir()
    fusion = module('edge_score_fusion', ROOT / 'scripts/fuse_mast3r_stereo_imu.py')
    evaluation = module('edge_evaluation', ROOT / 'scripts/evaluate_slam_ground_truth.py')
    cached = module('edge_score_cases', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    cases = [(name, ROOT / 'reports' / source / 'mast3r/graph_fusion_report.json',
              ROOT / f'reports/joint_metric_scale_20260927/verified_v1_six/{name}/official_score/precision.json') for name, source, _ in cached.CASES]
    cases += [(f'fresh{i}', ROOT / f'reports/joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}/mast3r/graph_fusion_report.json',
               ROOT / f'reports/joint_scale_independent_four_20260927/take{i}/official_score/precision.json') for i in range(1, 5)]
    summary = []
    for name, graph_path, score_path in cases:
        graph = json.loads(graph_path.read_text())
        score = json.loads(score_path.read_text())
        reference_path = Path(score['ground_truth'])
        probe_path = ROOT / f'reports/stereo_factor_attribution_20260927/raw_gyro_pnp_v1/{name}.json'
        times, _, _, _ = fusion.load_trajectory(Path(graph['inputs']['trajectory']))
        gt_times, gt_positions, gt_quats = evaluation.load_trajectory(reference_path)
        inside, accepted_time, interpolated, quaternions = evaluation.interpolate_ground_truth(times, gt_times, gt_positions, gt_quats, .05)
        indices = np.flatnonzero(inside)[accepted_time]
        ext = np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])
        camera_positions, camera_rotations = camera_reference(interpolated[:, 1:], Rotation.from_quat(quaternions), ext)
        mapping = np.full(len(times), -1, dtype=int)
        mapping[indices] = np.arange(len(indices))
        sampled = json.loads(probe_path.read_text())['measurements']
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (reference_path, probe_path, graph_path, score_path)}
        stereo_paths = [Path(graph['inputs']['stereo_report'])] + [Path(p) for p in graph['inputs']['additional_stereo_reports']]
        reports = {path.name: json.loads(path.read_text()) for path in stereo_paths}
        hashes.update({str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in stereo_paths})
        rows = []
        for sample in sampled:
            first, second = sample['first'], sample['second']
            measurement = next(edge for edge in reports[sample['family']]['observations']
                               if edge['first_index']==first and edge['second_index']==second and edge.get('accepted'))
            if not measurement.get('accepted'):
                rows.append(dict(first=first, second=second, status='cached_pnp_rejected'))
                continue
            i, j = mapping[first], mapping[second]
            if min(i, j) < 0:
                rows.append(dict(first=first, second=second, status='outside_reference_coverage'))
                continue
            reference_delta = camera_rotations[i].inv().apply(camera_positions[j] - camera_positions[i])
            measured_delta = np.asarray(measurement['metric_displacement_camera_i_m'])
            residual = measured_delta - reference_delta
            reference_pnp = camera_rotations[j].inv() * camera_rotations[i]
            measured_pnp = Rotation.from_quat(measurement['pnp_rotation_quaternion_xyzw'])
            rows.append(dict(first=first, second=second, family=sample['family'], status='scored',
                             duration_s=sample['duration_s'], reference_motion_mm=float(np.linalg.norm(reference_delta)*1000),
                             measured_delta_m=measured_delta.tolist(), reference_delta_m=reference_delta.tolist(),
                             local_displacement_error_mm=float(np.linalg.norm(residual)*1000),
                             local_error_vector_mm=(residual*1000).tolist(),
                             rotation_error_deg=float(np.degrees((reference_pnp.inv()*measured_pnp).magnitude()))))
        valid_rows = [row for row in rows if row['status']=='scored']
        errors = [row['local_displacement_error_mm'] for row in valid_rows]
        row = dict(case=name, sampled_edges=len(rows), scored_edges=len(errors),
                   displacement_median_mm=float(np.median(errors)), displacement_p95_mm=float(np.percentile(errors,95)),
                   displacement_max_mm=max(errors), rotation_median_deg=float(np.median([r['rotation_error_deg'] for r in valid_rows])))
        case = dict(summary=row, external_reference_used_in_evaluation=True, external_reference_used_in_optimization=False,
                    trajectory_modified=False, measurements_modified=False,
                    measurement_source='original cached production stereo report, not newly recomputed freePnP subset',
                    reference_policy='existing official body reference; fixed body-to-leftIR lever; no new time/SE3/scale fit',
                    warning='Local stereo motion evaluation, not full-trajectory ATE; reference calibration uncertainty remains.',
                    source_sha256=hashes, measurements=rows)
        for path, digest in hashes.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
        (out/f'{name}.json').write_text(json.dumps(case,indent=2)+'\n')
        summary.append(row)
        print(row,flush=True)
    (out/'summary.json').write_text(json.dumps(dict(cases=summary, external_reference_used_in_optimization=False),indent=2)+'\n')


if __name__=='__main__':
    main()
