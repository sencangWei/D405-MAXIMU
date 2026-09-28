import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT/'.planning/metric_window_bundle_20260928/run_observability_controls.py'
spec = importlib.util.spec_from_file_location('run_observability_controls', PATH)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def test_adapter_only_adds_diagnostics_and_records_frozen_sources(tmp_path, monkeypatch):
    output = tmp_path/'census'
    original = adapter.coverage.base.solve_stereo_window
    seen = []
    def instrument(bundle, *args, **kwargs):
        seen.append((bundle, args, kwargs))
        return {'accepted': True}
    monkeypatch.setattr(adapter.diagnostic, 'solve_with_observability', instrument)
    def census(argv):
        assert argv == ['--output', str(output)]
        assert adapter.coverage.base.solve_stereo_window(1, marker=2) == {'accepted': True}
        output.mkdir()
        (output/'summary.json').write_text(json.dumps({'source_sha256':{}, 'external_reference_used':False}))
    monkeypatch.setattr(adapter.coverage, 'main', census)
    adapter.main(['--output', str(output)])
    assert adapter.coverage.base.solve_stereo_window is original
    assert seen == [(adapter.bundle, (1,), {'marker':2})]
    report = json.loads((output/'summary.json').read_text())
    assert report['endpoint_observability_adapter']['used_for_selection_or_graph_weights'] is False
    assert str(PATH) in report['source_sha256']
    assert str(Path(adapter.diagnostic.__file__).resolve()) in report['source_sha256']


def test_adapter_restores_harness_if_capture_fails(tmp_path, monkeypatch):
    original = adapter.coverage.base.solve_stereo_window
    def failure(argv):
        raise RuntimeError('capture fails')
    monkeypatch.setattr(adapter.coverage, 'main', failure)
    with pytest.raises(RuntimeError):
        adapter.main(['--output', str(tmp_path/'census')])
    assert adapter.coverage.base.solve_stereo_window is original
