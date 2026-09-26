"""Read-only gate/prediction comparison. Never fits a local warp or time offset."""
import csv
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

import compare_fixed_board as board


def raw_support(path):
    rows, axes, residuals = [], set(), []
    for line in path.open():
        f = line.split()
        if len(f) >= 7 and f[1:3] == ['WM0', 'RA']:
            axes.add((int(f[6]), int(f[4])))
            residuals.append(float(f[5]))
        elif len(f) >= 10 and f[1:3] == ['WM0-raw-obs', 'EXTERNAL_POSE']:
            rows.append([float(f[0]), len(axes), len(residuals),
                         np.sqrt(np.mean(np.square(residuals))) if residuals else np.nan])
            axes, residuals = set(), []
    return np.asarray(rows)


def main():
    calibration = json.loads(board.CALIBRATION.read_text())
    external = np.asarray(calibration['tracker_T_d405_left_camera'])
    offset, clock = board.clock_offset(board.read_record(board.CAPTURE/'lighthouse_raw.rec'))
    ct, cp, cq = board.load_camera_poses(board.OUT/'aprilgrid_camera_poses.csv')
    host, _ = board.map_camera_times(ct, 'host_monotonic', board.SESSION/'d405_frames.csv')
    query = host + calibration['tracker_query_offset_ms']/1000
    samples, masks, optical = {}, [], {}
    paths = {gate:board.OUT/f'replay/board_joint_candidate_{gate}/validation.rec'
             for gate in ['no_gate','fourway']}
    candidate = board.ROOT/'reports/lighthouse_prediction_audit_20260927/no_motion_deskew/board_joint_candidate_fourway/validation.rec'
    if candidate.exists():
        paths['no_motion_deskew'] = candidate
    delayed = board.ROOT/'reports/lighthouse_prediction_audit_20260927/delayed_measurement/board_joint_candidate_fourway/validation.rec'
    if delayed.exists():
        paths['delayed_measurement'] = delayed
    for gate,path in paths.items():
        poses = board.read_record(path)
        t, p, q = board.clean_poses(poses[:,0]+offset, poses[:,1:4], poses[:,[5,6,7,4]],path)
        interpolated, valid = board.interpolate_tracker(query,t,p,q,.030)
        full = np.full((len(ct),4,4),np.nan)
        full[valid] = interpolated@external
        samples[gate] = full
        masks.append(valid)
        optical[gate] = raw_support(path)
        optical[gate][:,0] += offset
    common = np.logical_and.reduce(masks)
    # One shared alignment: no independent frame fit favoring either mode.
    r, tr = board.align(samples['fourway'][common,:3,3],cp[common])
    errors = {name:np.linalg.norm(poses[common,:3,3]@r.T+tr-cp[common],axis=1)*1000
              for name,poses in samples.items()}
    elapsed = host[common]-host[common][0]
    ages = {}
    report = {'result':'DIAGNOSTIC_ONLY','common_samples':int(common.sum()),'board_samples':len(ct),
              'elapsed_origin_host_monotonic_s':float(host[common][0]),
              'clock_mapping':clock,'tracker_query_offset_ms':calibration['tracker_query_offset_ms'],
              'alignment':'Same SE3 fitted to fourway, applied to both modes',
              'scale_fit':False,'external_fit':False,'time_offset_fit':False,'modes':{}}
    for gate,a in optical.items():
        index = np.searchsorted(a[:,0],query[common],side='right')-1
        if np.any(index < 0):
            raise ValueError('Query precedes first optical solve')
        age = (query[common]-a[index,0])*1000
        ages[gate] = age
        groups = a[index,1]
        report['modes'][gate] = {'position_mm':board.stats(errors[gate]),
                               'peak_elapsed_s':float(elapsed[np.argmax(errors[gate])]),
                               'optical_age_ms':board.stats(age),'conditioned':{}}
        for label,mask in [('age_le_20ms',age<=20),('age_gt_20ms',age>20),
                           ('last_solve_four_axes',groups==4),('last_solve_partial_axes',groups<4)]:
            if mask.any():
                report['modes'][gate]['conditioned'][label] = {'samples':int(mask.sum()),
                                                              'position_mm':board.stats(errors[gate][mask])}
    # Exact decomposition at the same samples/frame alignment: separate
    # Tracker-origin position from orientation-dependent camera lever arm.
    truth_track_rot = Rotation.from_quat(cq[common]).as_matrix()@external[:3,:3].T
    for gate,full in samples.items():
        camera=full[common]
        estimated_track_rot=camera[:,:3,:3]@external[:3,:3].T
        lever_error=np.einsum('nij,j->ni',r@estimated_track_rot-truth_track_rot,external[:3,3])*1000
        camera_error=(camera[:,:3,3]@r.T+tr-cp[common])*1000
        origin_error=camera_error-lever_error
        peak=int(np.argmax(errors[gate]))
        report['modes'][gate]['origin_lever_decomposition']={
            'lever_length_mm':float(np.linalg.norm(external[:3,3])*1000),
            'orientation_lever_component_mm':board.stats(np.linalg.norm(lever_error,axis=1)),
            'tracker_origin_residual_mm':board.stats(np.linalg.norm(origin_error,axis=1)),
            'at_camera_peak':{'elapsed_s':float(elapsed[peak]),
                'camera_residual_vector_mm':camera_error[peak].tolist(),
                'tracker_origin_residual_vector_mm':origin_error[peak].tolist(),
                'orientation_lever_vector_mm':lever_error[peak].tolist()}}
    report['multi_record_motion_deskew_ab'] = {}
    baseline = board.ROOT/'reports/lighthouse_gss_coverage_20260926_valid_v1/three_new_validation_v2'
    candidate_root = board.ROOT/'reports/lighthouse_prediction_audit_20260927/no_motion_deskew'
    for take in ['gP8kSJ','ucf7GS','rAt98l','board']:
        base = (board.OUT/'replay' if take=='board' else baseline)/f'{take}_joint_candidate_fourway/audit.json'
        test = candidate_root/f'{take}_joint_candidate_fourway/audit.json'
        if not test.exists():
            continue
        summary = {}
        for name,path in [('baseline',base),('no_motion_deskew',test)]:
            data = json.loads(path.read_text())
            summary[name] = {'raw_solves':data['event_counts']['EXTERNAL_POSE'],
                             'final_poses':data['event_counts']['POSE'],
                             'raw_jump_flags':sum(j['stream']=='WM0-raw-obs' for j in data['recorded_pose_jumps']),
                             'final_jump_flags':sum(j['stream']=='WM0' for j in data['recorded_pose_jumps']),
                             'invalid_values':data['invalid_values']}
        report['multi_record_motion_deskew_ab'][take] = summary
    peak = int(np.argmax(errors['fourway']))
    source=paths['fourway']
    supports,pending,points=[],{},None
    for line in source.open():
        f=line.split()
        if len(f)>=3 and f[1:3]==['WM0','CONFIG']:
            points=np.asarray(json.loads(line.split(' CONFIG ',1)[1])['lighthouse_config']['modelPoints'])
        elif len(f)>=7 and f[1:3]==['WM0','RA']:
            pending.setdefault((int(f[6]),int(f[4])),set()).add(int(f[3]))
        elif len(f)>=10 and f[1:3]==['WM0-raw-obs','EXTERNAL_POSE']:
            supports.append((float(f[0])+offset,pending))
            pending={}
    idx=int(np.searchsorted([x[0] for x in supports],query[common][peak],side='right')-1)
    if points is None or idx<0:
        raise ValueError('Missing raw sensor geometry at peak')
    coverage={}
    for key,sensors in supports[idx][1].items():
        cloud=points[sorted(sensors)]
        singular=np.linalg.svd(cloud-cloud.mean(axis=0),compute_uv=False)*1000
        coverage[str(key)]={'sensor_ids':sorted(sensors),'count':len(sensors),
                            'centered_point_singular_values_mm':singular.tolist()}
    report['peak_raw_sensor_coverage']=coverage
    instrumented = board.ROOT/'reports/lighthouse_prediction_audit_20260927/timing_instrumented/board_joint_candidate_fourway/validation.rec'
    if instrumented.exists():
        rows=[]
        for line in instrumented.open():
            f=line.split()
            if len(f)==52 and f[1:3]==["WM0'",'FULL_COVARIANCE']:
                rows.append((float(f[0])+offset,np.asarray(f[3:],dtype=float).reshape(7,7)))
        native_times=np.asarray([x[0] for x in rows])
        last=int(np.searchsorted(native_times,query[common][peak],side='right')-1)
        covariance=rows[last][1][:3,:3]
        values,vectors=np.linalg.eigh((covariance+covariance.T)/2)
        weak_direction=r@vectors[:,-1]
        error=np.asarray(report['modes']['fourway']['origin_lever_decomposition']['at_camera_peak']['tracker_origin_residual_vector_mm'])
        report['peak_optical_geometry']={
            'last_optical_age_ms':float((query[common][peak]-native_times[last])*1000),
            'raw_imu_origin_position_std_principal_mm':(np.sqrt(np.maximum(values,0))*1000).tolist(),
            'filtered_origin_error_projected_on_raw_weak_direction_mm':float(abs(error@weak_direction)),
            'filtered_origin_error_norm_mm':float(np.linalg.norm(error)),
            'limits':'Raw IMU-origin position covariance, not filtered covariance or a precision bound; head lever covariance not propagated'}
    local = np.abs(elapsed-elapsed[peak])<.20
    fields = ['elapsed_s','fourway_error_mm','no_gate_error_mm','fourway_optical_age_ms','no_gate_optical_age_ms']
    with (board.OUT/'prediction_gap_samples.csv').open('w') as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for i in np.flatnonzero(common):
            j = int(np.sum(common[:i]))
            writer.writerow([elapsed[j],errors['fourway'][j],errors['no_gate'][j],ages['fourway'][j],ages['no_gate'][j]])
    report['peak_neighborhood'] = [dict(zip(fields,[float(x) for x in row])) for row in
                                 zip(elapsed[local],errors['fourway'][local],errors['no_gate'][local],
                                     ages['fourway'][local],ages['no_gate'][local])]
    (board.OUT/'prediction_gap_diagnosis.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
