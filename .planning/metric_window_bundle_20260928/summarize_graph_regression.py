"""Evaluation-only frozen all-ten comparison; never selects estimator factors."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / 'reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    output = args.candidate / 'comparison.json'
    if output.exists():
        raise ValueError('Refuse to overwrite comparison')
    status_path = args.candidate / 'batch_status.json'
    status = json.loads(status_path.read_text())
    expected = {'dev1', 'dev2', 'heldout1', 'heldout2', 'heldout3', 'heldout4', 'fresh1', 'fresh2', 'fresh3', 'fresh4'}
    if {row['case'] for row in status['cases']} != expected or len(status['cases']) != 10:
        raise ValueError('Incomplete ten-case census')
    rows = []
    hashes = {str(status_path): digest(status_path), str(Path(__file__)): digest(Path(__file__))}
    for row in status['cases']:
        name = row['case']
        if not row['completed']:
            rows.append(dict(case=name, completed=False, failure_stage=row.get('failure_stage')))
            continue
        paths = [BASELINE / name / 'official_score/precision.json', args.candidate / name / 'official_score/precision.json']
        old, new = [json.loads(path.read_text()) for path in paths]
        for key in ['alignment', 'samples', 'thresholds', 'max_interpolation_gap_s', 'estimate_samples_total', 'timestamp_overlap_samples']:
            if old[key] != new[key]:
                raise ValueError(f'Evaluation contract changed: {name} {key}')
        if digest(old['ground_truth']) != digest(new['ground_truth']):
            raise ValueError(f'External reference changed: {name}')
        paths.extend([Path(old['ground_truth']), Path(new['ground_truth']), Path(old['estimate']), Path(new['estimate'])])
        hashes.update({str(path): digest(path) for path in paths})
        rows.append(dict(case=name, completed=True,
            old_max_mm=old['ate_translation_max_m'] * 1000, max_mm=new['ate_translation_max_m'] * 1000,
            old_mean_mm=old['ate_translation_mean_m'] * 1000, mean_mm=new['ate_translation_mean_m'] * 1000,
            median_mm=new['ate_translation_median_m'] * 1000, p95_mm=new['ate_translation_p95_m'] * 1000,
            rmse_mm=new['ate_translation_rmse_m'] * 1000, within_10mm_ratio=new['ate_translation_within_10mm_ratio'],
            old_result=old['result'], result=new['result'], failures=new['failures'],
            lost_previous_pass=old['result'] == 'PASS' and new['result'] != 'PASS'))
    completed = [row for row in rows if row['completed']]
    report = dict(cases=rows, all_ten_completed=len(completed) == 10,
        old_pass_count=sum(row['old_result'] == 'PASS' for row in completed),
        pass_count=sum(row['result'] == 'PASS' for row in completed),
        target_met=len(completed) == 10 and all(row['result'] == 'PASS' and row['max_mm'] < 10 for row in completed),
        lost_previous_pass_count=sum(row['lost_previous_pass'] for row in completed),
        external_reference_used_in_estimation=False, external_reference_used_in_evaluation=True,
        provenance_sha256=hashes, warning='No promotion or GT-based candidate selection is performed here')
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
