"""Frozen UMI-only adjacent-window pixel controls, not production selection."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT/'scripts'))
import run_observation_controls as base
from join_stereo_windows import boundary_matches, join_stereo_windows


def pair_windows(count):
    if count < 197:
        raise ValueError('recording too short for fixed five adjacent pairs')
    return [np.arange(round(f*(count-1))-20, round(f*(count-1))+21, 5)
            for f in (.1, .3, .5, .7, .9)]


def relative_endpoints(centers, rotations):
    centers = np.asarray(centers)
    if centers.shape != (9, 3) or len(rotations) != 9 or not np.isfinite(centers).all():
        raise ValueError('nine joint poses required')
    return [rotations[i].inv().apply(centers[j]-centers[i]) for i, j in [(0, 4), (4, 8)]]


def birth_heldout_rmse(data, calibration, centers, rotations):
    errors = []
    for point in np.flatnonzero(data['heldout']):
        birth = int(data['birth_indices'][point])
        # Withheld source stereo depth; no fitted landmark or train pixel used.
        global_point = rotations[birth].apply(data['birth_points'][point])+centers[birth]
        for node in range(birth+1, len(centers)):
            if not data['valid'][node, point]:
                continue
            camera = rotations[node].inv().apply(global_point-centers[node])
            if camera[2] <= 0:
                raise ValueError('heldout_negative_depth')
            for key, offset, columns in [('left_intrinsics', 0., slice(0, 2)),
                    ('right_intrinsics', calibration['baseline_m'], slice(2, 4))]:
                k = calibration[key]
                pixel = [k['fx']*(camera[0]-offset)/camera[2]+k['cx'],
                         k['fy']*camera[1]/camera[2]+k['cy']]
                errors.extend(pixel-data['observations'][node, point, columns])
    return float(np.sqrt(np.mean(np.square(errors)))) if errors else None


def endpoint_rows(result, selection, timestamps, initial_centers, initial_rotations, pair):
    rows = []
    initials = relative_endpoints(initial_centers, initial_rotations)
    endpoints = relative_endpoints(result['centers'], result['rotations']) if result['accepted'] else None
    for part, local in enumerate([selection[:5], selection[4:]]):
        row = dict(window=2*(pair-1)+part+1, joint_pair=pair, indices=local.tolist(),
                   elapsed_s=(timestamps[local]-timestamps[0]).tolist(),
                   accepted=result['accepted'], reason=result['reason'],
                   correlated_factors_from_shared_window=True,
                   initial_endpoint_m=initials[part].tolist())
        for key in ('diagnostics', 'detail', 'failures'):
            if key in result:
                row[key] = result[key]
        if endpoints is not None:
            row['endpoint_m'] = endpoints[part].tolist()
        rows.append(row)
    return rows


def solve(data, calibration, times, deltas, jacobians):
    train = base.training_support(data['train'], data['admitted_train'], data['initial_points'])
    return base.solve_stereo_window(data['observations'][:, train], data['admitted_train'][:, train],
        times-times[0], calibration['left_intrinsics'], calibration['right_intrinsics'],
        calibration['baseline_m'], data['initial_points'][train],
        data['initial_centers'], data['initial_rotations'], deltas,
        gyro_noise_density=.00103, gyro_bias_sigma=.01/3., gyro_bias_jacobians=jacobians)


def process_pair(left, right, calibration, times, deltas, jacobians):
    datasets = [base.track_stereo_window(left[start:start+21], right[start:start+21],
                calibration, initialize_poses=False) for start in (0, 20)]
    for data in datasets:
        if not data['accepted']:
            raise ValueError('raw_tracking_refused:'+data['reason'])
        data['observations'] = data['observations'][::5]
        data['valid'] = data['valid'][::5]
    matches = boundary_matches(*datasets)
    holdouts = [np.arange(len(datasets[0]['initial_points'])) % 5 == 0, matches['holdout_b']]
    poses, admissions, independent = [], [], []
    for part, (data, heldout) in enumerate(zip(datasets, holdouts)):
        centers, rotations, admission = base.visual_initialization(data, calibration, ~heldout)
        poses.append((centers, rotations))
        admissions.append(admission)
        supported = base.training_support(~heldout, admission, data['initial_points'])
        result = base.solve_stereo_window(data['observations'][:, supported], admission[:, supported],
            times[part*4:part*4+5]-times[part*4], calibration['left_intrinsics'],
            calibration['right_intrinsics'], calibration['baseline_m'], data['initial_points'][supported],
            centers, rotations, deltas[part*4:part*4+4], gyro_noise_density=.00103,
            gyro_bias_sigma=.01/3., gyro_bias_jacobians=jacobians[part*4:part*4+4])
        result['initial_endpoint_m'] = centers[-1].tolist()
        if result['accepted']:
            try:
                result['heldout_rmse_px'] = base.heldout_rmse(data, calibration, heldout,
                                                           result['centers'], result['rotations'])
            except ValueError as error:
                result.update(heldout_rmse_px=None, heldout_unavailable_reason=str(error))
        independent.append(result)
    try:
        joined = join_stereo_windows(*datasets, *admissions,
            *poses[0], *poses[1], first_holdout=holdouts[0], second_holdout=holdouts[1])
    except ValueError as error:
        # Joint refusal must not erase already-completed independent controls.
        ca, ra = poses[0]
        cb, rb = poses[1]
        initialization = dict(initial_centers=np.vstack((ca, ra[-1].apply(cb[1:])+ca[-1])),
                              initial_rotations=Rotation.concatenate([ra, ra[-1]*rb[1:]]))
        return dict(accepted=False, reason='joint_preparation_failed', detail=str(error)), independent, dict(
            joint_preparation_detail=str(error), correlated_factors_from_shared_window=True), initialization
    shared = joined['shared_train_cross_window'] & joined['train']
    try:
        if not base.node_geometry_supported(joined['initial_points'][shared]):
            result = dict(accepted=False, reason='insufficient_shared_training_geometry')
        else:
            result = solve(joined, calibration, times, deltas, jacobians)
    except (ValueError, RuntimeError, cv2.error) as error:
        result = dict(accepted=False, reason='joint_solve_failed', detail=str(error))
    info = dict(shared_training_landmarks=joined['shared_train_count'],
        boundary_matches=len(matches['pairs_a']), new_birth_landmarks=int(np.sum(joined['birth_indices'] == 4)),
        node_train_tracks=joined['admitted_train'].sum(axis=1).tolist(),
        correlated_factors_from_shared_window=True, calibrated_covariance=False,
        initialization='A initial training PnP endpoint, never optimized/learned/GT')
    try:
        info['initial_heldout_rmse_px'] = birth_heldout_rmse(joined, calibration,
            joined['initial_centers'], joined['initial_rotations'])
    except ValueError as error:
        info.update(initial_heldout_rmse_px=None, initial_heldout_unavailable_reason=str(error))
    if result['accepted']:
        try:
            info['optimized_heldout_rmse_px'] = birth_heldout_rmse(joined, calibration,
                                                                result['centers'], result['rotations'])
        except ValueError as error:
            info.update(optimized_heldout_rmse_px=None, optimized_heldout_unavailable_reason=str(error))
    return result, independent, info, joined


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_case(name, graph_path, output):
    graph = json.loads(graph_path.read_text())
    inputs = graph['inputs']
    stereo_report = json.loads(Path(inputs['stereo_report']).read_text())
    calibration = stereo_report['factory_stereo_calibration']
    trajectory = Path(stereo_report['trajectory'])
    timestamps = base.stereo.load_trajectory(trajectory)[0]  # timestamps only
    selections = pair_windows(len(timestamps))
    session = Path(inputs['session'])
    paths = [graph_path, Path(inputs['stereo_report']), trajectory, Path(inputs['imu_calibration']),
        Path(inputs['vins_spatiotemporal_calibration']), session/'d405_frames.csv',
        session/'external_imu/imu.bin', trajectory.parent/'dataset/frames.csv']
    hashes = {str(p): digest(p) for p in paths}
    mono = base.fusion.camera_epoch_to_monotonic(session/'d405_frames.csv', 'infrared_left', timestamps)
    imu_times, gyro, _, _ = base.fusion.load_calibrated_imu(session/'external_imu/imu.bin', Path(inputs['imu_calibration']))
    config = base.fusion.load_vins_config(Path(inputs['vins_spatiotemporal_calibration']), -.009109323)
    if abs(config['td_s']+.009109323) > 1e-10:
        raise ValueError('formal td mismatch')
    body_from_camera = Rotation.from_matrix(np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])[:3, :3])
    ln, rn, _ = base.stereo.match_trajectory_to_stereo_frames(session/'d405_frames.csv', timestamps,
                                                          trajectory_frame='infrared_left')
    dense = np.concatenate([base.tracking_frames(s) for s in selections])
    left, right = base.stereo.load_selected_prepared_stereo_images(trajectory.parent/'dataset',
        session/'d405_frames.csv', {int(ln[i]) for i in dense}, {int(rn[i]) for i in dense})
    image_hashes = {f'{stream}:{n}': hashlib.sha256(image.tobytes()).hexdigest()
                   for stream, images in [('left', left), ('right', right)] for n, image in images.items()}
    rows, independent_rows, pairs = [], [], []
    for number, selection in enumerate(selections, 1):
        started = time.monotonic()
        local_dense = base.tracking_frames(selection)
        info = dict(pair=number, indices=selection.tolist(), raw_frame_indices=local_dense.tolist())
        try:
            deltas, jacobians = base.gyro_factors(imu_times, gyro, mono[selection], config['td_s'], body_from_camera)
            result, independent, detail, joined = process_pair(
                [left[int(ln[i])] for i in local_dense], [right[int(rn[i])] for i in local_dense],
                calibration, mono[selection], deltas, jacobians)
            part_rows = endpoint_rows(result, selection, timestamps, joined['initial_centers'],
                                      joined['initial_rotations'], number)
            info.update(detail, accepted=result['accepted'], reason=result['reason'])
            for part, row in enumerate(part_rows):
                row['pair_diagnostics'] = detail
                old = independent[part]
                independent_rows.append(dict(window=row['window'], indices=row['indices'],
                    elapsed_s=row['elapsed_s'], accepted=old['accepted'], reason=old['reason'],
                    initial_endpoint_m=old['initial_endpoint_m'],
                    **({'endpoint_m': old['centers'][-1].tolist(), 'diagnostics': old['diagnostics'],
                        'optimized_heldout_rmse_px': old['heldout_rmse_px'],
                        'heldout_unavailable_reason': old.get('heldout_unavailable_reason')} if old['accepted'] else {})))
        except (ValueError, RuntimeError, cv2.error) as error:
            info.update(accepted=False, reason=type(error).__name__, detail=str(error))
            part_rows = [dict(window=2*(number-1)+part+1, joint_pair=number,
                indices=s.tolist(), elapsed_s=(timestamps[s]-timestamps[0]).tolist(),
                accepted=False, reason=info['reason'], detail=str(error),
                correlated_factors_from_shared_window=True) for part, s in enumerate([selection[:5], selection[4:]])]
            independent_rows.extend([{**r, 'reason': 'pair_preparation_failed'} for r in part_rows])
        info['runtime_s'] = time.monotonic()-started
        rows.extend(part_rows)
        pairs.append(info)
        (output/f'{name}.json').write_text(json.dumps(dict(case=name, windows=rows,
            independent_windows=independent_rows, pairs=pairs, external_reference_used=False), indent=2)+'\n')
        print(name, number, info['accepted'], info['reason'], round(info['runtime_s'], 2), flush=True)
    if any(digest(p) != h for p, h in hashes.items()):
        raise ValueError('input changed during controls')
    return dict(case=name, windows=rows, independent_windows=independent_rows, pairs=pairs,
                input_sha256=hashes, decoded_grayscale_frame_sha256=image_hashes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('refuse to overwrite controls')
    sources = [Path(__file__), Path(base.__file__), ROOT/'scripts/join_stereo_windows.py',
        ROOT/'scripts/stereo_window_bundle.py', ROOT/'scripts/prepare_stereo_window_observations.py',
        ROOT/'scripts/align_mast3r_scale_with_stereo.py', ROOT/'scripts/fuse_mast3r_stereo_imu.py',
        ROOT/'.planning/joint_metric_scale_20260927/run_cached_regression.py']
    hashes = {str(p): digest(p) for p in sources}
    cases = list(base.load_module('joint_case_sources', ROOT/'.planning/joint_metric_scale_20260927/run_cached_regression.py').CASES)
    cases += [(f'fresh{i}', f'joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}', '') for i in range(1, 5)]
    if len(cases) != 10 or len({c[0] for c in cases}) != 10:
        raise ValueError('exact ten unique cases required')
    args.output.mkdir(parents=True)
    rows = [run_case(name, ROOT/'reports'/path/'mast3r/graph_fusion_report.json', args.output) for name, path, _ in cases]
    if any(digest(p) != h for p, h in hashes.items()):
        raise ValueError('source changed during controls')
    summary = dict(cases=rows, source_sha256=hashes, external_reference_used=False, production_modified=False,
        correlated_factors_from_shared_window=True, candidate_policy='same fixed5pairs per recording; no fallback or GT selection')
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    independent = [{**r, 'windows': r['independent_windows']} for r in rows]
    (args.output/'independent_summary.json').write_text(json.dumps({**summary, 'cases': independent,
        'correlated_factors_from_shared_window': False}, indent=2)+'\n')


if __name__ == '__main__':
    main()
