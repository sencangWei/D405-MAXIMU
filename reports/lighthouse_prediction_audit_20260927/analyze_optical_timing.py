"""Verify diagnostic replay invariance and audit device-time observation lag."""
import json
from pathlib import Path

import numpy as np

ROOT = Path('/home/robot/ego_vio_humble')
OUT = Path(__file__).resolve().parent
BOARD = ROOT/'reports/lighthouse_umi_sessions/20260927_023856_lighthouse_world_board_validation_retry/board_validation'


def load(path):
    timing, covariance, streams = [], [], {'POSE':[], 'EXTERNAL_POSE':[]}
    for line in path.open():
        f = line.split()
        if len(f)<3:
            continue
        if f[1:3]==['WM0','OBS_TIMING']:
            timing.append([float(f[0]),*map(float,f[3:])])
        elif f[1:3]==["WM0'",'FULL_COVARIANCE']:
            covariance.append(list(map(float,f[3:])))
        elif (f[1]=='WM0' and f[2]=='POSE') or (f[1]=='WM0-raw-obs' and f[2]=='EXTERNAL_POSE'):
            streams[f[2]].append([float(f[0]),*map(float,f[3:10])])
    return np.asarray(timing),np.asarray(covariance),{k:np.asarray(v) for k,v in streams.items()}


def distribution(a):
    return {'min':float(np.min(a)), 'median':float(np.median(a)),
            'p95':float(np.percentile(a,95)),'max':float(np.max(a))}


def main():
    report = {'result':'DIAGNOSTIC_ONLY','takes':{},'production_modified':False}
    for take in ['gP8kSJ','ucf7GS','rAt98l','board']:
        relative = Path(f'{take}_joint_candidate_fourway/validation.rec')
        baseline = (BOARD/'replay' if take=='board' else ROOT/'reports/lighthouse_gss_coverage_20260926_valid_v1/three_new_validation_v2')/relative
        candidate = OUT/'timing_instrumented'/relative
        if not candidate.exists():
            raise ValueError(f'Missing completed replay {take}')
        timing,covariance,streams = load(candidate)
        _,_,reference = load(baseline)
        invariant = {k:bool(np.array_equal(streams[k],reference[k])) for k in streams}
        if not all(invariant.values()):
            raise ValueError(f'Diagnostic unexpectedly changed poses: {take}: {invariant}')
        if len(timing)!=len(streams['EXTERNAL_POSE']) or covariance.shape!=(len(timing),49):
            raise ValueError(f'Incomplete timing/covariance records {take}: {timing.shape} {covariance.shape}')
        lag = (timing[:,2]-timing[:,1])*1000
        matrix = covariance.reshape(-1,7,7)
        symmetry_error = np.max(np.abs(matrix-matrix.transpose(0,2,1)),axis=(1,2))
        eigen = np.linalg.eigvalsh((matrix+matrix.transpose(0,2,1))/2)
        item = {'pose_streams_byte_numeric_identical':invariant,'optical_updates':len(timing),
                'observation_lag_ms':distribution(lag), 'late_observation_count':int(np.sum(lag>0)),
                'lag_gt_10ms_count':int(np.sum(lag>10)),
                'raw_position_std_mm':distribution(np.sqrt(np.maximum(timing[:,4:7],0))*1000),
                'covariance_negative_eigenvalue_count':int(np.sum(eigen[:,0]<-1e-5)),
                'covariance_symmetry_abs_max':float(np.max(symmetry_error))}
        if take=='board':
            clock=json.loads((BOARD/'fixed_board_comparison.json').read_text())['clock_mapping']['record_to_host_offset_s']
            import sys
            sys.path.insert(0,str(BOARD))
            import compare_fixed_board as b
            ct,_,_=b.load_camera_poses(BOARD/'aprilgrid_camera_poses.csv')
            host,_=b.map_camera_times(ct,'host_monotonic',b.SESSION/'d405_frames.csv')
            origin=json.loads((BOARD/'prediction_gap_diagnosis.json').read_text())['elapsed_origin_host_monotonic_s']
            elapsed=timing[:,0]+clock-origin
            mask=(elapsed>11.35)&(elapsed<11.9)
            item['peak_region_lag_ms']=distribution(lag[mask])
            item['peak_region_position_std_mm']=distribution(np.sqrt(np.maximum(timing[mask,4:7],0))*1000)
            item['peak_region_updates']=[{'elapsed_s':float(t),'lag_ms':float(l),
                'raw_position_std_mm':(np.sqrt(np.maximum(row[4:7],0))*1000).tolist(),
                'filter_speed_mps':float(row[7])} for t,l,row in zip(elapsed[mask],lag[mask],timing[mask])]
        report['takes'][take]=item
    (OUT/'optical_timing_audit.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
