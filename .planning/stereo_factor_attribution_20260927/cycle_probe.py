"""Leave-one-edge-out temporal cycle check from cached UMI observations only."""
import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('complementary', ROOT / 'scripts/fuse_docker2_mast3r_complementary.py')
trajectory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trajectory)


def cycles(edges, rotations):
    # Restrict alternate paths to shorter edges strictly inside the queried
    # interval. The queried edge cannot support its own reliability decision.
    outgoing = {}
    deltas = []
    for index, edge in enumerate(edges):
        first, second = int(edge['first_index']), int(edge['second_index'])
        delta = rotations[first].apply(edge['metric_displacement_camera_i_m'])
        deltas.append(delta)
        outgoing.setdefault(first, []).append((second, index))
    results = []
    for index, edge in enumerate(edges):
        first, last = int(edge['first_index']), int(edge['second_index'])
        path = {first: (0, np.zeros(3), [])}
        for start in range(first, last):
            if start not in path:
                continue
            cost, delta, members = path[start]
            for end, candidate in outgoing.get(start, []):
                if candidate == index or end > last or end - start >= last - first:
                    continue
                next_cost = cost + (end - start) ** 2
                if end not in path or next_cost < path[end][0]:
                    path[end] = (next_cost, delta + deltas[candidate], members + [candidate])
        if last not in path or len(path[last][2]) < 2:
            results.append({'independent_path_available': False})
            continue
        _, predicted, members = path[last]
        residual = deltas[index] - predicted
        results.append({'independent_path_available': True,
                        'first_index': first, 'second_index': last,
                        'midpoint': (first + last) / 2,
                        'alternate_edge_indices': members,
                        'residual_world_m': residual.tolist(),
                        'residual_mm': float(np.linalg.norm(residual) * 1000),
                        'nominal_sigma_mm': 4 * np.sqrt(1 + len(members)),
                        'independence_caveat': 'shared images/features; path is leave-one-edge-out, not statistically independent'})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite output')
    args.output.mkdir(parents=True)
    replay_spec = importlib.util.spec_from_file_location('cached', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    replay = importlib.util.module_from_spec(replay_spec)
    replay_spec.loader.exec_module(replay)
    cases = [(name, ROOT / 'reports' / cached / 'mast3r/graph_fusion_report.json') for name, cached, _ in replay.CASES]
    cases += [(f'fresh{i}', ROOT / f'reports/joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}/mast3r/graph_fusion_report.json') for i in range(1, 5)]
    summary = []
    for name, report_path in cases:
        report = json.loads(report_path.read_text())
        edges = []
        for file in [report['inputs']['stereo_report']] + report['inputs']['additional_stereo_reports']:
            edges.extend(edge for edge in json.loads(Path(file).read_text())['observations'] if edge.get('accepted'))
        _, _, rotations = trajectory.load_trajectory(report_path.parent / 'trajectory_graph.csv')
        values = cycles(edges, rotations)
        supported = [value for value in values if value['independent_path_available']]
        residuals = np.array([value['residual_mm'] for value in supported])
        detail = {'external_reference_used': False, 'case': name, 'checks': values}
        (args.output / f'{name}.json').write_text(json.dumps(detail, indent=2) + '\n')
        item = {'case': name, 'accepted_edges': len(edges), 'alternate_path_edges': len(supported),
                'closure_median_mm': float(np.median(residuals)), 'closure_p95_mm': float(np.percentile(residuals, 95)),
                'above_3_nominal_sigma': int(sum(value['residual_mm'] > 3 * value['nominal_sigma_mm'] for value in supported))}
        for peak in ([581] if name == 'fresh2' else [836, 1071] if name == 'fresh4' else []):
            local = [value['residual_mm'] for value in supported if abs(value['midpoint'] - peak) <= 15]
            item[f'window_{peak}'] = {'count': len(local), 'median_mm': float(np.median(local)), 'p95_mm': float(np.percentile(local, 95))} if local else {'count': 0}
        summary.append(item)
        print(item, flush=True)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
