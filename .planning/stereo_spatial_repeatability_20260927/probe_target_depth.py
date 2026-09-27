"""Same ten-case sampling; hold out target disparity from the free PnP fit."""
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
    base = module('frozen_depth_probe', ROOT / '.planning/stereo_factor_attribution_20260927/probe_raw_gyro_pnp.py')
    depth = module('target_depth', Path(__file__).with_name('target_depth.py'))
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
                expected_delta = np.asarray(result['metric_displacement_camera_i_m'])
                distances = [np.linalg.norm(-Rotation.from_rotvec(call[3].ravel()).inv().apply(call[4].ravel()) - expected_delta)
                             for call in pnp_calls]
                assert distances and min(distances) < 1e-8, 'No matching forward PnP'
                xyz, uv, k, rvec, tvec = pnp_calls[int(np.argmin(distances))]
                assert (Rotation.from_rotvec(rvec.ravel()).inv() * Rotation.from_quat(result['pnp_rotation_quaternion_xyzw'])).magnitude() < 1e-8
                disparity_left, disparity_right = loaded.stereo_disparity(args[2], args[3], args[9])
                valid, disparity = loaded.left_right_consistent(uv, disparity_left, disparity_right, 1.0)
                available = np.isfinite(disparity) & (disparity > .5)
                calibration = args[8]
                baseline_m = calibration['baseline_m']
                target_z = np.full(len(disparity), np.nan)
                target_z[available] = k[0, 0] * baseline_m / disparity[available]
                # Use the exact range accepted for source depths by this call.
                in_range = available & (target_z >= args[10]) & (target_z <= args[11])
                diagnostic = depth.target_depth_consistency(xyz, uv, k, rvec, tvec, disparity, valid & in_range, baseline_m)
                diagnostic.update(target_disparity_available=int(available.sum()),
                                  target_lr_consistent=int(valid.sum()), target_in_depth_range=int(in_range.sum()),
                                  target_lr_tolerance_px=1.0)
                result['target_depth_holdout'] = diagnostic
            return result

        loaded.estimate_pair_scale = observed_estimate
        return loaded

    base.load_module = observed_load
    try:
        base.main()
    finally:
        base.load_module = original_load


if __name__ == '__main__':
    main()
