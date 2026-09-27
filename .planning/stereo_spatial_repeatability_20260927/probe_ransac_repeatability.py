"""Same166onboard pairs, fixed matches, vary only RANSAC seeds. Diagnostic only."""
import hashlib
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
    base = module('ransac_probe_base', ROOT / '.planning/stereo_factor_attribution_20260927/probe_raw_gyro_pnp.py')
    original_load = base.load_module

    def observed_load(name, path):
        loaded = original_load(name, path)
        if path.name != 'align_mast3r_scale_with_stereo.py':
            return loaded
        native_pnp = cv2.solvePnPRansac
        original_estimate = loaded.estimate_pair_scale

        def observed_estimate(*args, **kwargs):
            if kwargs.get('pnp_rotation_mode','free') != 'free':
                return original_estimate(*args, **kwargs)
            calls = []
            previous_pnp = cv2.solvePnPRansac

            def capture(xyz, uv, k, distortion, **pnp_kwargs):
                result = previous_pnp(xyz, uv, k, distortion, **pnp_kwargs)
                calls.append((xyz.copy(),uv.copy(),k.copy(),distortion,pnp_kwargs.copy()))
                return result

            cv2.solvePnPRansac = capture
            try:
                result = original_estimate(*args, **kwargs)
            finally:
                cv2.solvePnPRansac = previous_pnp
            if not calls:
                result['ransac_repeatability'] = dict(status='no_free_pnp_call', is_absolute_accuracy=False)
                return result
            # Only first forward solve; preserve all its original correspondences.
            xyz, uv, k, distortion, pnp_kwargs = calls[0]
            records = []
            for seed in range(10):
                cv2.setRNGSeed(seed)
                solved, rvec, tvec, inliers = native_pnp(xyz, uv, k, distortion, **pnp_kwargs)
                record = dict(seed=seed, solved=bool(solved))
                if solved and inliers is not None:
                    rotation = Rotation.from_rotvec(rvec.ravel())
                    delta = -rotation.inv().apply(tvec.ravel())
                    fitted = cv2.projectPoints(xyz[inliers.ravel()],rvec,tvec,k,distortion)[0].reshape(-1,2)
                    errors = np.linalg.norm(fitted-uv[inliers.ravel()],axis=1)
                    record.update(displacement_m=delta.tolist(), rotation_xyzw=rotation.as_quat().tolist(),
                                  inliers=int(len(inliers)), inlier_ratio=float(len(inliers)/len(xyz)),
                                  inlier_reprojection_median_px=float(np.median(errors)))
                records.append(record)
            solved_records = [r for r in records if 'displacement_m' in r]
            diagnostic = dict(status='measured', is_absolute_accuracy=False, input_points=len(xyz),
                              input_sha256=hashlib.sha256(xyz.tobytes()+uv.tobytes()).hexdigest(),
                              solver_kwargs=pnp_kwargs, seeds=records)
            if len(solved_records)>1:
                deltas = np.array([r['displacement_m'] for r in solved_records])
                rotations = Rotation.from_quat([r['rotation_xyzw'] for r in solved_records])
                diagnostic['translation_diameter_mm'] = float(np.max(np.linalg.norm(deltas[:,None]-deltas[None,:],axis=2))*1000)
                diagnostic['rotation_diameter_deg'] = max(float(np.degrees((rotation.inv()*rotations).magnitude()).max()) for rotation in rotations)
            result['ransac_repeatability'] = diagnostic
            return result

        loaded.estimate_pair_scale = observed_estimate
        return loaded

    base.load_module = observed_load
    try:
        base.main()
    finally:
        base.load_module = original_load


if __name__=='__main__':
    main()
