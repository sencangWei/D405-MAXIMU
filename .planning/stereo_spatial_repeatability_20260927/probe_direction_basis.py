"""Ten-case onboard world-basis contract survey; no trajectory modification."""
import importlib.util
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def stats(values):
    return dict(median=float(np.median(values)), p95=float(np.percentile(values, 95)), maximum=float(np.max(values)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    out = parser.parse_args().output
    if out.exists():
        raise ValueError('Refuse overwrite')
    out.mkdir()
    fusion = module('basis_fusion', ROOT / 'scripts/fuse_mast3r_stereo_imu.py')
    cached = module('basis_cases', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    helper = module('basis_helper', Path(__file__).with_name('basis_contract.py'))
    cases = [(name, ROOT / 'reports' / source / 'mast3r/graph_fusion_report.json') for name, source, _ in cached.CASES]
    cases += [(f'fresh{i}', ROOT / f'reports/joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}/mast3r/graph_fusion_report.json') for i in range(1, 5)]
    summary = []
    for name, graph_path in cases:
        graph = json.loads(graph_path.read_text())
        paths = [Path(graph['output']), Path(graph['inputs']['relative_motion_trajectory'])]
        paths += [Path(graph['inputs']['stereo_report'])] + [Path(p) for p in graph['inputs']['additional_stereo_reports']]
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths + [graph_path]}
        times, _, quats, _ = fusion.load_trajectory(paths[0])
        vt, _, vq, _ = fusion.load_trajectory(paths[1])
        valid = (times >= vt[0]) & (times <= vt[-1])
        # Graph CSV camera attitude has a final constant direction adjustment.
        # Undo exactly that recorded adjustment to recover the factor's basis.
        correction = Rotation.from_rotvec(np.radians(graph['trajectory_frame_alignment']['correction_rotvec_deg']))
        mast = correction.inv() * quats[valid]
        body_from_camera = Rotation.from_matrix(np.asarray(graph['camera_extrinsics']['effective_body_T_trajectory_camera'])[:3, :3])
        vins_camera = Slerp(vt, vq)(times[valid]) * body_from_camera
        position_world = Rotation.from_matrix(graph['relative_motion_alignment']['rotation'])
        mapping = np.full(len(times), -1, dtype=int)
        mapping[valid] = np.arange(valid.sum())
        sampled = json.loads((ROOT / f'reports/stereo_factor_attribution_20260927/raw_gyro_pnp_v1/{name}.json').read_text())['measurements']
        reports = {path.name: json.loads(path.read_text()) for path in paths[2:]}
        edges = []
        for sample in sampled:
            edge = next(edge for edge in reports[sample['family']]['observations']
                        if edge['first_index'] == sample['first'] and edge['second_index'] == sample['second'] and edge.get('accepted'))
            if valid[edge['first_index']]:
                edges.append(edge)
        deltas = np.array([edge['metric_displacement_camera_i_m'] for edge in edges])
        first = np.array([mapping[edge['first_index']] for edge in edges])
        comparison = helper.compare_bases(mast, vins_camera, position_world, deltas, first)
        case = dict(case=name, external_reference_used=False, trajectory_modified=False,
                    selection='same uniform recording-time stratified edges as raw_gyro_pnp_v1, within VINS attitude time coverage',
                    frame_policy='undo graph final constant attitude correction; VINS body to leftIR; align world gauge globally once',
                    vins_is_ground_truth=False, source_sha256=hashes,
                    pairs=[[edge['first_index'], edge['second_index']] for edge in edges], comparison=comparison)
        row = dict(case=name, sampled_edges=len(edges), constant_alignment_difference_deg=comparison['constant_alignment_difference_deg'])
        for basis in ('position_fit', 'attitude_fit'):
            row[basis] = {key: stats(comparison[basis][key]) for key in ('attitude_difference_deg', 'projected_displacement_difference_mm')}
        for path, digest in hashes.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        (out / f'{name}.json').write_text(json.dumps(case, indent=2) + '\n')
        summary.append(row)
        print(json.dumps(row), flush=True)
    (out / 'summary.json').write_text(json.dumps(dict(cases=summary, external_reference_used=False, trajectory_modified=False), indent=2) + '\n')


if __name__ == '__main__':
    main()
