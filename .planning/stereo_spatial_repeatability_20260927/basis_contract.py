"""Compare onboard attitude bases only after aligning their world-frame gauge."""
import numpy as np


def compare_bases(mast_camera, vins_camera, position_world_alignment, local_deltas, first_indices):
    # A single constant rotation over all overlap frames; never per-edge fitting.
    attitude_world_alignment = (mast_camera * vins_camera.inv()).mean()
    aligned_position = position_world_alignment * vins_camera
    aligned_attitude = attitude_world_alignment * vins_camera
    current = mast_camera[first_indices].apply(local_deltas)
    result = {}
    for name, aligned in [('position_fit', aligned_position), ('attitude_fit', aligned_attitude)]:
        difference = current - aligned[first_indices].apply(local_deltas)
        result[name] = {
            'attitude_difference_deg': np.degrees((mast_camera[first_indices].inv() * aligned[first_indices]).magnitude()).tolist(),
            'projected_displacement_difference_mm': (np.linalg.norm(difference, axis=1) * 1000).tolist(),
            'world_difference_vector_mm': (difference * 1000).tolist(),
        }
    result['constant_alignment_difference_deg'] = float(np.degrees((position_world_alignment.inv() * attitude_world_alignment).magnitude()))
    result['attitude_fit_world_rotation_xyzw'] = attitude_world_alignment.as_quat().tolist()
    return result
