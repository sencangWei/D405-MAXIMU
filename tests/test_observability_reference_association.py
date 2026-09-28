import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT/'.planning/metric_window_bundle_20260928/analyze_observability_reference.py'
spec = importlib.util.spec_from_file_location('observability_reference', PATH)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def make_cases():
    internal, external = [], []
    for name in analysis.CASE_NAMES:
        rows, scores = [], []
        for number in range(1, 5):
            rows.append(dict(window=number, accepted=True,
                diagnostics=dict(pixel_p95_px=number/10,
                    endpoint_observability=dict(
                        all_factors=dict(weak_response_mm_per_unit_normalized_residual=number),
                        pixel_rows_only=dict(weak_response_mm_per_unit_normalized_residual=number*2),
                        endpoint_tracks=50-number, source_endpoint_common_tracks=30-number,
                        endpoint_depth_m_quantiles=[.1,.2,.3],solver_optimality=.1))))
            scores.append(dict(window=number, accepted=True, scored=True,
                optimized_local_error_mm=number*3))
        internal.append(dict(case=name, windows=rows))
        external.append(dict(case=name, windows=scores))
    return internal, external


def test_all_cases_associated_without_candidate_selection():
    internal, external = make_cases()
    report = analysis.associate(internal, external)
    assert report['scored_windows'] == 40
    assert report['used_for_estimator_selection'] is False
    assert report['scope'] == 'local_displacement_NOT_full_trajectory_ATE'
    assert report['pooled']['all_factor_response']['spearman'] == pytest.approx(1)
    assert report['pooled']['endpoint_tracks']['spearman'] == pytest.approx(-1)
    assert len(report['cases']) == 10


def test_null_sensitivity_and_unscored_windows_are_explicit_not_zero():
    internal, external = make_cases()
    internal[0]['windows'][0]['diagnostics']['endpoint_observability']['all_factors']['weak_response_mm_per_unit_normalized_residual'] = None
    external[0]['windows'][1]['scored'] = False
    report = analysis.associate(internal, external)
    assert report['scored_windows'] == 39
    assert report['unscored_accepted_windows'] == 1
    assert report['pooled']['all_factor_response']['pairs'] == 38
    assert report['pooled']['all_factor_response']['missing_feature_windows'] == 1


@pytest.mark.parametrize('fault', ['duplicate_case', 'duplicate_window', 'changed_acceptance'])
def test_mismatched_frozen_inputs_refused(fault):
    internal, external = make_cases()
    if fault == 'duplicate_case': external[-1]['case'] = external[0]['case']
    elif fault == 'duplicate_window': external[0]['windows'][-1]['window'] = 1
    else: external[0]['windows'][0]['accepted'] = False
    with pytest.raises(ValueError):
        analysis.associate(internal, external)


def test_cli_accepts_actual_local_score_schema_after_census_preflight(tmp_path, monkeypatch):
    sys.path.insert(0, str(PATH.parent))
    import summarize_observability_controls as check
    internal, external = make_cases()
    controls, previous = tmp_path/'controls', tmp_path/'previous'
    controls.mkdir(); previous.mkdir()
    (controls/'summary.json').write_text(json.dumps({'cases':internal}))
    previous_path=previous/'summary.json'
    previous_path.write_text('frozen previous controls')
    score=dict(cases=external, external_reference_used_in_estimation=False,
        external_reference_used_in_evaluation=True,
        source_sha256={str(controls/'summary.json'):hashlib.sha256((controls/'summary.json').read_bytes()).hexdigest()})
    evaluation=tmp_path/'local_evaluation.json'
    evaluation.write_text(json.dumps(score))
    calls=[]
    def preflight(current, old):
        calls.append('census verified')
        return {'identity':{'accepted_reason_indices_unchanged':True}}
    monkeypatch.setattr(check,'summarize',preflight)
    # Census validation is tested separately against real producer-shaped
    # fixtures. This test targets the evaluation schema and ordering boundary.
    monkeypatch.setattr(check,'verify_hashes',lambda *args:None)
    real_verify=analysis.verify_local_evaluation
    def verified(*args):
        assert calls[0]=='census verified'
        calls.append('evaluation verified')
        return real_verify(*args)
    monkeypatch.setattr(analysis,'verify_local_evaluation',verified)
    output=tmp_path/'association.json'
    analysis.main(['--controls',str(controls),'--previous-controls',str(previous),
        '--local-evaluation',str(evaluation),'--output',str(output)])
    report=json.loads(output.read_text())
    assert report['scored_windows']==40
    assert report['used_for_estimator_selection'] is False
    assert len(calls)==3


@pytest.mark.parametrize('bad', ['estimation_flag','evaluation_flag','source_hash','binding'])
def test_frozen_eval_flags_hash_and_control_binding_fail_closed(tmp_path,bad):
    previous=tmp_path/'previous.json'; previous.write_text('previous')
    other=tmp_path/'other.json'; other.write_text('other')
    report=dict(external_reference_used_in_estimation=False,
        external_reference_used_in_evaluation=True,
        source_sha256={str(previous):hashlib.sha256(previous.read_bytes()).hexdigest()})
    if bad=='estimation_flag': report['external_reference_used_in_estimation']=True
    elif bad=='evaluation_flag': report['external_reference_used_in_evaluation']=False
    elif bad=='source_hash': report['source_sha256'][str(previous)]='0'*64
    else: report['source_sha256']={str(other):hashlib.sha256(other.read_bytes()).hexdigest()}
    with pytest.raises(ValueError): analysis.verify_local_evaluation(report,previous)
