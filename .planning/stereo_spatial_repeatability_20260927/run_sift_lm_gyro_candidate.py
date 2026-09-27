"""Frozen ten-case graph regression of refined, gyro-validated SIFT factors.

Recompute all originally accepted SIFT observations, not GT-selected windows.
Retain cached LK, global scales, MASt3R frontend, VINS and all graph parameters.
This is an isolated observation candidate, not a production default change.
"""
import hashlib
import importlib.util
import json
from collections import OrderedDict
from pathlib import Path

from scipy.spatial.transform import Rotation
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def refine_reports(stereo, cached, target):
    graph_path = ROOT / 'reports' / cached / 'mast3r/graph_fusion_report.json'
    graph = json.loads(graph_path.read_text())
    inputs = graph['inputs']
    sources = [inputs['stereo_report']] + inputs['additional_stereo_reports']
    originals = [json.loads(Path(p).read_text()) for p in sources]
    assert all(r['observation_frame'] == 'infrared_left_camera_i' for r in originals)
    assert all(r['factory_stereo_calibration'] == originals[0]['factory_stereo_calibration'] for r in originals)
    trajectory = Path(originals[0]['trajectory'])
    times, positions, quaternions, _ = stereo.load_trajectory(trajectory)
    rotations = Rotation.from_quat(quaternions)
    calibration = dict(originals[0]['factory_stereo_calibration'])
    calibration['left'], calibration['right'] = calibration['left_intrinsics'], calibration['right_intrinsics']
    session = Path(inputs['session'])
    fusion = module('sift_lm_gyro_fusion', ROOT / 'scripts/fuse_mast3r_stereo_imu.py')
    mono = fusion.camera_epoch_to_monotonic(session / 'd405_frames.csv', 'infrared_left', times)
    imu_times, gyro, _, _ = fusion.load_calibrated_imu(session / 'external_imu/imu.bin', Path(inputs['imu_calibration']))
    config = fusion.load_vins_config(Path(inputs['vins_spatiotemporal_calibration']), -0.009109323)
    assert abs(config['td_s'] + 0.009109323) < 1e-10
    body_from_camera = Rotation.from_matrix(np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])[:3, :3])
    left_numbers, right_numbers, _ = stereo.match_trajectory_to_stereo_frames(session / 'd405_frames.csv', times, trajectory_frame='infrared_left')
    edges = [e for r in originals for e in r['observations'] if e.get('accepted') and e.get('method') == 'sift']
    assert all('bidirectional_relative_disagreement' not in e for e in edges), 'Do not silently discard a bidirectional SIFT contract'
    indices = {e[k] for e in edges for k in ('first_index', 'second_index')}
    left, right = stereo.load_selected_prepared_stereo_images(trajectory.parent / 'dataset', session / 'd405_frames.csv',
        {int(left_numbers[i]) for i in indices}, {int(right_numbers[i]) for i in indices})
    target.mkdir(parents=True)
    source_hashes = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}
    disparity_cache, measurements = OrderedDict(), {}
    native_motion = stereo.estimate_motion_from_correspondences

    def refined_motion(*args, **kwargs):
        assert kwargs.get('pnp_rotation_mode', 'free') == 'free'
        return native_motion(*args, **dict(kwargs, refine_pnp=True))

    policy = dict(name='sift_free_LM_with_raw_gyro_rotation_gate', num_disparities=128,
                  min_depth_m=.07, max_depth_m=.6, pnp_refine_enabled=True,
                  gate_limit_deg=5.0, imu_td_s=config['td_s'],
                  rotation_fixed=False, external_reference_used=False,
                  lk_policy='unchanged cached factors', global_scale_policy='unchanged cached scales',
                  selection='all original accepted SIFT factors; no GT or per-case selection',
                  policy_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    rewritten = []
    stereo.estimate_motion_from_correspondences = refined_motion
    try:
        for source, report in zip(sources, originals):
            updated = []
            for edge in report['observations']:
                if not (edge.get('accepted') and edge.get('method') == 'sift'):
                    updated.append(edge)
                    continue
                first, second = edge['first_index'], edge['second_index']
                pair = first, second
                if pair not in measurements:
                    if first not in disparity_cache:
                        disparity_cache[first] = stereo.stereo_disparity(left[int(left_numbers[first])], right[int(right_numbers[first])], 128)
                        if len(disparity_cache) > 24:
                            disparity_cache.popitem(last=False)
                    disparity_cache.move_to_end(first)
                    body_delta = fusion.integrate_gyro(imu_times, gyro, mono[first]+config['td_s'], mono[second]+config['td_s'])
                    camera_delta = body_from_camera.inv() * body_delta * body_from_camera
                    measurements[pair] = stereo.estimate_sift_fallback(
                        left[int(left_numbers[first])], left[int(left_numbers[second])], *disparity_cache[first],
                        positions[first], positions[second], rotations[first], rotations[first]*camera_delta,
                        calibration, .07, .6, 'infrared_left', 'free')
                    if len(measurements) % 50 == 0:
                        print(target.parent.name, 'refined SIFT pairs', len(measurements), flush=True)
                updated.append(dict(edge, **measurements[pair], rotation_gate_reference='raw_calibrated_gyro',
                                    refinement_estimate=measurements[pair]))
            report['observations'] = updated
            report['refinement_policy'] = policy
            path = target / Path(source).name
            path.write_text(json.dumps(report, indent=2)+'\n')
            rewritten.append(str(path.resolve()))
    finally:
        stereo.estimate_motion_from_correspondences = native_motion
    for p, digest in source_hashes.items():
        assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest
    (target / 'refinement_summary.json').write_text(json.dumps(dict(
        policy=policy, original_sift_occurrences=len(edges), unique_pairs=len(measurements),
        accepted=sum(m.get('accepted', False) for m in measurements.values()), source_sha256=source_hashes), indent=2)+'\n')
    return rewritten


def main():
    runner = module('sift_refinement_runner', ROOT / '.planning/stereo_factor_attribution_20260927/validate_sift_and_regress.py')
    runner.validate_reports = refine_reports
    runner.main()


if __name__ == '__main__':
    main()
