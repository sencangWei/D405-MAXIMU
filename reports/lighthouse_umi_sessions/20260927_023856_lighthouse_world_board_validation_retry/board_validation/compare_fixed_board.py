"""Compare frozen Tracker/camera mapping against an independent fixed board.

Only the coordinate-frame SE(3) alignment is fitted. No scale, hand-eye,
world calibration, time offset, or trajectory samples are optimized here.
"""
import csv
import hashlib
import json
import re
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path('/home/robot/ego_vio_humble')
sys.path.insert(0, str(ROOT / 'scripts'))
from calibrate_lighthouse_aprilgrid import load_camera_poses, map_camera_times
from calibrate_lighthouse_umi import clean_poses, interpolate_tracker, load_tracker
from estimate_lighthouse_imu_time_offset import estimate_offset, load_imu

CAPTURE = Path(__file__).resolve().parent.parent
OUT = CAPTURE / 'board_validation'
SESSION = Path('/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260927_023859')
CALIBRATION = ROOT / 'reports/lighthouse_extrinsic_time_recheck_20260911_round4/independent_calibration/lighthouse_d405_aprilgrid_calibration.json'


def read_record(path, name='WM0', operation='POSE'):
    rows = []
    with path.open() as stream:
        for line in stream:
            f = line.split()
            if len(f) >= 10 and f[1:3] == [name, operation]:
                rows.append([float(f[0]), *map(float, f[3:10])])
    return np.asarray(rows)


def clock_offset(original):
    matches = {}
    duplicates = set()
    for row in original:
        key = tuple(row[1:])
        if key in matches:
            duplicates.add(key)
        matches[key] = row[0]
    differences = []
    with (CAPTURE / 'tracker.csv').open() as stream:
        for row in csv.DictReader(stream):
            key = tuple(round(float(row[k]), 6) for k in ['px_m', 'py_m', 'pz_m', 'qw', 'qx', 'qy', 'qz'])
            if key in matches and key not in duplicates:
                differences.append(int(row['host_monotonic_ns']) * 1e-9 - matches[key])
    if len(differences) < 100:
        raise ValueError(f'Only {len(differences)} unambiguous source pose/CSV clock matches')
    offset = float(np.median(differences))
    deviation_us = float(np.percentile(np.abs(np.asarray(differences) - offset), 99) * 1e6)
    if deviation_us > 1000:
        raise ValueError(f'Record/host clock mapping inconsistent: P99 {deviation_us} us')
    return offset, {'matched_callbacks': len(differences), 'record_to_host_offset_s': offset,
                    'absolute_residual_p99_us': deviation_us}


def align(x, y):
    xc, yc = x.mean(axis=0), y.mean(axis=0)
    u, singular, vt = np.linalg.svd((x-xc).T @ (y-yc))
    if singular[1] < 1e-8:
        raise ValueError('Insufficient spatial excitation for frame alignment')
    r = vt.T @ np.diag([1, 1, np.linalg.det(vt.T @ u.T)]) @ u.T
    return r, yc-r@xc


def stats(values):
    return {'mean': float(np.mean(values)), 'median': float(np.median(values)),
            'rmse': float(np.sqrt(np.mean(values**2))), 'p95': float(np.percentile(values,95)),
            'max': float(np.max(values)), 'fraction_below_10': float(np.mean(values < 10))}


def main():
    calibration = json.loads(CALIBRATION.read_text())
    if calibration['result'] != 'PASS_CANDIDATE' or calibration['slam_supervision'] is not False:
        raise ValueError('Invalid frozen external calibration provenance')
    external = np.asarray(calibration['tracker_T_d405_left_camera'])
    offset, clock = clock_offset(read_record(CAPTURE / 'lighthouse_raw.rec'))
    ct, cp, cq = load_camera_poses(OUT / 'aprilgrid_camera_poses.csv')
    host_times, camera_clock = map_camera_times(ct, 'host_monotonic', SESSION / 'd405_frames.csv')
    query_times = host_times + calibration['tracker_query_offset_ms'] / 1000
    live_t, live_p, live_q, serial = load_tracker(CAPTURE / 'tracker.csv','host_monotonic')
    if serial != calibration['tracker_serial']:
        raise ValueError('Tracker serial mismatch')
    trajectories = {'live_formal': (live_t, live_p, live_q)}
    for world in ['formal','joint_candidate']:
        path = OUT / f'replay/board_{world}_fourway/validation.rec'
        a = read_record(path)
        trajectories[world+'_fourway'] = clean_poses(a[:,0]+offset,a[:,1:4],a[:,[5,6,7,4]],path)
    samples, masks = {}, []
    for name, (t,p,q) in trajectories.items():
        poses, valid = interpolate_tracker(query_times,t,p,q,0.030)
        samples[name] = np.full((len(query_times),4,4),np.nan)
        samples[name][valid] = poses @ external
        masks.append(valid)
    common = np.logical_and.reduce(masks)
    if common.sum() < 100:
        raise ValueError('Insufficient common board/reference samples')
    elapsed = host_times[common]-host_times[common][0]
    truth, truth_rot = cp[common], Rotation.from_quat(cq[common]).as_matrix()
    first = elapsed <= (elapsed[0]+elapsed[-1])/2
    report = {'result': 'DIAGNOSTIC_ONLY', 'board_samples': len(ct), 'common_samples': int(common.sum()),
              'clock_mapping': clock, 'camera_clock_mapping': camera_clock,
              'frozen_handeye': str(CALIBRATION),
              'frozen_handeye_sha256': hashlib.sha256(CALIBRATION.read_bytes()).hexdigest(),
              'tracker_query_offset_ms': calibration['tracker_query_offset_ms'],
              'scale_fit': False, 'external_fit': False, 'time_offset_fit': False,
              'alignment': 'SE3 on all common samples; first-half alignment also evaluated on second half',
              'limits': ['Single independent capture', 'Frozen hand-eye calibration has material uncertainty',
                         'Board metric poses depend on measured print size and camera calibration',
                         'No absolute surveyed Lighthouse-position accuracy claim'], 'modes': {}}
    report['input_sha256'] = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [
        CAPTURE/'lighthouse_raw.rec', CAPTURE/'tracker.csv', OUT/'aprilgrid_camera_poses.csv',
        SESSION/'d405_frames.csv', ROOT/'config/aprilgrid_6x6_35mm.yaml']}
    imu_t, imu_gyro = load_imu(SESSION / 'external_imu/imu.bin')
    report['independent_gyro_timing'] = {}
    for name, (t, _, q) in {**trajectories, 'board_camera': (host_times,cp,cq)}.items():
        timing = estimate_offset(imu_t,imu_gyro,t,q)
        timing['single_capture_signal_gate_pass'] = bool(
            timing['correlation'] >= 0.8 and timing['tracker_speed_clipped_ratio'] <= 0.005)
        report['independent_gyro_timing'][name] = timing
    plt.figure(figsize=(10,4))
    for name, poses in samples.items():
        poses = poses[common]
        p = poses[:,:3,3]
        r, tr = align(p,truth)
        errors = np.linalg.norm(p@r.T+tr-truth,axis=1)*1000
        rot_errors = np.rad2deg(Rotation.from_matrix(truth_rot.transpose(0,2,1)@r@poses[:,:3,:3]).magnitude())
        hr, ht = align(p[first],truth[first])
        heldout = np.linalg.norm(p[~first]@hr.T+ht-truth[~first],axis=1)*1000
        report['modes'][name] = {'position_mm':stats(errors), 'orientation_deg':stats(rot_errors),
                               'first_half_alignment_second_half_position_mm':stats(heldout)}
        peak = int(np.argmax(errors))
        report['modes'][name]['peak_elapsed_s'] = float(elapsed[peak])
        plt.plot(elapsed,errors,label=name)
    tracker_timing = report['independent_gyro_timing']['joint_candidate_fourway']
    camera_timing = report['independent_gyro_timing']['board_camera']
    if tracker_timing['single_capture_signal_gate_pass'] and camera_timing['single_capture_signal_gate_pass']:
        relative_ms = tracker_timing['tracker_query_offset_ms']-camera_timing['tracker_query_offset_ms']
        t,p,q = trajectories['joint_candidate_fourway']
        alternative, valid = interpolate_tracker(host_times+relative_ms/1000,t,p,q,0.030)
        all_poses = np.full((len(ct),4,4),np.nan)
        all_poses[valid] = alternative@external
        shared = valid & common
        candidate_p = all_poses[shared,:3,3]
        r,tr = align(candidate_p,cp[shared])
        error = np.linalg.norm(candidate_p@r.T+tr-cp[shared],axis=1)*1000
        report['independent_timing_sensitivity'] = {
            'result': 'SINGLE_CAPTURE_DIAGNOSTIC_ONLY', 'tracker_query_offset_ms': relative_ms,
            'method': 'Tracker-to-IMU angular-speed offset minus board-camera-to-IMU offset',
            'samples': int(shared.sum()), 'position_mm': stats(error),
            'external_fit': False, 'position_error_used_to_select_offset': False}
    raw_path = OUT / 'replay/board_joint_candidate_fourway/validation.rec'
    raw = read_record(raw_path,'WM0-raw-obs','EXTERNAL_POSE')
    runtime = (raw_path.parent/'runtime.json').read_text()
    floor_match = re.search(r'"floor-offset":"([^"]+)"',runtime)
    if floor_match is None:
        raise ValueError('Missing runtime floor-offset for raw/final frame conversion')
    floor_offset = float(floor_match.group(1))
    # libsurvive records raw EXTERNAL_POSE before floor normalization; the
    # final POSE hook receives Z minus floor-offset. Use that same conversion.
    raw[:,3] -= floor_offset
    rt,rp,rq = clean_poses(raw[:,0]+offset,raw[:,1:4],raw[:,[5,6,7,4]],raw_path)
    raw_poses, raw_valid = interpolate_tracker(query_times,rt,rp,rq,0.030)
    all_raw = np.full((len(ct),4,4),np.nan)
    all_raw[raw_valid] = raw_poses@external
    shared_raw = raw_valid&common
    raw_checks = {}
    shared_r,shared_tr = align(samples['joint_candidate_fourway'][shared_raw,:3,3],cp[shared_raw])
    for name, full_poses in [('raw_optical',all_raw),('filtered',samples['joint_candidate_fourway'])]:
        p = full_poses[shared_raw,:3,3]
        error = np.linalg.norm(p@shared_r.T+shared_tr-cp[shared_raw],axis=1)*1000
        peak = int(np.argmax(error))
        raw_checks[name] = {'position_mm': stats(error),
                            'peak_elapsed_s':float(host_times[shared_raw][peak]-host_times[common][0])}
    report['raw_vs_filtered_diagnostic'] = {'samples':int(shared_raw.sum()),'modes':raw_checks,
                                           'raw_to_final_z_subtract_m':floor_offset,
                                           'alignment': 'same SE3 fitted to filtered samples for both streams',
                                           'tracker_query_offset_ms':calibration['tracker_query_offset_ms']}
    peak_time = host_times[common][0]+report['modes']['joint_candidate_fourway']['peak_elapsed_s']
    raw_index = int(np.searchsorted(rt,peak_time+calibration['tracker_query_offset_ms']/1000))
    if 0 < raw_index < len(rt):
        report['candidate_peak_optical_bracket'] = {
            'peak_elapsed_s':report['modes']['joint_candidate_fourway']['peak_elapsed_s'],
            'previous_optical_elapsed_s':float(rt[raw_index-1]-host_times[common][0]),
            'next_optical_elapsed_s':float(rt[raw_index]-host_times[common][0]),
            'optical_interval_ms':float((rt[raw_index]-rt[raw_index-1])*1000),
            'query_age_since_last_optical_ms':float((peak_time+calibration['tracker_query_offset_ms']/1000-rt[raw_index-1])*1000)}
        pending_axes, skipped = set(), []
        with (OUT/'replay/board_joint_candidate_no_gate/validation.rec').open() as stream:
            for line in stream:
                f = line.split()
                if len(f) >= 7 and f[1:3] == ['WM0','RA']:
                    pending_axes.add((int(f[6]),int(f[4])))
                elif len(f) >= 10 and f[1:3] == ['WM0-raw-obs','EXTERNAL_POSE']:
                    event_time = float(f[0])+offset
                    if rt[raw_index-1] < event_time < rt[raw_index]:
                        skipped.append({'elapsed_s':float(event_time-host_times[common][0]),
                                        'station_axis_groups':len(pending_axes),
                                        'station_count':len({a[0] for a in pending_axes})})
                    pending_axes.clear()
        report['candidate_peak_optical_bracket']['ungated_solutions_inside_gap'] = skipped
    plt.axhline(10,color='black',ls='--',label='10 mm')
    plt.xlabel('Elapsed camera time (s)'); plt.ylabel('Board position residual (mm)')
    plt.legend(); plt.grid(alpha=.3); plt.tight_layout()
    plt.savefig(OUT/'fixed_board_comparison.png',dpi=160)
    (OUT/'fixed_board_comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
