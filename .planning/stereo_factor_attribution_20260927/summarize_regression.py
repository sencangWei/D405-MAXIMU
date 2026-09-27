"""Compare frozen ten-case scores without using GT to change any estimate."""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('cached', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    cases = [(name, ROOT / f'reports/joint_metric_scale_20260927/verified_v1_six/{name}/official_score/precision.json') for name, _, _ in replay.CASES]
    cases += [(f'fresh{i}', ROOT / f'reports/joint_scale_independent_four_20260927/take{i}/official_score/precision.json') for i in range(1, 5)]
    rows = []
    for name, baseline in cases:
        target = args.candidate / name
        score = target / 'official_score/precision.json'
        before = json.loads(baseline.read_text())
        row = {'case': name, 'baseline_result': before['result'],
               'baseline_max_mm': before['ate_translation_max_m'] * 1000,
               'candidate_complete': score.is_file()}
        if score.is_file():
            after = json.loads(score.read_text())
            assert after['samples'] == before['samples'], f'{name} scored sample count changed'
            assert after['alignment'] == before['alignment']
            graph = json.loads((target / 'graph_fusion_report.json').read_text())
            assert graph['external_ground_truth_used'] is False
            assert graph['slam_supervision'] is False
            stats = json.loads((args.candidate.with_name(args.candidate.name + '_measurements') / name / 'stereo/validation_summary.json').read_text())
            observation_counts = {}
            for report in (args.candidate.with_name(args.candidate.name + '_measurements') / name / 'stereo').glob('stereo_scale*report.json'):
                content = json.loads(report.read_text())
                observation_counts[report.name] = {
                    'accepted_after_validation': sum(bool(edge.get('accepted')) for edge in content['observations']),
                    'original_quality_snapshot': content.get('quality'),
                    'snapshot_is_not_post_validation_quality': True}
            (target / 'derived_stereo_provenance.json').write_text(json.dumps({
                'global_scale_and_original_quality_frozen': True,
                'policy': 'existing LK scalar reverse gate also applied to SIFT, followed by inverse-vector gate',
                'source_reports_unchanged': True, 'validation_summary': stats,
                'post_validation_counts': observation_counts}, indent=2) + '\n')
            row.update(candidate_result=after['result'], candidate_max_mm=after['ate_translation_max_m'] * 1000,
                       candidate_mean_mm=after['ate_translation_mean_m'] * 1000,
                       candidate_p95_mm=after['ate_translation_p95_m'] * 1000,
                       candidate_within_10mm_ratio=after['ate_translation_within_10mm_ratio'],
                       delta_max_mm=(after['ate_translation_max_m'] - before['ate_translation_max_m']) * 1000,
                       samples=after['samples'], validation=stats)
        rows.append(row)
    complete = all(row['candidate_complete'] for row in rows)
    summary = {'complete': complete, 'total_cases': len(rows),
               'completed_cases': sum(row['candidate_complete'] for row in rows),
               'passed_cases': sum(row.get('candidate_result') == 'PASS' for row in rows),
               'production_changed': False, 'cases': rows}
    (args.candidate / 'comparison.json').write_text(json.dumps(summary, indent=2) + '\n')
    lines = ['# Fixed bidirectional SIFT validation comparison', '',
             f'Completed: {summary["completed_cases"]}/{len(rows)}; PASS: {summary["passed_cases"]}. Production unchanged.', '',
             'SE(3), no scale fit, original scored sample counts preserved; GT scoring-only.', '',
             '| Case | Baseline max mm | Candidate max mm | Mean mm | P95 mm | Result |',
             '|---|---:|---:|---:|---:|---|']
    for row in rows:
        if row['candidate_complete']:
            lines.append(f'| {row["case"]} | {row["baseline_max_mm"]:.3f} | {row["candidate_max_mm"]:.3f} | {row["candidate_mean_mm"]:.3f} | {row["candidate_p95_mm"]:.3f} | {row["candidate_result"]} |')
        else:
            lines.append(f'| {row["case"]} | {row["baseline_max_mm"]:.3f} | pending | — | — | pending |')
    (args.candidate / 'comparison.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({key: value for key, value in summary.items() if key != 'cases'}))
    for row in rows:
        print({key: value for key, value in row.items() if key != 'validation'})


if __name__ == '__main__':
    main()
