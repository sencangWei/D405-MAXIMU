"""Bounded all-case measurement diagnostic; never changes or selects a trajectory.

Select accepted measurements nearest 10%, 30%, 50%, 70%, and 90% of each recording in
each of the four report families, before seeing GT. Compare unconstrained PnP,
raw MASt3R-attitude-fixed PnP, and calibrated raw gyro-fixed PnP. The latter
does NOT use graph-refined orientation as an allegedly independent IMU input.
VINS displacement is a cross-source diagnostic, not truth or a selection rule.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation, Slerp

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def argument_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    # Formal workflow uses 0.6m. Explicit 1.5m reproduces the old diagnostic,
    # which was not a production-equivalent replay.
    parser.add_argument('--max-depth-m', type=float, default=0.6)
    return parser


def main():
    args = argument_parser().parse_args()
    if not np.isfinite(args.max_depth_m) or args.max_depth_m <= 0.07:
        raise ValueError('Maximum depth must be finite and greater than 0.07m')
    if args.output.exists():
        raise ValueError('Refuse to overwrite diagnostic output')
    args.output.mkdir(parents=True)
    cv2.setNumThreads(2)
    stereo = load_module('gyro_probe_stereo', ROOT / 'scripts/align_mast3r_scale_with_stereo.py')
    fusion = load_module('gyro_probe_fusion', ROOT / 'scripts/fuse_mast3r_stereo_imu.py')
    cached = load_module('gyro_probe_cases', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    cases = [(name, ROOT / 'reports' / source / 'mast3r/graph_fusion_report.json')
             for name, source, _ in cached.CASES]
    cases += [(f'fresh{i}', ROOT / f'reports/joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}/mast3r/graph_fusion_report.json')
              for i in range(1, 5)]
    summary = []
    original_combine = stereo.combine_bidirectional_scale
    for name, graph_path in cases:
        graph = json.loads(graph_path.read_text())
        inputs = graph['inputs']
        report_paths = [inputs['stereo_report']] + inputs['additional_stereo_reports']
        source_hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in report_paths}
        reports = [json.loads(Path(path).read_text()) for path in report_paths]
        primary = reports[0]
        assert primary['observation_frame'] == 'infrared_left_camera_i'
        assert graph['camera_extrinsics']['trajectory_observation_frame'] == 'infrared_left_camera_i'
        calibration = dict(primary['factory_stereo_calibration'])
        calibration['left'], calibration['right'] = calibration['left_intrinsics'], calibration['right_intrinsics']
        times, positions, quaternions, _ = stereo.load_trajectory(Path(primary['trajectory']))
        rotations = Rotation.from_quat(quaternions)
        session = Path(inputs['session'])
        mono = fusion.camera_epoch_to_monotonic(session / 'd405_frames.csv', 'infrared_left', times)
        imu_times, gyro, _, imu_info = fusion.load_calibrated_imu(session / 'external_imu/imu.bin', Path(inputs['imu_calibration']))
        config = fusion.load_vins_config(Path(inputs['vins_spatiotemporal_calibration']), -0.009109323)
        body_t_camera = np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])
        body_from_camera = Rotation.from_matrix(body_t_camera[:3, :3])
        selected = []
        seen = set()
        for path, report in zip(report_paths, reports):
            edges = [edge for edge in report['observations'] if edge.get('accepted')]
            for fraction in (0.1, 0.3, 0.5, 0.7, 0.9):
                if not edges:
                    continue
                target = times[0] + fraction * (times[-1] - times[0])
                edge = min(edges, key=lambda item: abs(0.5 * (times[item['first_index']] + times[item['second_index']]) - target))
                pair = (edge['first_index'], edge['second_index'])
                if pair not in seen:
                    selected.append((Path(path).name, fraction, edge))
                    seen.add(pair)
        left_numbers, right_numbers, _ = stereo.match_trajectory_to_stereo_frames(session / 'd405_frames.csv', times, trajectory_frame='infrared_left')
        indices = {edge[key] for _, _, edge in selected for key in ('first_index', 'second_index')}
        left, right = stereo.load_selected_prepared_stereo_images(Path(primary['trajectory']).parent / 'dataset', session / 'd405_frames.csv',
            {int(left_numbers[index]) for index in indices}, {int(right_numbers[index]) for index in indices})
        vins_times, vins_positions, vins_quaternions, _ = stereo.load_trajectory(Path(inputs['relative_motion_trajectory']))
        vins_rotations = Rotation.from_quat(vins_quaternions)
        vins_camera = vins_positions + vins_rotations.apply(body_t_camera[:3, 3])
        inverse_checks = []
        pnp_fingerprints = []
        original_pnp = cv2.solvePnPRansac

        def observe_pnp(object_points, image_points, *args, **kwargs):
            result = original_pnp(object_points, image_points, *args, **kwargs)
            fingerprint = hashlib.sha256(np.ascontiguousarray(object_points).tobytes() + np.ascontiguousarray(image_points).tobytes())
            if result[3] is not None:
                fingerprint.update(np.ascontiguousarray(result[3]).tobytes())
            pnp_fingerprints.append(fingerprint.hexdigest())
            return result

        def observe_combine(forward, reverse, *args, **kwargs):
            if forward.get('accepted') and reverse.get('accepted'):
                inverse = -Rotation.from_quat(forward['pnp_rotation_quaternion_xyzw']).inv().apply(reverse['metric_displacement_camera_i_m'])
                inverse_checks.append(float(np.linalg.norm(np.asarray(forward['metric_displacement_camera_i_m']) - inverse) * 1000))
            return original_combine(forward, reverse, *args, **kwargs)

        stereo.combine_bidirectional_scale = observe_combine
        cv2.solvePnPRansac = observe_pnp
        records = []
        disparity_cache = {}
        try:
            for family, fraction, edge in selected:
                first, second = edge['first_index'], edge['second_index']
                body_delta = fusion.integrate_gyro(imu_times, gyro, mono[first] + config['td_s'], mono[second] + config['td_s'])
                camera_delta = body_from_camera.inv() * body_delta * body_from_camera
                expected_pnp = camera_delta.inv()
                item = {'family': family, 'fraction': fraction, 'first': first, 'second': second,
                        'duration_s': float(times[second] - times[first]), 'cached_method': edge.get('method'),
                        'gyro_rotation_deg': float(np.degrees(body_delta.magnitude()))}
                for label, mode in (('free', 'free'), ('mast3r_fixed', 'trajectory-fixed'), ('raw_gyro_fixed', 'trajectory-fixed')):
                    inverse_checks.clear()
                    pnp_fingerprints.clear()
                    cv2.setRNGSeed(0)
                    second_rotation = rotations[first] * camera_delta if label == 'raw_gyro_fixed' else rotations[second]
                    measurement = stereo.estimate_pair_scale(left[int(left_numbers[first])], right[int(right_numbers[first])],
                        left[int(left_numbers[second])], right[int(right_numbers[second])], positions[first], positions[second],
                        rotations[first], second_rotation, calibration, 128, 0.07, args.max_depth_m,
                        trajectory_frame='infrared_left', pnp_rotation_mode=mode)
                    reverse_diagnostic = None
                    if measurement.get('accepted') and measurement.get('method') == 'sift' and not inverse_checks:
                        if second not in disparity_cache:
                            disparity_cache[second] = stereo.stereo_disparity(left[int(left_numbers[second])], right[int(right_numbers[second])], 128)
                        cv2.setRNGSeed(0)
                        reverse_diagnostic = stereo.estimate_sift_fallback(left[int(left_numbers[second])], left[int(left_numbers[first])],
                            *disparity_cache[second], positions[second], positions[first], second_rotation, rotations[first],
                            calibration, 0.07, args.max_depth_m, 'infrared_left', mode)
                        if reverse_diagnostic.get('accepted'):
                            inverse = -Rotation.from_quat(measurement['pnp_rotation_quaternion_xyzw']).inv().apply(reverse_diagnostic['metric_displacement_camera_i_m'])
                            inverse_checks.append(float(np.linalg.norm(np.asarray(measurement['metric_displacement_camera_i_m']) - inverse) * 1000))
                    item[label] = {'measurement': measurement, 'reverse_closure_mm': list(inverse_checks),
                                   'reverse_closure_measured': bool(inverse_checks), 'explicit_sift_reverse': reverse_diagnostic,
                                   'pnp_correspondence_and_inlier_sha256': list(pnp_fingerprints)}
                    if measurement.get('accepted'):
                        measured_rotation = Rotation.from_quat(measurement['pnp_rotation_quaternion_xyzw'])
                        item[label]['pnp_vs_raw_gyro_rotation_deg'] = float(np.degrees((expected_pnp.inv() * measured_rotation).magnitude()))
                        if label == 'raw_gyro_fixed':
                            assert item[label]['pnp_vs_raw_gyro_rotation_deg'] < 1e-8, 'Fixed PnP rotation convention mismatch'
                        if vins_times[0] <= times[first] < times[second] <= vins_times[-1]:
                            vins_delta = np.array([np.interp(times[second], vins_times, vins_camera[:, axis]) -
                                                   np.interp(times[first], vins_times, vins_camera[:, axis]) for axis in range(3)])
                            camera_rotation = Slerp(vins_times, vins_rotations)([times[first]])[0] * body_from_camera
                            camera_delta_in_vins = camera_rotation.apply(measurement['metric_displacement_camera_i_m'])
                            item[label]['vs_vins_displacement_mm'] = float(np.linalg.norm(camera_delta_in_vins - vins_delta) * 1000)
                first_inputs = [item[label]['pnp_correspondence_and_inlier_sha256'][:1]
                                for label in ('free', 'mast3r_fixed', 'raw_gyro_fixed')]
                item['same_first_pnp_correspondences_and_inliers'] = all(value == first_inputs[0] for value in first_inputs)
                assert item['same_first_pnp_correspondences_and_inliers'], 'PnP input/inlier replay mismatch'
                records.append(item)
                print(name, first, second, 'free/fixed accepted', item['free']['measurement'].get('accepted'),
                      item['raw_gyro_fixed']['measurement'].get('accepted'), flush=True)
        finally:
            stereo.combine_bidirectional_scale = original_combine
            cv2.solvePnPRansac = original_pnp
        for path, expected in source_hashes.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
        case = {'case': name, 'external_reference_used': False, 'trajectory_modified': False,
                'replay_parameters': {'num_disparities': 128, 'min_depth_m': 0.07,
                                      'max_depth_m': args.max_depth_m},
                'selection': '10%, 30%, 50%, 70%, 90% recording-time quantiles per report family; accepted edges only; duplicate pairs removed',
                'rotation_source': 'raw calibrated gyro preintegration, fixed formal td applied once; no graph-refined attitude',
                'bias_policy': 'fixed accepted runtime gyro calibration; no per-recording bias fit',
                'imu': imu_info, 'source_sha256': source_hashes, 'measurements': records}
        (args.output / f'{name}.json').write_text(json.dumps(case, indent=2) + '\n')
        summary.append({'case': name, 'measurements': len(records), 'free_accepted': sum(item['free']['measurement'].get('accepted', False) for item in records),
                        'mast3r_fixed_accepted': sum(item['mast3r_fixed']['measurement'].get('accepted', False) for item in records),
                        'raw_gyro_fixed_accepted': sum(item['raw_gyro_fixed']['measurement'].get('accepted', False) for item in records)})
    (args.output / 'summary.json').write_text(json.dumps({'external_reference_used': False, 'cases': summary}, indent=2) + '\n')


if __name__ == '__main__':
    main()
