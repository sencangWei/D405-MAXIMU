"""Enrich the frozen all-ten measurement probe; never rewrite input/trajectory.

Capture original forward RANSAC inliers, then deterministic delete-tile LM fits.
The prior free/MASt3R-fixed/gyro-fixed controls and sampling remain unchanged.
"""
import importlib.util
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    base = module('frozen_measurement_probe', ROOT / '.planning/stereo_factor_attribution_20260927/probe_raw_gyro_pnp.py')
    spatial = module('spatial_sensitivity', Path(__file__).with_name('spatial_pnp.py'))
    original_load = base.load_module

    def observed_load(name, path):
        loaded = original_load(name, path)
        if path.name != 'align_mast3r_scale_with_stereo.py':
            return loaded
        original_estimate = loaded.estimate_pair_scale

        def observed_estimate(*args, **kwargs):
            if kwargs.get('pnp_rotation_mode', 'free') != 'free':
                return original_estimate(*args, **kwargs)
            pnp_calls = []
            previous_pnp = cv2.solvePnPRansac

            def capture_pnp(xyz, uv, k, distortion, **pnp_kwargs):
                result = previous_pnp(xyz, uv, k, distortion, **pnp_kwargs)
                solved, rvec, tvec, inliers = result
                if solved and inliers is not None and len(inliers) >= 20:
                    index = inliers.ravel()
                    pnp_calls.append((xyz[index].copy(), uv[index].copy(), k.copy(), rvec.copy(), tvec.copy()))
                return result

            cv2.solvePnPRansac = capture_pnp
            try:
                result = original_estimate(*args, **kwargs)
            finally:
                cv2.solvePnPRansac = previous_pnp
            if result.get('accepted'):
                target = np.asarray(result['metric_displacement_camera_i_m'])
                assert pnp_calls, 'Accepted edge without captured free PnP'
                distances = [np.linalg.norm(spatial.displacement(record[3], record[4]) - target) for record in pnp_calls]
                closest = int(np.argmin(distances))
                assert distances[closest] < 1e-8, 'No matching forward PnP input set'
                xyz, uv, k, rvec, tvec = pnp_calls[closest]
                rotation_error = (Rotation.from_rotvec(rvec.ravel()).inv() *
                                  Rotation.from_quat(result['pnp_rotation_quaternion_xyzw'])).magnitude()
                assert rotation_error < 1e-8, 'Captured PnP rotation mismatch'
                height, width = args[0].shape
                result['spatial_repeatability'] = spatial.spatial_repeatability(xyz, uv, k, rvec, tvec, image_size=(width, height))
                result['spatial_repeatability']['captured_forward_pose_match_error_m'] = float(distances[closest])
            return result

        loaded.estimate_pair_scale = observed_estimate
        return loaded

    base.load_module = observed_load
    base.main()


if __name__ == '__main__':
    main()
