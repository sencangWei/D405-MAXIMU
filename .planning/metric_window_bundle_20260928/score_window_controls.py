"""Evaluation-only local displacement scoring AFTER frozen window estimation.

No write access to measurements, estimator settings, calibration or trajectories.
This is not full-trajectory ATE and does not choose production constraints.
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import align_mast3r_scale_with_stereo as stereo
import evaluate_slam_ground_truth as evaluation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--controls',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('refuse to overwrite evaluation')
    summary_path = args.controls/'summary.json'
    controls = json.loads(summary_path.read_text())
    spec = importlib.util.spec_from_file_location('score_cases',ROOT/'.planning/joint_metric_scale_20260927/run_cached_regression.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = {name:source for name,source,_ in module.CASES}
    sources.update({f'fresh{i}':f'joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i==2 else "baseline"}' for i in range(1,5)})
    hashes = {str(path):hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (summary_path,Path(__file__))}
    results = []
    for case in controls['cases']:
        name = case['case']
        graph_path = ROOT/'reports'/sources[name]/'mast3r/graph_fusion_report.json'
        precision_path = (ROOT/f'reports/joint_scale_independent_four_20260927/take{name[-1]}/official_score/precision.json'
                          if name.startswith('fresh') else
                          ROOT/f'reports/joint_metric_scale_20260927/verified_v1_six/{name}/official_score/precision.json')
        graph = json.loads(graph_path.read_text())
        precision = json.loads(precision_path.read_text())
        reference_path = Path(precision['ground_truth'])
        trajectory = json.loads(Path(graph['inputs']['stereo_report']).read_text())['trajectory']
        times = stereo.load_trajectory(Path(trajectory))[0]
        gt_times,gt_positions,gt_quats = evaluation.load_trajectory(reference_path)
        inside,accepted,interpolated,quaternions = evaluation.interpolate_ground_truth(
            times,gt_times,gt_positions,gt_quats,precision['max_interpolation_gap_s'])
        mapping = np.full(len(times),-1,dtype=int)
        mapping[np.flatnonzero(inside)[accepted]] = np.arange(len(interpolated))
        body_rotations = Rotation.from_quat(quaternions)
        extrinsic = np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])
        positions = interpolated[:,1:]+body_rotations.apply(extrinsic[:3,3])
        rotations = body_rotations*Rotation.from_matrix(extrinsic[:3,:3])
        for path in (graph_path,precision_path,reference_path,Path(trajectory)):
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = []
        for window in case['windows']:
            row = dict(window=window['window'],accepted=window['accepted'],reason=window['reason'])
            if window['accepted']:
                i,j = mapping[window['indices'][0]],mapping[window['indices'][-1]]
                if min(i,j)<0:
                    row.update(scored=False,score_reason='outside_reference_coverage')
                else:
                    delta = rotations[i].inv().apply(positions[j]-positions[i])
                    row.update(scored=True,reference_displacement_m=delta.tolist(),
                        initial_local_error_mm=float(1000*np.linalg.norm(np.asarray(window['initial_endpoint_m'])-delta)),
                        optimized_local_error_mm=float(1000*np.linalg.norm(np.asarray(window['endpoint_m'])-delta)))
            rows.append(row)
        results.append(dict(case=name,windows=rows))
        scored = [row for row in rows if row.get('scored')]
        print(name,'scored',len(scored),'improved',sum(r['optimized_local_error_mm']<r['initial_local_error_mm'] for r in scored),flush=True)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(cases=results,source_sha256=hashes,
        external_reference_used_in_estimation=False,external_reference_used_in_evaluation=True,
        calibration_and_time_policy='existing official body reference + fixed body-to-leftIR lever; no new fit',
        warning='local window displacement evaluation, NOT full-trajectory ATE; reference uncertainty remains'),indent=2)+'\n')


if __name__ == '__main__':
    main()
