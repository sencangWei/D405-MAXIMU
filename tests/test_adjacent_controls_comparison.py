import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('adjacent_compare',
    ROOT/'.planning/metric_window_bundle_20260928/compare_adjacent_controls.py')
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


def fixture():
    cases, score = [], []
    for c in range(10):
        windows = [dict(window=w, indices=[w, w+1], accepted=True, reason='ok',
                        initial_endpoint_m=[0., 0., 0.], endpoint_m=[.1, 0., 0.]) for w in range(1, 11)]
        cases.append(dict(case=str(c), windows=windows, independent_windows=copy.deepcopy(windows),
                          input_sha256={'recording': 'frozen'}, decoded_grayscale_frame_sha256={'left': 'frozen'}))
        score.append(dict(case=str(c), windows=[dict(window=w, scored=True, optimized_local_error_mm=1.) for w in range(1, 11)]))
    controls = dict(cases=cases, external_reference_used=False)
    evaluation = dict(cases=score)
    return controls, copy.deepcopy(controls), evaluation, copy.deepcopy(evaluation)


def test_refusals_remain_in_coverage_and_only_same_windows_are_paired():
    a, b, sa, sb = fixture()
    b['cases'][0]['windows'][0]['accepted'] = False
    sb['cases'][0]['windows'][0]['scored'] = False
    sb['cases'][0]['windows'][1]['optimized_local_error_mm'] = .5
    result = compare.compare(a, b, sa, sb)
    assert result['previous_accepted'] == 100 and result['candidate_accepted'] == 99
    assert result['independent_accepted'] == 100
    assert result['paired_candidate']['count'] == 99
    assert result['paired_improved'] == 1


@pytest.mark.parametrize('fault', ['case', 'schedule', 'input', 'independent', 'reference'])
def test_changed_inputs_controls_or_scope_fail_closed(fault):
    a, b, sa, sb = fixture()
    if fault == 'case':
        b['cases'].pop()
    elif fault == 'schedule':
        b['cases'][0]['windows'][0]['indices'] = [99, 100]
    elif fault == 'input':
        b['cases'][0]['input_sha256']['recording'] = 'changed'
    elif fault == 'independent':
        b['cases'][0]['independent_windows'][0]['endpoint_m'][0] = 99.
    else:
        b['external_reference_used'] = True
    with pytest.raises(ValueError):
        compare.compare(a, b, sa, sb)


def test_empty_stats_are_explicit_not_zero_error():
    assert compare.stats([])['max_mm'] is None
    with pytest.raises(ValueError, match='nonfinite'):
        compare.stats([float('nan')])
