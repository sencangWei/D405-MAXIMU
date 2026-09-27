"""Experimental constant-world alignment, using onboard attitudes, not GT."""
import numpy as np
from scipy.spatial.transform import Slerp


def align_positions_with_attitudes(query_times, target_positions, reference_times, reference_positions,
                                  target_camera_rotations, reference_body_rotations, body_from_camera):
    valid = (query_times >= reference_times[0]) & (query_times <= reference_times[-1])
    if np.count_nonzero(valid) < 3:
        raise ValueError('Insufficient attitude overlap')
    reference_camera = Slerp(reference_times, reference_body_rotations)(query_times[valid]) * body_from_camera
    rotation = (target_camera_rotations[valid] * reference_camera.inv()).mean()
    interpolated = np.column_stack([np.interp(query_times, reference_times, reference_positions[:, axis]) for axis in range(3)])
    # Both positions are body origins, just as in the production alignment.
    translation = np.mean(target_positions[valid] - rotation.apply(interpolated[valid]), axis=0)
    aligned = rotation.apply(interpolated) + translation
    residual = np.linalg.norm(aligned[valid] - target_positions[valid], axis=1)
    attitude_error = np.degrees((target_camera_rotations[valid].inv() * rotation * reference_camera).magnitude())
    return aligned, valid, dict(
        alignment='constant_onboard_attitude_world_rotation_with_body_translation',
        rotation=rotation.as_matrix().tolist(), translation_m=translation.tolist(),
        position_disagreement_median_m=float(np.median(residual)),
        position_disagreement_p95_m=float(np.percentile(residual, 95)),
        inlier_position_disagreement_median_m=float(np.median(residual)),
        inlier_position_disagreement_p95_m=float(np.percentile(residual, 95)),
        alignment_inliers=int(valid.sum()), alignment_outliers=0,
        overlap_samples=int(valid.sum()), overlap_ratio=float(np.mean(valid)),
        attitude_disagreement_median_deg=float(np.median(attitude_error)),
        attitude_disagreement_p95_deg=float(np.percentile(attitude_error, 95)),
        external_reference_used=False,
    )
