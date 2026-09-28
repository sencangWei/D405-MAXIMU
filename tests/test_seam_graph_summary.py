import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.planning/metric_window_bundle_20260928/summarize_seam_graph_regression.py'
spec = importlib.util.spec_from_file_location('seam_graph_summary', SCRIPT)
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


def fixture(tmp_path, monkeypatch):
    candidate = tmp_path / 'candidate'
    baseline = tmp_path / 'old'
    candidate.mkdir()
    baseline.mkdir()
    monkeypatch.setattr(summary, 'BASELINE', baseline)
    reference = tmp_path / 'gt.csv'
    estimate = tmp_path / 'estimate.csv'
    reference.write_text('frozen reference')
    estimate.write_text('frozen estimate')
    precision = dict(alignment='se3', samples=1142, thresholds={'max_m': .01},
                     max_interpolation_gap_s=.05, estimate_samples_total=1199,
                     timestamp_overlap_samples=1142, ground_truth=str(reference),
                     estimate=str(estimate), ate_translation_max_m=.009,
                     ate_translation_mean_m=.003, ate_translation_median_m=.002,
                     ate_translation_p95_m=.006, ate_translation_rmse_m=.004,
                     ate_translation_within_10mm_ratio=1.0, result='PASS', failures=[])
    records = []
    for name in summary.CASES:
        for variant in summary.VARIANTS:
            folder = candidate / variant / name / 'official_score'
            folder.mkdir(parents=True)
            (folder / 'precision.json').write_text(json.dumps(precision))
            records.append(dict(case=name, variant=variant, completed=True,
                                experiment_status='research_fixedcost_not_promoted',
                                calibrated_covariance=False,
                                statistical_independence_claimed=False))
        old = baseline / name / 'official_score'
        old.mkdir(parents=True)
        (old / 'precision.json').write_text(json.dumps(precision))
    status = dict(cases=records, gt_scoring_started_after_all_graphs=True)
    (candidate / 'batch_status.json').write_text(json.dumps(status))
    return candidate, status


def test_all_thirty_report_is_analysis_only_and_refuses_overwrite(tmp_path, monkeypatch):
    candidate, _ = fixture(tmp_path, monkeypatch)
    result = summary.summarize(candidate)
    assert len(result['cases']) == 10
    assert result['variants']['joint']['pass_count'] == 10
    assert result['variants']['independent']['numeric_max_target_met'] is True
    assert result['promotion_authorized'] is False
    assert result['external_reference_used_in_estimation'] is False
    assert result['external_reference_used_in_evaluation'] is True
    assert all(row['baseline_replay_max_delta_mm'] == 0 for row in result['cases'])
    summary.main(['--candidate', str(candidate)])
    with pytest.raises(ValueError, match='overwrite'):
        summary.main(['--candidate', str(candidate)])


@pytest.mark.parametrize('mutation', ['duplicate', 'barrier', 'covariance'])
def test_rejects_incomplete_or_unfrozen_contract(tmp_path, monkeypatch, mutation):
    candidate, status = fixture(tmp_path, monkeypatch)
    if mutation == 'duplicate':
        status['cases'][-1] = status['cases'][0]
    elif mutation == 'barrier':
        status['gt_scoring_started_after_all_graphs'] = False
    else:
        status['cases'][0]['calibrated_covariance'] = True
    (candidate / 'batch_status.json').write_text(json.dumps(status))
    with pytest.raises(ValueError):
        summary.summarize(candidate)


def test_failed_graph_retained_and_sample_change_rejected(tmp_path, monkeypatch):
    candidate, status = fixture(tmp_path, monkeypatch)
    failed = next(row for row in status['cases']
                  if row['case'] == summary.CASES[0] and row['variant'] == 'joint')
    failed.update(completed=False, failure_stage='graph')
    other = next(row for row in status['cases']
                 if row['case'] == summary.CASES[2] and row['variant'] == 'independent')
    other.update(completed=False, failure_stage='body')
    status['cases'].reverse()
    (candidate / 'batch_status.json').write_text(json.dumps(status))
    result = summary.summarize(candidate)
    assert result['variants']['joint']['completed_count'] == 9
    assert result['variants']['joint']['numeric_max_target_met'] is False
    assert result['cases'][0]['joint']['failure_stage'] == 'graph'
    assert result['variants']['independent']['completed_count'] == 9
    assert result['cases'][2]['independent']['failure_stage'] == 'body'
    assert result['cases'][2]['joint']['completed'] is True
    path = candidate / 'independent' / summary.CASES[0] / 'official_score/precision.json'
    precision = json.loads(path.read_text())
    precision['samples'] -= 1
    path.write_text(json.dumps(precision))
    with pytest.raises(ValueError, match='samples'):
        summary.summarize(candidate)
