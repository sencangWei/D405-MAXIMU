"""Frozen all-ten fixed-cost graph comparison; never selects estimator inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / 'reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1'
CASES = ['fresh1', 'dev1', 'dev2', 'heldout1', 'heldout2', 'heldout3',
         'heldout4', 'fresh2', 'fresh3', 'fresh4']
VARIANTS = ['baseline', 'joint', 'independent']
CONTRACT_KEYS = ['alignment', 'samples', 'thresholds', 'max_interpolation_gap_s',
                 'estimate_samples_total', 'timestamp_overlap_samples']


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(candidate: Path) -> dict:
    status_path = candidate / 'batch_status.json'
    status = json.loads(status_path.read_text())
    records = status.get('cases', [])
    identities = [(row.get('case'), row.get('variant')) for row in records]
    expected = {(case, variant) for case in CASES for variant in VARIANTS}
    if len(identities) != 30 or set(identities) != expected:
        raise ValueError('exact all-thirty graph outcomes required, including failures')
    if status.get('gt_scoring_started_after_all_graphs') is not True:
        raise ValueError('all-graph freeze barrier not confirmed')
    for row in records:
        if not isinstance(row.get('completed'), bool):
            raise ValueError('all thirty outcomes must be finalized; pending is not failure')
        if (row.get('experiment_status') != 'research_fixedcost_not_promoted'
                or row.get('calibrated_covariance') is not False
                or row.get('statistical_independence_claimed') is not False):
            raise ValueError('fixed-cost, non-covariance experiment contract invalid')
    by_id = {(row['case'], row['variant']): row for row in records}
    hashes = {str(status_path): digest(status_path), str(Path(__file__)): digest(Path(__file__))}
    cases = []
    for name in CASES:
        old_path = BASELINE / name / 'official_score/precision.json'
        old = json.loads(old_path.read_text())
        hashes[str(old_path)] = digest(old_path)
        row = {'case': name, 'baseline_replay_max_delta_mm': None}
        for variant in VARIANTS:
            record = by_id[name, variant]
            if not record.get('completed'):
                row[variant] = dict(completed=False, failure_stage=record.get('failure_stage'))
                continue
            path = candidate / variant / name / 'official_score/precision.json'
            score = json.loads(path.read_text())
            for key in CONTRACT_KEYS:
                if old[key] != score[key]:
                    raise ValueError(f'evaluation contract changed: {name}/{variant} {key}')
            if digest(Path(old['ground_truth'])) != digest(Path(score['ground_truth'])):
                raise ValueError(f'reference changed: {name}/{variant}')
            for source in (path, Path(score['ground_truth']), Path(score['estimate'])):
                hashes[str(source)] = digest(source)
            maximum = float(score['ate_translation_max_m']) * 1000
            metrics = {
                label: float(score[key]) * 1000 for label, key in (
                    ('mean_mm', 'ate_translation_mean_m'),
                    ('median_mm', 'ate_translation_median_m'),
                    ('p95_mm', 'ate_translation_p95_m'),
                    ('rmse_mm', 'ate_translation_rmse_m'))
            }
            if not all(0 <= value < float('inf') for value in [maximum, *metrics.values()]):
                raise ValueError(f'nonfinite/negative errors: {name}/{variant}')
            row[variant] = dict(completed=True, max_mm=maximum, **metrics,
                                within_10mm_ratio=score['ate_translation_within_10mm_ratio'],
                                result=score['result'], failures=score['failures'])
            if variant == 'baseline':
                row['baseline_replay_max_delta_mm'] = maximum - float(old['ate_translation_max_m']) * 1000
        cases.append(row)
    variants = {}
    for variant in VARIANTS:
        completed = [row[variant] for row in cases if row[variant]['completed']]
        variants[variant] = dict(
            completed_count=len(completed),
            pass_count=sum(row['result'] == 'PASS' for row in completed),
            worst_max_mm=max((row['max_mm'] for row in completed), default=None),
            numeric_max_target_met=len(completed) == 10 and all(
                row['result'] == 'PASS' and row['max_mm'] < 10 for row in completed))
    for path, expected_hash in hashes.items():
        if digest(Path(path)) != expected_hash:
            raise ValueError(f'frozen scoring input changed: {path}')
    return dict(cases=cases, variants=variants, promotion_authorized=False,
                external_reference_used_in_estimation=False,
                external_reference_used_in_evaluation=True,
                provenance_sha256=hashes,
                warning='Full ATE comparison of fixed-penalty inputs only; correlated covariance is NOT modeled; no promotion or GT selection')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.candidate / 'comparison.json'
    if output.exists():
        raise ValueError('refuse to overwrite comparison')
    result = summarize(args.candidate)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['variants'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
