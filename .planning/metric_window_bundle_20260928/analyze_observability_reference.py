"""AFTER-freeze descriptive association; never estimator selection or ATE.

Inputs: all-ten UMI-only census plus an already frozen local-displacement score.
No pose fitting, reference editing, candidate selection, or quality thresholds.
Window correlations are descriptive: adjacent samples are not independent.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

CASE_NAMES = {'dev1', 'dev2', *(f'heldout{i}' for i in range(1, 5)),
              *(f'fresh{i}' for i in range(1, 5))}
FEATURES = ('all_factor_response', 'pixel_response', 'endpoint_tracks',
            'source_endpoint_common_tracks', 'endpoint_depth_median_m',
            'solver_optimality', 'pixel_p95_px', 'heldout_rmse_px')


def indexed_cases(cases):
    result = {case['case']: case for case in cases}
    if len(cases) != 10 or set(result) != CASE_NAMES:
        raise ValueError('exact ten distinct frozen case names required')
    return result


def windows(case):
    result = {row['window']: row for row in case['windows']}
    if len(result) != len(case['windows']):
        raise ValueError('duplicate window')
    return result


def features(row):
    diag = row.get('diagnostics', {})
    obs = diag.get('endpoint_observability', {})
    return dict(
        all_factor_response=obs.get('all_factors', {}).get('weak_response_mm_per_unit_normalized_residual'),
        pixel_response=obs.get('pixel_rows_only', {}).get('weak_response_mm_per_unit_normalized_residual'),
        endpoint_tracks=obs.get('endpoint_tracks'),
        source_endpoint_common_tracks=obs.get('source_endpoint_common_tracks'),
        endpoint_depth_median_m=(obs['endpoint_depth_m_quantiles'][1]
                                 if 'endpoint_depth_m_quantiles' in obs else None),
        solver_optimality=obs.get('solver_optimality'),
        pixel_p95_px=diag.get('pixel_p95_px'),
        heldout_rmse_px=row.get('optimized_heldout_rmse_px'))


def correlations(rows):
    output = {}
    for feature in FEATURES:
        pairs = [(r[feature], r['local_error_mm']) for r in rows
                 if r[feature] is not None and np.isfinite(r[feature])]
        coefficient = None
        if len(pairs) >= 3:
            x, y = np.asarray(pairs).T
            if len(set(x)) > 1 and len(set(y)) > 1:
                coefficient = float(spearmanr(x, y).statistic)
        output[feature] = dict(pairs=len(pairs), missing_feature_windows=len(rows)-len(pairs),
                               spearman=coefficient)
    return output


def associate(internal_cases, external_cases):
    internal = indexed_cases(internal_cases)
    external = indexed_cases(external_cases)
    all_rows, reports = [], []
    unscored = 0
    for name in sorted(CASE_NAMES):
        measurements, scores = windows(internal[name]), windows(external[name])
        if set(measurements) != set(scores):
            raise ValueError('window identities differ')
        rows = []
        for number, row in measurements.items():
            score = scores[number]
            if score['accepted'] != row['accepted']:
                raise ValueError('frozen acceptance differs')
            if not row['accepted']:
                continue
            if not score.get('scored'):
                unscored += 1
                continue
            error = float(score['optimized_local_error_mm'])
            if not np.isfinite(error) or error < 0:
                raise ValueError('invalid external local error')
            rows.append(dict(case=name, window=number, local_error_mm=error, **features(row)))
        all_rows.extend(rows)
        reports.append(dict(case=name, scored_windows=len(rows), associations=correlations(rows)))
    return dict(scope='local_displacement_NOT_full_trajectory_ATE',
                external_reference_used_in_estimation=False,
                external_reference_used_in_evaluation=True,
                used_for_estimator_selection=False, scored_windows=len(all_rows),
                unscored_accepted_windows=unscored, pooled=correlations(all_rows),
                cases=reports, windows=all_rows,
                warning='Descriptive only, not proof of causation; adjacent windows correlated; no p-value/accuracy guarantee')


def verify_local_evaluation(external, previous):
    if (external.get('external_reference_used_in_estimation') is not False
            or external.get('external_reference_used_in_evaluation') is not True):
        raise ValueError('evaluation-only reference flags required')
    hashes = external.get('source_sha256')
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('frozen evaluation source hashes required')
    for raw, expected in hashes.items():
        if hashlib.sha256(Path(raw).read_bytes()).hexdigest() != expected:
            raise ValueError('frozen local evaluation source changed: '+raw)
    previous_hash = hashlib.sha256(previous.read_bytes()).hexdigest()
    matches = [h for p, h in hashes.items() if Path(p).resolve() == previous.resolve()]
    if matches != [previous_hash]:
        raise ValueError('local evaluation is not from the compared frozen controls')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--controls', type=Path, required=True)
    parser.add_argument('--previous-controls', type=Path, required=True)
    parser.add_argument('--local-evaluation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--numeric-runtime-summary', type=Path, action='append')
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('refuse to overwrite association')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import summarize_observability_controls as check
    current = args.controls/'summary.json'
    previous = args.previous_controls/'summary.json'
    # This verifies completeness, input/source hashes and unchanged endpoints
    # BEFORE any external score/reference file is opened.
    if args.numeric_runtime_summary:
        verified = check.summarize(current, previous,
                                  numeric_runtime_summaries=args.numeric_runtime_summary)
    else:
        verified = check.summarize(current, previous)
    external = json.loads(args.local_evaluation.read_text())
    verify_local_evaluation(external, current)
    current_data = json.loads(current.read_text())
    report = associate(current_data['cases'], external['cases'])
    paths = [current, previous, args.local_evaluation, Path(__file__), Path(check.__file__)]
    paths.extend(args.numeric_runtime_summary or [])
    report['source_sha256'] = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report['verified_numeric_reproducibility'] = verified['identity']
    check.verify_hashes(current_data, 'post association controls')
    verify_local_evaluation(external, current)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print('Scored', report['scored_windows'], 'windows; association only, no estimator changes')


if __name__ == '__main__':
    main()
