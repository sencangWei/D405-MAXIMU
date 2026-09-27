"""Replay a bounded set of stereo measurements to inspect PnP conditioning.

Nominal covariance assumes fixed stereo 3D points and 1 px independent image
noise: this is a lower bound, not a calibrated error bar or an ATE prediction.
"""
import argparse
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('stereo', ROOT / 'scripts/align_mast3r_scale_with_stereo.py')
stereo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stereo)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite output')
    args.output.mkdir(parents=True)
    summary = []
    for take, center in [(2, 581), (4, 836), (4, 1071)]:
        directory = ROOT / f'reports/joint_scale_independent_four_20260927/take{take}/fusion/{"rescue" if take == 2 else "baseline"}/mast3r'
        graph = json.loads((directory / 'graph_fusion_report.json').read_text())
        primary = json.loads(Path(graph['inputs']['stereo_report']).read_text())
        calibration = dict(primary['factory_stereo_calibration'])
        calibration['left'] = calibration['left_intrinsics']
        calibration['right'] = calibration['right_intrinsics']
        times, positions, quaternions, _ = stereo.load_trajectory(Path(primary['trajectory']))
        rotations = Rotation.from_quat(quaternions)
        trace = np.load(ROOT / f'reports/stereo_factor_attribution_20260927/observational_v1/fresh{take}/trace.npz')
        body_rotation = Rotation.from_quat(trace['body_rotation_xyzw'])
        body_t_camera = np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])
        imu_camera_rotation = body_rotation * Rotation.from_matrix(body_t_camera[:3, :3])
        selected = []
        for report_path in [graph['inputs']['stereo_report']] + graph['inputs']['additional_stereo_reports']:
            report = json.loads(Path(report_path).read_text())
            edges = [edge for edge in report['observations'] if edge.get('accepted')]
            nearest = sorted(edges, key=lambda edge: abs((edge['first_index'] + edge['second_index']) / 2 - center))[:3]
            selected.extend(nearest)
        left_numbers, right_numbers, _ = stereo.match_trajectory_to_stereo_frames(Path(primary['session']) / 'd405_frames.csv', times, trajectory_frame='infrared_left')
        indexes = {edge[key] for edge in selected for key in ('first_index', 'second_index')}
        left, right = stereo.load_selected_prepared_stereo_images(directory / 'dataset', Path(primary['session']) / 'd405_frames.csv',
                    {int(left_numbers[i]) for i in indexes}, {int(right_numbers[i]) for i in indexes})
        original = cv2.solvePnPRansac
        original_combine = stereo.combine_bidirectional_scale
        records = []
        vector_checks = []

        def inspect_bidirectional(forward, reverse, *args, **kwargs):
            combined = original_combine(forward, reverse, *args, **kwargs)
            if forward.get('accepted') and reverse.get('accepted'):
                first_delta = np.asarray(forward['metric_displacement_camera_i_m'])
                reverse_rotation = Rotation.from_quat(forward['pnp_rotation_quaternion_xyzw']).inv()
                reverse_delta_in_first = -reverse_rotation.apply(reverse['metric_displacement_camera_i_m'])
                vector_checks.append({'scale_gate_accepted': bool(combined.get('accepted')),
                    'forward_delta_m': first_delta.tolist(), 'reverse_delta_in_first_m': reverse_delta_in_first.tolist(),
                    'closure_mm': float(np.linalg.norm(first_delta - reverse_delta_in_first) * 1000),
                    'relative_vector_disagreement': float(np.linalg.norm(first_delta - reverse_delta_in_first) / max(0.5 * (np.linalg.norm(first_delta) + np.linalg.norm(reverse_delta_in_first)), 1e-12))})
            return combined

        def inspect_pnp(object_points, image_points, matrix, distortion, **kwargs):
            result = original(object_points, image_points, matrix, distortion, **kwargs)
            solved, rvec, tvec, inliers = result
            if solved and inliers is not None and len(inliers) >= 20:
                indices = inliers.ravel()
                xyz, uv = object_points[indices], image_points[indices]
                projected, jacobian = cv2.projectPoints(xyz, rvec, tvec, matrix, distortion)
                j = jacobian[:, :6]
                covariance = np.linalg.pinv(j.T @ j, rcond=1e-12)
                std = np.sqrt(np.maximum(np.diag(covariance), 0))
                correlation = covariance / np.maximum(std[:, None] * std[None, :], 1e-20)
                records.append({'inliers': int(len(indices)),
                    'nominal_translation_std_mm': (std[3:] * 1000).tolist(),
                    'nominal_rotation_std_deg': np.degrees(std[:3]).tolist(),
                    'rotation_translation_correlation_max': float(np.max(np.abs(correlation[:3, 3:]))),
                    'depth_median_m': float(np.median(xyz[:, 2])),
                    'object_points_spread_std_m': np.std(xyz, axis=0).tolist(),
                    'bottom_quarter_image_fraction': float(np.mean(uv[:, 1] > 0.75 * calibration['left']['height'])),
                    'reprojection_median_px': float(np.median(np.linalg.norm(projected.reshape(-1, 2) - uv, axis=1))),
                    'normal_matrix_condition': float(np.linalg.cond(j.T @ j))})
            return result

        cv2.solvePnPRansac = inspect_pnp
        stereo.combine_bidirectional_scale = inspect_bidirectional
        try:
            for edge in selected:
                first, second = edge['first_index'], edge['second_index']
                records.clear()
                vector_checks.clear()
                cv2.setRNGSeed(0)
                replay = stereo.estimate_pair_scale(left[int(left_numbers[first])], right[int(right_numbers[first])],
                    left[int(left_numbers[second])], right[int(right_numbers[second])], positions[first], positions[second],
                    rotations[first], rotations[second], calibration, 128, 0.07, 1.5, trajectory_frame='infrared_left')
                expected = imu_camera_rotation[second].inv() * imu_camera_rotation[first]
                measured = Rotation.from_quat(edge['pnp_rotation_quaternion_xyzw'])
                rotation_error = float(np.degrees((expected.inv() * measured).magnitude()))
                item = {'take': take, 'window_center': center, 'first': first, 'second': second,
                    'cached': edge, 'replayed': replay, 'pnp_solves': list(records),
                    'bidirectional_vectors': list(vector_checks),
                    'cached_pnp_vs_imu_rotation_deg': rotation_error,
                    'rotation_depth_coupling_length_mm': float(edge['median_depth_m'] * np.radians(rotation_error) * 1000)}
                summary.append(item)
                print(take, center, first, second, replay.get('accepted'), 'rot_error_deg', round(rotation_error, 4), 'bidirectional_vectors', vector_checks, flush=True)
        finally:
            cv2.solvePnPRansac = original
            stereo.combine_bidirectional_scale = original_combine
    (args.output / 'measurements.json').write_text(json.dumps({'external_reference_used': False,
        'covariance_assumptions': 'fixed noiseless stereo points; independent 1px isotropic image noise; covariance is not calibrated',
        'measurements': summary}, indent=2) + '\n')


if __name__ == '__main__':
    main()
