"""Frozen regression replay must not alter estimator/scoring parameters."""
import importlib.util
from pathlib import Path
import pytest
import json
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / '.planning/metric_window_bundle_20260928/run_graph_regression.py'


def module():
    spec = importlib.util.spec_from_file_location('metric_window_replay', PATH)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_replay_uses_validated_graph_not_original_stereo():
    runner = module()
    old = Path('/baseline/fresh2')
    new = Path('/candidate/fresh2')
    stages = [('graph', ['py', 'fuse_mast3r_stereo_imu.py', '--stereo-report', '/original.json']),
              ('smooth', ['py', 'smooth.py', '--input', str(old / 'raw.csv')]),
              ('score', ['py', 'score.py', '--estimate', str(old / 'final.csv'), '--capture', '/gt'])]
    validated = ['py', 'fuse_mast3r_stereo_imu.py', '--stereo-report', '/refined.json',
                 '--output', str(old / 'graph.csv'), '--expected-td-s', '-0.009109323']
    result = runner.replay_commands(stages, validated, old, new, Path('/controls.json'), 'fresh2')
    graph = result[0][1]
    assert graph[graph.index('--stereo-report') + 1] == '/refined.json'
    assert graph[graph.index('--output') + 1] == str(new / 'graph.csv')
    assert Path(graph[1]).name == 'fuse_mast3r_metric_windows.py'
    assert graph[graph.index('--expected-td-s') + 1] == '-0.009109323'
    assert graph[-4:] == ['--metric-window-controls', '/controls.json', '--metric-window-case', 'fresh2']
    assert result[-1][1][-1] == '/gt'
    assert result[-1][1][3] == str(new / 'final.csv')
    assert stages[0][1][-1] == '/original.json'


def test_remap_does_not_replace_prefix_sibling():
    runner = module()
    stages = [('score', ['py', 'score.py', '/baseline/fresh2_extra/ref.csv', '/baseline/fresh2/final.csv'])]
    result = runner.replay_commands(stages, [], Path('/baseline/fresh2'), Path('/candidate/fresh2'), Path('/c'), 'fresh2')
    assert result[0][1][2] == '/baseline/fresh2_extra/ref.csv'
    assert result[0][1][3] == '/candidate/fresh2/final.csv'


def test_postflight_detects_other_input_mutation(tmp_path):
    runner = module()
    extra = tmp_path / 'imu_input.bin'
    extra.write_bytes(b'frozen')
    hashes = {str(extra): runner.digest(extra)}
    runner.verify_hashes(hashes)
    extra.write_bytes(b'mutated')
    with pytest.raises(ValueError, match='Frozen source/input changed'):
        runner.verify_hashes(hashes)


def test_main_detects_input_mutation_by_subprocess(tmp_path, monkeypatch):
    runner = module()
    baseline = tmp_path / 'baseline'
    old = baseline / 'one'
    old.mkdir(parents=True)
    extra = tmp_path / 'imu.bin'
    extra.write_bytes(b'frozen')
    manifest = dict(source_sha256={}, input_sha256={str(extra): runner.digest(extra)},
                    commands=[['graph', ['python', 'native.py', '--output', str(old / 'graph.csv')]]])
    (old / 'manifest.json').write_text(json.dumps(manifest))
    (old / 'validated_graph_command.json').write_text(json.dumps(dict(command=manifest['commands'][0][1])))
    controls = tmp_path / 'controls.json'
    controls.write_text('{}')
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / 'fuse_mast3r_metric_windows.py').write_text('# fixture')
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'BASELINE', baseline)
    monkeypatch.setattr(runner, 'CONTROLS', controls)
    monkeypatch.setattr(runner, 'CASES', ['one'])
    monkeypatch.setattr('sys.argv', ['runner', '--output', str(tmp_path / 'new')])

    def mutate(*args, **kwargs):
        extra.write_bytes(b'mutated')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, 'run', mutate)
    with pytest.raises(ValueError, match='Frozen source/input changed'):
        runner.main()
