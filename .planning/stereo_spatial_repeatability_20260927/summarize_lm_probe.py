"""Preserve all sampled pairs, report rejection changes and matched-pair scores."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'reports/stereo_spatial_repeatability_20260927'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--score', type=Path, required=True)
    parser.add_argument('--label', choices=('free', 'raw_gyro_gate'), default='free')
    args = parser.parse_args()
    rows = []
    for entry in json.loads((args.probe / 'summary.json').read_text())['cases']:
        name = entry['case']
        raw = json.loads((BASE / f'production_depth_raw_ten_v2/{name}.json').read_text())
        candidate = json.loads((args.probe / f'{name}.json').read_text())
        assert raw['source_sha256'] == candidate['source_sha256']
        assert raw['replay_parameters'] == candidate['replay_parameters']
        assert not candidate['external_reference_used'] and not candidate['trajectory_modified']
        for path, digest in raw['source_sha256'].items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        assert [(m['first'], m['second']) for m in raw['measurements']] == [
            (m['first'], m['second']) for m in candidate['measurements']]
        for a, b in zip(raw['measurements'], candidate['measurements']):
            assert a['free']['pnp_correspondence_and_inlier_sha256'][:1] == b[args.label]['pnp_correspondence_and_inlier_sha256'][:1]
        source = json.loads((BASE / f'local_stereo_scoring_cached_ten_v2/{name}.json').read_text())
        scored = json.loads((args.score / f'{name}.json').read_text())
        source_rows = {(m['first'], m['second']): m for m in source['measurements']}
        pairs = [(source_rows[(m['first'], m['second'])], m) for m in scored['measurements'] if m['status'] == 'scored']
        rows.append(dict(case=name, sampled_edges=len(raw['measurements']), compared_pairs=len(pairs),
                         rejected_edges=[dict(first=m['first'], second=m['second'],
                                              reason=m[args.label]['measurement'].get('reason'),
                                              sift_failure_reason=m[args.label]['measurement'].get('sift_failure_reason'))
                                         for m in candidate['measurements'] if not m[args.label]['measurement'].get('accepted')],
                         paired_original_median_mm=float(np.median([a['local_displacement_error_mm'] for a, b in pairs])),
                         paired_candidate_median_mm=float(np.median([b['local_displacement_error_mm'] for a, b in pairs])),
                         paired_original_max_mm=max(a['local_displacement_error_mm'] for a, b in pairs),
                         paired_candidate_max_mm=max(b['local_displacement_error_mm'] for a, b in pairs)))
    output = dict(cases=rows, external_reference_used_in_optimization=False,
                  external_reference_used_in_evaluation=True, production_changed=False,
                  policy='Existing free-PnP LM on original RANSAC inliers; original gates. raw_gyro_gate changes only rotation gate reference, never fixes PnP rotation.',
                  warning='Observation-only sampled-pair diagnostic, not all production edges or trajectory ATE. Rejection is reported, not treated as corrected accuracy.')
    (args.probe / f'{args.label}_audit.json').write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
