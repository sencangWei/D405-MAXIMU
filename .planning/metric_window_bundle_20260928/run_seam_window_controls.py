"""Fixed seam-born pixel replenishment; experimental UMI-only controls."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT/'scripts'))
import run_joint_window_controls as previous
from prepare_seam_stereo_observations import track_seam_stereo_window


def replenish(joined, seam):
    """Add distinct seam-born pixels in the A INITIAL training-PnP gauge."""
    observations = seam['observations'][::5]
    valid = seam['valid'][::5]
    if observations.shape[0] != 9 or valid.shape != observations.shape[:2]:
        raise ValueError('nine sampled seam nodes required')
    existing = joined['observations'][4, joined['valid'][4], :2]
    # Exact subpixel duplicate check supplements the detector's raster mask.
    points = observations[4, :, :2]
    duplicate = np.any(np.linalg.norm(points[:, None]-existing[None], axis=2) < 7., axis=1)
    keep = ~duplicate & valid[4] & valid[:4].any(axis=0) & valid[5:].any(axis=0)
    selected = np.flatnonzero(keep)
    ids = np.asarray(seam['source_indices']['feature_indices'], dtype=int)[selected]
    local_points = seam['initial_points'][selected]
    global_points = joined['initial_rotations'][4].apply(local_points)+joined['initial_centers'][4]
    if not np.isfinite(global_points).all() or np.any(global_points[:, 2] <= 0):
        raise ValueError('invalid seam birth depth in A gauge')
    added_holdout = ids % 5 == 0
    # Only geometry required by this solve; old merge mappings/masks have a
    # different point count and must not leak into downstream diagnostics.
    data = {key: joined[key] for key in ('initial_centers', 'initial_rotations')}
    for key, supplement in [('observations', observations[:, selected]), ('valid', valid[:, selected])]:
        data[key] = np.concatenate((joined[key], supplement), axis=1)
    for key, supplement in [('initial_points', global_points), ('heldout', added_holdout),
            ('birth_indices', np.full(len(selected), 4, dtype=int)), ('birth_points', local_points)]:
        data[key] = np.concatenate((joined[key], supplement), axis=0)
    data['train'] = ~data['heldout']
    return data, dict(seam_source_candidates=int(len(points)),
        excluded_near_existing=int(duplicate.sum()), seam_born_added=int(len(selected)),
        seam_born_train=int((~added_holdout).sum()), seam_born_heldout=int(added_holdout.sum()),
        duplicate_exclusion_px=7., seam_tracker_exclusion=seam['exclusion'],
        seam_source_identity=seam['source_indices'])


def process_pair(left, right, calibration, times, deltas, jacobians):
    old_result, independent, info, joined = previous.process_pair(
        left, right, calibration, times, deltas, jacobians)
    info = {**info, 'previous_joint_accepted': old_result['accepted'],
            'previous_joint_reason': old_result['reason'], 'supplementary_family': 'fixed_seam_birth_bidirectional_LK'}
    for key in ('initial_heldout_rmse_px', 'optimized_heldout_rmse_px',
                'initial_heldout_unavailable_reason', 'optimized_heldout_unavailable_reason'):
        if key in info:
            info['previous_'+key] = info.pop(key)
    # No joint-preparation rescue and no primary raw-tracking gate relaxation.
    if 'observations' not in joined:
        return old_result, independent, info, joined
    try:
        seam = track_seam_stereo_window(left, right, calibration,
            excluded_left_points=joined['observations'][4, joined['valid'][4], :2])
        if not seam['accepted']:
            return dict(accepted=False, reason='seam_observations_refused', detail=seam['reason']), independent, info, joined
        supplemented, detail = replenish(joined, seam)
        info.update(detail)
        centers, rotations, admission = previous.base.visual_initialization(
            supplemented, calibration, supplemented['train'])
        supplemented.update(initial_centers=centers, initial_rotations=rotations,
                            admitted_train=admission & supplemented['train'][None, :])
        shared = (admission[:4].any(axis=0) & admission[4] & admission[5:].any(axis=0)
                  & supplemented['train'])
        info.update(crosswindow_support_count=int(shared.sum()),
                    new_support_rank=int(np.linalg.matrix_rank(
                        supplemented['initial_points'][shared]-supplemented['initial_points'][shared].mean(axis=0))) if shared.any() else 0,
                    node_train_tracks=supplemented['admitted_train'].sum(axis=1).tolist(),
                    calibrated_covariance=False, correlated_factors_from_shared_window=True,
                    seam_birth_gauge='A INITIAL train-only PnP seam pose, before any optimized pose',
                    initialization='combined train-only PnP; no withheld/learned/GT poses')
        if not previous.base.node_geometry_supported(supplemented['initial_points'][shared]):
            result = dict(accepted=False, reason='insufficient_shared_training_geometry')
        else:
            result = previous.solve(supplemented, calibration, times, deltas, jacobians)
    except (ValueError, RuntimeError, cv2.error) as error:
        return dict(accepted=False, reason='seam_joint_preparation_or_solve_failed', detail=str(error)), independent, info, joined
    try:
        info['initial_heldout_rmse_px'] = previous.birth_heldout_rmse(
            supplemented, calibration, supplemented['initial_centers'], supplemented['initial_rotations'])
    except ValueError as error:
        info.update(initial_heldout_rmse_px=None, initial_heldout_unavailable_reason=str(error))
    if result['accepted']:
        try:
            info['optimized_heldout_rmse_px'] = previous.birth_heldout_rmse(
                supplemented, calibration, result['centers'], result['rotations'])
        except ValueError as error:
            info.update(optimized_heldout_rmse_px=None, optimized_heldout_unavailable_reason=str(error))
    return result, independent, info, supplemented


def run_case(name, graph_path, output):
    graph = json.loads(graph_path.read_text())
    inputs = graph['inputs']
    report = json.loads(Path(inputs['stereo_report']).read_text())
    calibration = report['factory_stereo_calibration']
    trajectory = Path(report['trajectory'])
    timestamps = previous.base.stereo.load_trajectory(trajectory)[0]  # timestamps only
    selections = previous.pair_windows(len(timestamps))
    session = Path(inputs['session'])
    paths = [graph_path, Path(inputs['stereo_report']), trajectory, Path(inputs['imu_calibration']),
        Path(inputs['vins_spatiotemporal_calibration']), session/'d405_frames.csv',
        session/'external_imu/imu.bin', trajectory.parent/'dataset/frames.csv']
    hashes = {str(p): previous.digest(p) for p in paths}
    mono = previous.base.fusion.camera_epoch_to_monotonic(session/'d405_frames.csv', 'infrared_left', timestamps)
    imu_times, gyro, _, _ = previous.base.fusion.load_calibrated_imu(session/'external_imu/imu.bin', Path(inputs['imu_calibration']))
    config = previous.base.fusion.load_vins_config(Path(inputs['vins_spatiotemporal_calibration']), -.009109323)
    if abs(config['td_s']+.009109323) > 1e-10:
        raise ValueError('formal td mismatch')
    rotation = previous.base.Rotation.from_matrix(np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])[:3, :3])
    ln, rn, _ = previous.base.stereo.match_trajectory_to_stereo_frames(session/'d405_frames.csv', timestamps,
                                                                    trajectory_frame='infrared_left')
    dense = np.concatenate([previous.base.tracking_frames(s) for s in selections])
    left, right = previous.base.stereo.load_selected_prepared_stereo_images(trajectory.parent/'dataset',
        session/'d405_frames.csv', {int(ln[i]) for i in dense}, {int(rn[i]) for i in dense})
    image_hashes = {f'{stream}:{n}': hashlib.sha256(image.tobytes()).hexdigest()
                   for stream, images in [('left', left), ('right', right)] for n, image in images.items()}
    rows, independent_rows, pairs = [], [], []
    for number, selection in enumerate(selections, 1):
        started = time.monotonic()
        local_dense = previous.base.tracking_frames(selection)
        info = dict(pair=number, indices=selection.tolist(), raw_frame_indices=local_dense.tolist())
        try:
            deltas, jacobians = previous.base.gyro_factors(imu_times, gyro, mono[selection], config['td_s'], rotation)
            result, independent, detail, joined = process_pair(
                [left[int(ln[i])] for i in local_dense], [right[int(rn[i])] for i in local_dense],
                calibration, mono[selection], deltas, jacobians)
            part_rows = previous.endpoint_rows(result, selection, timestamps, joined['initial_centers'],
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
    if any(previous.digest(p) != h for p, h in hashes.items()):
        raise ValueError('input changed during controls')
    return dict(case=name, windows=rows, independent_windows=independent_rows, pairs=pairs,
                input_sha256=hashes, decoded_grayscale_frame_sha256=image_hashes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('refuse to overwrite controls')
    registry = ROOT/'.planning/joint_metric_scale_20260927/run_cached_regression.py'
    sources = [Path(__file__), Path(previous.__file__), Path(previous.base.__file__), registry,
        ROOT/'scripts/join_stereo_windows.py', ROOT/'scripts/prepare_seam_stereo_observations.py',
        ROOT/'scripts/stereo_window_bundle.py', ROOT/'scripts/prepare_stereo_window_observations.py',
        ROOT/'scripts/align_mast3r_scale_with_stereo.py', ROOT/'scripts/fuse_mast3r_stereo_imu.py']
    hashes = {str(p): previous.digest(p) for p in sources}
    cases = list(previous.base.load_module('seam_case_sources', registry).CASES)
    cases += [(f'fresh{i}', f'joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}', '') for i in range(1, 5)]
    if len(cases) != 10 or len({c[0] for c in cases}) != 10:
        raise ValueError('exact ten unique cases required')
    args.output.mkdir(parents=True)
    rows = [run_case(name, ROOT/'reports'/path/'mast3r/graph_fusion_report.json', args.output) for name, path, _ in cases]
    if any(previous.digest(p) != h for p, h in hashes.items()):
        raise ValueError('source changed during controls')
    summary = dict(cases=rows, source_sha256=hashes, external_reference_used=False, production_modified=False,
        correlated_factors_from_shared_window=True, candidate_policy='fixed seam birth for ALL five pairs on ALL ten; no fallback or GT selection')
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    independent = [{**r, 'windows': r['independent_windows']} for r in rows]
    (args.output/'independent_summary.json').write_text(json.dumps({**summary, 'cases': independent,
        'correlated_factors_from_shared_window': False}, indent=2)+'\n')


if __name__ == '__main__':
    main()
