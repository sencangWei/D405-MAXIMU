"""Frozen mapping, shared-frame comparison; no trajectory correction or GT tuning."""
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path('/home/robot/ego_vio_humble')
OUT = Path(__file__).resolve().parent
SOURCE = ROOT / 'reports/lighthouse_umi_sessions/20260927_023856_lighthouse_world_board_validation_retry/board_validation/compare_fixed_board.py'
spec = importlib.util.spec_from_file_location('board', SOURCE)
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
board.CAPTURE = OUT.parent
board.OUT = OUT
board.SESSION = Path('/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260927_040620')


def main():
    calibration = json.loads(board.CALIBRATION.read_text())
    external = np.asarray(calibration['tracker_T_d405_left_camera'])
    offset, _ = board.clock_offset(board.read_record(board.CAPTURE / 'lighthouse_raw.rec'))
    ct, truth, cq = board.load_camera_poses(OUT / 'aprilgrid_camera_poses.csv')
    host, _ = board.map_camera_times(ct, 'host_monotonic', board.SESSION / 'd405_frames.csv')
    query = host + calibration['tracker_query_offset_ms'] / 1000
    samples, masks, optical = {}, [], {}
    for gate in ['fourway', 'no_gate']:
        path = OUT / f'replay/board_joint_candidate_{gate}/validation.rec'
        rows = board.read_record(path)
        t, p, q = board.clean_poses(rows[:, 0] + offset, rows[:, 1:4], rows[:, [5, 6, 7, 4]], path)
        interpolated, valid = board.interpolate_tracker(query, t, p, q, .030)
        full = np.full((len(ct), 4, 4), np.nan)
        full[valid] = interpolated @ external
        samples[gate] = full
        masks.append(valid)
        pending, solves = set(), []
        for line in path.open():
            f = line.split()
            if len(f) >= 7 and f[1:3] == ['WM0', 'RA']:
                pending.add((int(f[6]), int(f[4])))
            elif len(f) >= 10 and f[1:3] == ['WM0-raw-obs', 'EXTERNAL_POSE']:
                solves.append([float(f[0]) + offset, len(pending)])
                pending = set()
        optical[gate] = np.asarray(solves)
    common = np.logical_and.reduce(masks)
    r, tr = board.align(samples['fourway'][common, :3, 3], truth[common])
    elapsed = host[common] - host[0]
    rotations = Rotation.from_quat(cq).as_matrix()
    relative = rotations[0].T @ rotations[common]
    angle = np.rad2deg(Rotation.from_matrix(relative).magnitude())
    report = {'result': 'DIAGNOSTIC_ONLY', 'samples': int(common.sum()),
              'total_camera_samples': len(ct), 'elapsed_origin': 'first camera frame',
              'alignment': 'Same SE3 fitted to fourway and applied to no_gate',
              'external_fit': False, 'time_fit': False, 'scale_fit': False,
              'supervision': False, 'modes': {}}
    for gate, full in samples.items():
        camera = full[common]
        error_vector = (camera[:, :3, 3] @ r.T + tr - truth[common]) * 1000
        error = np.linalg.norm(error_vector, axis=1)
        track_rot = camera[:, :3, :3] @ external[:3, :3].T
        true_track_rot = rotations[common] @ external[:3, :3].T
        lever = np.einsum('nij,j->ni', r @ track_rot - true_track_rot, external[:3, 3]) * 1000
        a = optical[gate]
        index = np.searchsorted(a[:, 0], query[common], side='right') - 1
        if np.any(index < 0):
            raise ValueError('Query before first optical solve')
        age = (query[common] - a[index, 0]) * 1000
        peak = int(np.argmax(error))
        result = {'position_mm': board.stats(error), 'peak_camera_elapsed_s': float(elapsed[peak]),
                  'peak_optical_age_ms': float(age[peak]),
                  'peak_camera_angle_from_initial_deg': float(angle[peak]),
                  'peak_last_optical_axis_groups': int(a[index[peak], 1]),
                  'peak_origin_residual_mm': float(np.linalg.norm(error_vector[peak] - lever[peak])),
                  'peak_orientation_lever_component_mm': float(np.linalg.norm(lever[peak])),
                  'subsets': {}}
        for label, mask in [('optical_age_le_20ms', age <= 20), ('optical_age_gt_20ms', age > 20),
                            ('angle_le_10deg', angle <= 10), ('angle_gt_10deg', angle > 10)]:
            if mask.any():
                result['subsets'][label] = {'samples': int(mask.sum()), 'position_mm': board.stats(error[mask])}
        report['modes'][gate] = result
    report['fourway_max_10mm_acceptance'] = (
        'PASS' if report['modes']['fourway']['position_mm']['max'] <= 10 else 'FAIL')
    (OUT / 'orientation_control_diagnosis.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
