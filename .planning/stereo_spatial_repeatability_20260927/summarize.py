"""Audit diagnostic-only replay identity and write bounded all-case summaries."""
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/stereo_spatial_repeatability_20260927'
REFERENCE = ROOT / 'reports/stereo_factor_attribution_20260927/raw_gyro_pnp_v1'


def summarize(directory, field):
    rows = []
    for item in json.loads((directory / 'summary.json').read_text())['cases']:
        name = item['case']
        case = json.loads((directory / f'{name}.json').read_text())
        reference = json.loads((REFERENCE / f'{name}.json').read_text())
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


if __name__ == '__main__':
    summarize(OUT / 'ten_case_v1', 'spatial_repeatability')
    if (OUT / 'target_depth_ten_v1/summary.json').exists():
        summarize(OUT / 'target_depth_ten_v1', 'target_depth_holdout')
