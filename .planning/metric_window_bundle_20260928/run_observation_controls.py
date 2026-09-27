"""Frozen, time-stratified pixel controls; external reference is never read."""
import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import align_mast3r_scale_with_stereo as stereo
import fuse_mast3r_stereo_imu as fusion
from prepare_stereo_window_observations import track_stereo_window
from stereo_window_bundle import solve_stereo_window


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def windows(count):
    if count < 31:
        raise ValueError('recording too short for fixed five-window controls')
    selections = [np.arange(int(round(f * (count-1)))-10,
                      int(round(f * (count-1)))+11, 5)
            for f in (.1, .3, .5, .7, .9)]
    if any(np.any(row<0) or np.any(row>=count) for row in selections):
        raise ValueError('fixed windows exceed recording bounds')
    return selections


def gyro_factors(imu_times, gyro, camera_times, td_s, body_from_camera):
    """Right-tangent bias Jacobian of the actual trapezoidal raw-gyro integral.

    Measured rate = true rate + body_from_camera.apply(camera_bias).
    Delta(b) = Delta(0) Exp(J_b b) + O(||b||^2); this is not exact
    reintegration at each optimized bias, nor calibrated covariance.
    """
    deltas,jacobians = [],[]
    epsilon = 1e-5
    for a,b in zip(camera_times[:-1],camera_times[1:]):
        def integrate(bias):
            body_delta = fusion.integrate_gyro(imu_times,
                gyro-body_from_camera.apply(bias),a+td_s,b+td_s)
            return body_from_camera.inv()*body_delta*body_from_camera
        delta = integrate(np.zeros(3))
        columns = []
        for axis in range(3):
            bias = np.eye(3)[axis]*epsilon
            positive = (delta.inv()*integrate(bias)).as_rotvec()
            negative = (delta.inv()*integrate(-bias)).as_rotvec()
            columns.append((positive-negative)/(2*epsilon))
        deltas.append(delta)
        jacobians.append(np.column_stack(columns))
    return Rotation.concatenate(deltas),np.asarray(jacobians)


def visual_initialization(data, calibration, train):
    """Reinitialize poses using training tracks only, never held-out pixels."""
    camera = calibration['left_intrinsics']
    matrix = np.array([[camera['fx'],0,camera['cx']],
                       [0,camera['fy'],camera['cy']],[0,0,1.]])
    centers, rotations = [np.zeros(3)], [Rotation.identity().as_quat()]
    admission = np.zeros_like(data['valid'])
    admission[0] = train & data['valid'][0]
    for index in range(1,len(data['valid'])):
        mask = train & data['valid'][index]
        cv2.setRNGSeed(0)
        ok,r,t,inliers = cv2.solvePnPRansac(
            data['initial_points'][mask].astype(np.float32),
            data['observations'][index,mask,:2].astype(np.float32),
            matrix,None,iterationsCount=200,reprojectionError=2.,confidence=.999,
            flags=cv2.SOLVEPNP_EPNP)
        if not ok or inliers is None or len(inliers)<20:
            raise ValueError('training_only_PnP_failed')
        admission[index,np.flatnonzero(mask)[inliers.ravel()]] = True
        rotation = Rotation.from_rotvec(r.ravel()).inv()
        centers.append(-rotation.apply(t.ravel()))
        rotations.append(rotation.as_quat())
    return np.asarray(centers),Rotation.from_quat(rotations),admission


def training_support(train, admission):
    # Remove only landmark variables with no temporal support; do not globally
    # discard a track merely because one observation was a PnP outlier.
    supported = train & (admission.sum(axis=0)>=2)
    if np.any(admission[:,supported].sum(axis=1)<20):
        raise ValueError('training_support_collapsed_after_admission')
    return supported


def heldout_rmse(data, calibration, heldout, centers, rotations):
    # Source stereo pixels give withheld point depths; those points never enter
    # pose fitting. Only subsequent views are scored, not their source pixels.
    errors = []
    for index in range(1,len(centers)):
        mask = heldout & data['valid'][index]
        camera_points = rotations[index].inv().apply(data['initial_points'][mask]-centers[index])
        for key,offset,columns in [('left_intrinsics',0.,slice(0,2)),
                                  ('right_intrinsics',calibration['baseline_m'],slice(2,4))]:
            k = calibration[key]
            xyz = camera_points-np.array([offset,0.,0.])
            if np.any(xyz[:,2]<=0):
                raise ValueError('heldout_negative_depth')
            pixels = np.column_stack((k['fx']*xyz[:,0]/xyz[:,2]+k['cx'],
                                      k['fy']*xyz[:,1]/xyz[:,2]+k['cy']))
            errors.extend((pixels-data['observations'][index,mask,columns]).ravel())
    return float(np.sqrt(np.mean(np.square(errors)))) if errors else None


def initial_consistency(data, calibration, train, centers, rotations, gyro_deltas):
    errors = []
    for index in range(len(centers)):
        mask = train & data['valid'][index]
        xyz = rotations[index].inv().apply(data['initial_points'][mask]-centers[index])
        k = calibration['left_intrinsics']
        px = np.column_stack((k['fx']*xyz[:,0]/xyz[:,2]+k['cx'],
                              k['fy']*xyz[:,1]/xyz[:,2]+k['cy']))
        errors.append(np.linalg.norm(px-data['observations'][index,mask,:2],axis=1))
    return dict(training_left_p95_px=[float(np.percentile(e,95)) for e in errors],
        training_left_outlier_fraction_2px=[float(np.mean(e>2.)) for e in errors],
        visual_raw_gyro_disagreement_deg=np.degrees(
            (gyro_deltas.inv()*(rotations[:-1].inv()*rotations[1:])).magnitude()).tolist())


def run_case(name, graph_path, output):
    graph = json.loads(graph_path.read_text())
    inputs = graph['inputs']
    report = json.loads(Path(inputs['stereo_report']).read_text())
    calibration = report['factory_stereo_calibration']
    trajectory = Path(report['trajectory'])
    timestamps = stereo.load_trajectory(trajectory)[0]  # poses are NOT used
    indices = windows(len(timestamps))
    session = Path(inputs['session'])
    input_paths = [graph_path,Path(inputs['stereo_report']),trajectory,
        Path(inputs['vins_spatiotemporal_calibration']),Path(inputs['imu_calibration']),
        session/'d405_frames.csv',session/'external_imu/imu.bin',trajectory.parent/'dataset/frames.csv']
    hashes = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in input_paths}
    mono = fusion.camera_epoch_to_monotonic(session/'d405_frames.csv','infrared_left',timestamps)
    imu_times,gyro,_,_ = fusion.load_calibrated_imu(session/'external_imu/imu.bin',Path(inputs['imu_calibration']))
    config = fusion.load_vins_config(Path(inputs['vins_spatiotemporal_calibration']),-.009109323)
    assert abs(config['td_s']+.009109323)<1e-10
    body_from_camera = Rotation.from_matrix(np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])[:3,:3])
    left_numbers,right_numbers,_ = stereo.match_trajectory_to_stereo_frames(session/'d405_frames.csv',timestamps,trajectory_frame='infrared_left')
    selected = np.concatenate(indices)
    left,right = stereo.load_selected_prepared_stereo_images(trajectory.parent/'dataset',session/'d405_frames.csv',
        {int(left_numbers[i]) for i in selected},{int(right_numbers[i]) for i in selected})
    image_hashes = {f'{stream}:{number}':hashlib.sha256(image.tobytes()).hexdigest()
                    for stream,images in [('left',left),('right',right)]
                    for number,image in images.items()}
    rows = []
    for number,selection in enumerate(indices,1):
        started = time.monotonic()
        row = dict(window=number,indices=selection.tolist(),
                   elapsed_s=(timestamps[selection]-timestamps[0]).tolist())
        try:
            data = track_stereo_window([left[int(left_numbers[i])] for i in selection],
                                      [right[int(right_numbers[i])] for i in selection],calibration,
                                      initialize_poses=False)
            if not data['accepted']:
                row.update(accepted=False,reason=data['reason'])
            else:
                heldout = np.arange(len(data['initial_points'])) % 5 == 0
                train = ~heldout
                centers,rotations,admission = visual_initialization(data,calibration,train)
                original_train = train.copy()
                train = training_support(train,admission)
                deltas,jacobians = gyro_factors(imu_times,gyro,mono[selection],config['td_s'],body_from_camera)
                row['initial_consistency'] = initial_consistency(data,calibration,original_train,centers,rotations,deltas)
                row['admission'] = dict(
                    policy='training-only PnP per-observation inliers, fixed existing2px; source stereo retained',
                    pre_count=data['valid'][:,original_train].sum(axis=1).tolist(),
                    post_count=admission[:,train].sum(axis=1).tolist(),
                    source_depth_bias_caveat='noisy source depth can reject otherwise valid temporal pixels; not proof of corrupted recording')
                result = solve_stereo_window(data['observations'][:,train],admission[:,train],
                    mono[selection]-mono[selection[0]],calibration['left_intrinsics'],
                    calibration['right_intrinsics'],calibration['baseline_m'],
                    data['initial_points'][train],centers,rotations,deltas,
                    gyro_noise_density=.00103,gyro_bias_sigma=.01/3.,
                    gyro_bias_jacobians=jacobians)
                row.update(accepted=result['accepted'],reason=result['reason'],
                    train_tracks=int(train.sum()),heldout_tracks=int(heldout.sum()),
                    initial_heldout_rmse_px=heldout_rmse(data,calibration,heldout,centers,rotations))
                if 'diagnostics' in result:
                    row['diagnostics'] = result['diagnostics']
                if 'detail' in result:
                    row['detail'] = result['detail']
                if 'failures' in result:
                    row['failures'] = result['failures']
                if result['accepted']:
                    row.update(diagnostics=result['diagnostics'],gyro_bias_rad_s=result['gyro_bias'].tolist(),
                        initial_endpoint_m=centers[-1].tolist(),endpoint_m=result['centers'][-1].tolist(),
                        endpoint_change_mm=float(1000*np.linalg.norm(result['centers'][-1]-centers[-1])),
                        optimized_heldout_rmse_px=heldout_rmse(data,calibration,heldout,result['centers'],result['rotations']))
        except (ValueError,RuntimeError,cv2.error) as error:
            row.update(accepted=False,reason=type(error).__name__,detail=str(error))
        row['runtime_s'] = time.monotonic()-started
        rows.append(row)
        (output/f'{name}.json').write_text(json.dumps(dict(case=name,windows=rows,external_reference_used=False),indent=2)+'\n')
        print(name,number,row['accepted'],row['reason'],round(row['runtime_s'],2),flush=True)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items()),'input changed during controls'
    return dict(case=name,windows=rows,input_sha256=hashes,
                decoded_grayscale_frame_sha256=image_hashes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',action='append')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('refuse to overwrite controls')
    cases = load_module('window_cases',ROOT/'.planning/joint_metric_scale_20260927/run_cached_regression.py').CASES
    cases = list(cases)+[(f'fresh{i}',f'joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i==2 else "baseline"}','') for i in range(1,5)]
    unknown = set(args.case or [])-{name for name,_,_ in cases}
    if unknown:
        raise ValueError(f'unknown cases: {unknown}')
    args.output.mkdir(parents=True)
    sources = [Path(__file__),ROOT/'scripts/stereo_window_bundle.py',ROOT/'scripts/prepare_stereo_window_observations.py',
               ROOT/'scripts/align_mast3r_scale_with_stereo.py',ROOT/'scripts/fuse_mast3r_stereo_imu.py']
    hashes = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    rows = []
    for name,path,_ in cases:
        if args.case and name not in args.case:
            continue
        rows.append(run_case(name,ROOT/'reports'/path/'mast3r/graph_fusion_report.json',args.output))
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items()),'source changed during controls'
    (args.output/'summary.json').write_text(json.dumps(dict(cases=rows,source_sha256=hashes,
        external_reference_used=False,production_modified=False,
        interpretation='observation diagnostics only, not trajectory accuracy or calibrated covariance'),indent=2)+'\n')


if __name__ == '__main__':
    main()
