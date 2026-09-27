"""Paired onboard comparisons only; no external reference or parameter selection."""
import argparse
import json
from pathlib import Path

import numpy as np


def median(values):
    return float(np.median(values)) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for path in sorted(args.probe.glob('*.json')):
        data = json.loads(path.read_text())
        if 'measurements' not in data:
            continue
        assert data['external_reference_used'] is False
        assert data['trajectory_modified'] is False
        comparisons = {}
        for label in ('mast3r_fixed', 'raw_gyro_fixed'):
            pairs = [(item['free'], item[label]) for item in data['measurements']
                     if item['free']['measurement'].get('accepted') and item[label]['measurement'].get('accepted')
                     and item['free']['measurement']['method'] == item[label]['measurement']['method']]
            closures = [(median(first['reverse_closure_mm']), median(second['reverse_closure_mm']))
                        for first, second in pairs if first['reverse_closure_measured'] and second['reverse_closure_measured']]
            vins = [(first['vs_vins_displacement_mm'], second['vs_vins_displacement_mm'])
                    for first, second in pairs if 'vs_vins_displacement_mm' in first and 'vs_vins_displacement_mm' in second]
            comparisons[label] = {
                'same_method_accepted_pairs': len(pairs),
                'closure_pairs': len(closures),
                'free_reverse_closure_median_mm': median([first for first, _ in closures]),
                'fixed_reverse_closure_median_mm': median([second for _, second in closures]),
                'closure_improved_pairs': sum(second < first for first, second in closures),
                'reprojection_change_median_px': median([second['measurement']['pnp_reprojection_median_px'] -
                                                        first['measurement']['pnp_reprojection_median_px'] for first, second in pairs]),
                'translation_vector_change_median_mm': median([np.linalg.norm(np.asarray(second['measurement']['metric_displacement_camera_i_m']) -
                                                                             np.asarray(first['measurement']['metric_displacement_camera_i_m'])) * 1000
                                                               for first, second in pairs]),
                'vins_displacement_comparison_pairs': len(vins),
                'free_vs_vins_median_mm': median([first for first, _ in vins]),
                'fixed_vs_vins_median_mm': median([second for _, second in vins]),
                'vins_disagreement_change_median_mm': median([second - first for first, second in vins]),
            }
        rows.append({'case': data['case'], 'sampled_edges': len(data['measurements']), 'comparisons': comparisons})
    output = {'complete': len(rows) == 10, 'external_reference_used': False, 'production_changed': False,
              'caveat': 'Independent relative gyro has fixed calibration bias; reverse closure can improve through shared constraints and is not proof of absolute trajectory accuracy. VINS is not truth.',
              'cases': rows}
    (args.probe / 'paired_comparison.json').write_text(json.dumps(output, indent=2) + '\n')
    lines = ['# Raw gyro PnP diagnostic (not a trajectory repair)', '',
             f'Cases: {len(rows)}/10; same-method accepted pairs only; external GT never read.', '',
             output['caveat'], '',
             '| Case | Paired edges | Closure free→gyro mm | Reprojection change px | VINS disagreement change mm |',
             '|---|---:|---:|---:|---:|']
    for row in rows:
        result = row['comparisons']['raw_gyro_fixed']
        values = [result['free_reverse_closure_median_mm'], result['fixed_reverse_closure_median_mm'],
                  result['reprojection_change_median_px'], result['vins_disagreement_change_median_mm']]
        formatted = [f'{value:.3f}' if value is not None else 'n/a' for value in values]
        lines.append(f'| {row["case"]} | {result["same_method_accepted_pairs"]} | {formatted[0]}→{formatted[1]} | {formatted[2]} | {formatted[3]} |')
    (args.probe / 'paired_comparison.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
