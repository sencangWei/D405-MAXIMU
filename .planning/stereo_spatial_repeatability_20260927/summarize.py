"""Audit diagnostic-only replay identity and write bounded all-case summaries."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/stereo_spatial_repeatability_20260927'
REFERENCE = ROOT / 'reports/stereo_factor_attribution_20260927/raw_gyro_pnp_v1'


def summarize(directory, field, reference_directory=REFERENCE):
    rows = []
    for item in json.loads((directory / 'summary.json').read_text())['cases']:
        name = item['case']
        case = json.loads((directory / f'{name}.json').read_text())
        reference = json.loads((reference_directory / f'{name}.json').read_text())
        stripped = json.loads(json.dumps(case))
        for measurement in stripped['measurements']:
            measurement['free']['measurement'].pop(field, None)
        assert stripped == reference, f'{name}: sidecar changed existing replay fields'
        assert not case['external_reference_used'] and not case['trajectory_modified']
        for path, digest in case['source_sha256'].items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        measured = [row['free']['measurement'][field] for row in case['measurements']
                    if field in row['free']['measurement']]
        row = {'case': name, 'sampled_edges': len(case['measurements']), 'accepted_free': len(measured),
               'existing_replay_fields_unchanged': True}
        if field == 'spatial_repeatability':
            good = [m for m in measured if m['status'] == 'observable']
            maximums = [m['translation_max_mm'] for m in good]
            row.update(observable=len(good),
                       tile_max_median_mm=float(np.median(maximums)),
                       tile_max_p95_mm=float(np.percentile(maximums, 95)),
                       tile_maximum_mm=max(maximums),
                       lm_shift_p95_mm=float(np.percentile([m['refinement_shift_mm'] for m in measured], 95)))
        else:
            good = [m for m in measured if m['status'] == 'measured']
            row.update(measured=len(good),
                       valid_target_fraction_median=float(np.median([m['valid_target_depth_fraction'] for m in measured])),
                       disparity_median_px=float(np.median([m['absolute_disparity_median_px'] for m in good])),
                       disparity_p95_px=float(np.percentile([m['absolute_disparity_p95_px'] for m in good], 95)),
                       depth_median_mm=float(np.median([m['absolute_depth_median_mm'] for m in good])),
                       depth_p95_mm=float(np.percentile([m['absolute_depth_p95_mm'] for m in good], 95)),
                       depth_ratio_median=float(np.median([m['predicted_to_measured_depth_ratio_median'] for m in good])))
        rows.append(row)
    output = {'cases': rows, 'sampled_edges': sum(row['sampled_edges'] for row in rows),
              'external_reference_used': False, 'trajectory_modified': False,
              'warning': 'Measurement consistency/sensitivity, not absolute accuracy or calibrated covariance.'}
    (directory / 'audit_summary.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output, indent=2))


def audit_ransac(directory):
    rows = []
    for item in json.loads((directory/'summary.json').read_text())['cases']:
        name = item['case']
        case = json.loads((directory/f'{name}.json').read_text())
        reference = json.loads((REFERENCE/f'{name}.json').read_text())
        stripped = json.loads(json.dumps(case))
        diagnostics = [r['free']['measurement'].get('ransac_repeatability', {}) for r in case['measurements']]
        for r in stripped['measurements']:
            r['free']['measurement'].pop('ransac_repeatability', None)
        assert stripped==reference, f'{name}: original diagnostic fields changed'
        for path,digest in case['source_sha256'].items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
        measured = [d for d in diagnostics if 'translation_diameter_mm' in d]
        rows.append(dict(case=name, sampled_edges=len(case['measurements']), measured_edges=len(measured),
                         maximum_translation_diameter_mm=max(d['translation_diameter_mm'] for d in measured),
                         maximum_rotation_diameter_deg=max(d['rotation_diameter_deg'] for d in measured),
                         previous_control_fields_unchanged=True))
    output = dict(cases=rows, external_reference_used=False, trajectory_modified=False,
                  interpretation='Changing global RNG seed alone on these fixed correspondence inputs does not perturb PnP. Original cached input sets may differ.')
    (directory/'audit_summary.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(output,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    if args.production:
        reference = OUT / 'production_depth_raw_ten_v2'
        summarize(OUT / 'production_depth_spatial_ten_v2', 'spatial_repeatability', reference)
        summarize(OUT / 'production_depth_target_ten_v2', 'target_depth_holdout', reference)
    else:
        summarize(OUT / 'ten_case_v1', 'spatial_repeatability')
        if (OUT / 'target_depth_ten_v1/summary.json').exists():
            summarize(OUT / 'target_depth_ten_v1', 'target_depth_holdout')
        if (OUT/'ransac_repeatability_ten_v1/summary.json').exists():
            audit_ransac(OUT/'ransac_repeatability_ten_v1')
