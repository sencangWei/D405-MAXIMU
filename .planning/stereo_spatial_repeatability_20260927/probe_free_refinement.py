"""One uniform alternative estimator: refine free PnP on its original inliers.

Existing image inputs, formal depth range, RANSAC inliers and downstream gates
remain unchanged. No external reference is read and production stays untouched.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def refine_free_motion(original, *args, **kwargs):
    if kwargs.get('pnp_rotation_mode', 'free') == 'free':
        kwargs['refine_pnp'] = True
    return original(*args, **kwargs)


def main():
    path = ROOT / '.planning/stereo_factor_attribution_20260927/probe_raw_gyro_pnp.py'
    spec = importlib.util.spec_from_file_location('refinement_probe_base', path)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    original_load = base.load_module

    def observed_load(name, path):
        loaded = original_load(name, path)
        if path.name == 'align_mast3r_scale_with_stereo.py':
            original = loaded.estimate_motion_from_correspondences
            loaded.estimate_motion_from_correspondences = lambda *a, **k: refine_free_motion(original, *a, **k)
        return loaded

    base.load_module = observed_load
    try:
        base.main()
    finally:
        base.load_module = original_load


if __name__ == '__main__':
    main()
