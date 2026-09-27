"""Opt-in experimental graph entry; existing production file untouched."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    fusion = module('attitude_graph', ROOT / 'scripts/fuse_mast3r_stereo_imu.py')
    helper = module('attitude_alignment', Path(__file__).with_name('attitude_alignment.py'))
    def option(name):
        return Path(sys.argv[sys.argv.index(name) + 1])
    _, _, reference_rotations, _ = fusion.load_trajectory(option('--relative-motion-trajectory'))
    config = fusion.load_vins_config(option('--vins-config'), -0.009109323)
    stereo = fusion.load_json_report(option('--stereo-report'))
    ext = fusion.body_t_trajectory_camera_from_stereo_report(config['body_T_camera'], stereo)
    body_from_camera = fusion.Rotation.from_matrix(ext[:3, :3])
    original_refine = fusion.refine_orientations
    original_align = fusion.align_relative_motion_positions
    state = {}

    def observed_refine(*args, **kwargs):
        result = original_refine(*args, **kwargs)
        state['camera_rotations'] = result[0]
        return result

    def changed_align(query_times, target_positions, reference_times, reference_positions):
        return helper.align_positions_with_attitudes(query_times, target_positions, reference_times, reference_positions,
                                                     state['camera_rotations'], reference_rotations, body_from_camera)

    fusion.refine_orientations = observed_refine
    fusion.align_relative_motion_positions = changed_align
    try:
        return fusion.main()
    finally:
        fusion.refine_orientations = original_refine
        fusion.align_relative_motion_positions = original_align


if __name__ == '__main__':
    raise SystemExit(main())
