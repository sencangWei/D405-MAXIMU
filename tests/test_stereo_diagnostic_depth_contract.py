"""Diagnostic replay must not silently extend the formal stereo depth range."""
import importlib.util
from pathlib import Path


def probe():
    path = Path(__file__).resolve().parents[1] / '.planning/stereo_factor_attribution_20260927/probe_raw_gyro_pnp.py'
    spec = importlib.util.spec_from_file_location('depth_contract_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formal_depth_is_default():
    args = probe().argument_parser().parse_args(['--output', '/tmp/unused-depth-contract'])
    assert args.max_depth_m == 0.6


def test_legacy_diagnostic_requires_explicit_override():
    args = probe().argument_parser().parse_args([
        '--output', '/tmp/unused-depth-contract', '--max-depth-m', '1.5'])
    assert args.max_depth_m == 1.5


def test_refinement_candidate_only_changes_free_solver():
    path = Path(__file__).resolve().parents[1] / '.planning/stereo_spatial_repeatability_20260927/probe_free_refinement.py'
    spec = importlib.util.spec_from_file_location('refinement_contract_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return 'unchanged-return'

    assert module.refine_free_motion(original, 'same-points', pnp_rotation_mode='free') == 'unchanged-return'
    assert calls[-1] == (('same-points',), dict(pnp_rotation_mode='free', refine_pnp=True))
    module.refine_free_motion(original, 'same-points', pnp_rotation_mode='trajectory-fixed')
    assert calls[-1] == (('same-points',), dict(pnp_rotation_mode='trajectory-fixed'))
