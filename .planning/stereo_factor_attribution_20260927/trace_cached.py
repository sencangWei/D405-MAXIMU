"""Observe unchanged cached graph solves; no external reference enters tracing."""
import argparse
import importlib.util
import json
from pathlib import Path
import runpy
import subprocess
import sys

import numpy as np
from scipy.sparse.linalg import lsqr

ROOT = Path(__file__).resolve().parents[2]


def trace_graph(command, output):
    captured = {}

    def profile(frame, event, arg):
        if event != 'return' or frame.f_code.co_filename != str(ROOT / 'scripts/fuse_mast3r_stereo_imu.py'):
            return
        name = frame.f_code.co_name
        if name == 'refine_positions_visual_inertial':
            loc = frame.f_locals
            captured['joint_input'] = loc['positions'].copy()
            captured['joint_output'] = arg[0].copy()
            captured['body_rotation_xyzw'] = loc['body_rotations'].as_quat()
            captured['times_mono'] = loc['visual_times_mono'].copy()
            captured['node_indices'] = loc['node_indices'].copy()
            captured['system'] = (loc['design'].copy(), loc['target'].copy(), loc['solution'].copy())
            captured['layout'] = {
                key: int(loc[key]) for key in ('node_count', 'velocity_offset', 'scale_offset')
            }
            captured['layout'].update(relative_rows=3 * len(loc['relative_motion_weights']),
                                      stereo_rows=3 * len(loc['accepted']),
                                      static_rows=3 * len(loc['static_pairs']),
                                      scale_active=bool(loc['scale_active']))
            captured['scale_basis'] = loc['scale_basis'].copy()
            captured['bias'] = loc['bias'].copy()
            captured['frame_left'] = loc['frame_left'].copy()
            captured['frame_right'] = loc['frame_right'].copy()
            captured['frame_alpha'] = loc['frame_alpha'].copy()
        elif name == 'refine_positions_full_rate_imu':
            captured['full_input'] = frame.f_locals['camera_positions'].copy()
            captured['full_output'] = arg[0].copy()
        elif name == 'align_orientations_to_translation_frame':
            captured['final_rotation_xyzw'] = arg[0].as_quat()

    argv = sys.argv
    previous_profile = sys.getprofile()
    try:
        sys.argv = command[1:]
        sys.setprofile(profile)
        runpy.run_path(command[1], run_name='__main__')
    except SystemExit as exc:
        if exc.code not in (0, None, 3):
            raise
    finally:
        sys.setprofile(previous_profile)
        sys.argv = argv
    design, target, solution = captured.pop('system')
    layout = captured.pop('layout')
    n = layout['node_count']
    groups = [
        ('visual_prior', 3 * n), ('anchor', 3), ('smooth', 3 * max(n - 2, 0)),
        ('vins', layout['relative_rows']), ('imu', 6 * (n - 1)),
        ('stereo', layout['stereo_rows']), ('gravity', 3), ('bias', 3),
        ('stationary', layout['static_rows']),
    ]
    row = 0
    summed = np.zeros_like(solution)
    for name, count in groups:
        rhs = np.zeros_like(target)
        rhs[row:row + count] = target[row:row + count]
        contribution = lsqr(design, rhs, atol=1e-10, btol=1e-10, iter_lim=5000)[0]
        summed += contribution
        nodes = contribution[:layout['velocity_offset']].reshape(-1, 3)
        alpha = captured['frame_alpha'][:, None]
        frames = (1 - alpha) * nodes[captured['frame_left']] + alpha * nodes[captured['frame_right']]
        if layout['scale_active']:
            frames += contribution[layout['scale_offset']] * captured['scale_basis']
        captured[f'factor_{name}'] = frames
        row += count
    assert row == len(target), (row, len(target))
    consistency = {'fixed_irls_decomposition': True, 'not_factor_removal_ablation': True,
                   'normal_system_rows': row, 'unknowns': len(solution),
                   'sum_solution_difference_max': float(np.max(np.abs(summed - solution))),
                   'external_reference_used': False, 'layout': layout}
    np.savez_compressed(output / 'trace.npz', **captured)
    (output / 'trace_manifest.json').write_text(json.dumps(consistency, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', action='append', choices=['fresh1', 'fresh2', 'fresh3', 'fresh4'])
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('cached_replay', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    captures = ['20260927_181211_joint_scale_independent_take1_of4',
                '20260927_181447_joint_scale_independent_take2_of4',
                '20260927_181736_joint_scale_independent_take3_of4',
                '20260927_182002_joint_scale_independent_take4_of4']
    replay.CASES = [(f'fresh{i}', f'joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}', capture)
                    for i, capture in enumerate(captures, 1)]
    real_run = subprocess.run

    def instrumented_run(command, **kwargs):
        if Path(command[1]).name == 'fuse_mast3r_stereo_imu.py':
            output = Path(command[command.index('--output') + 1]).parent
            trace_graph(command, output)
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    replay.subprocess.run = instrumented_run
    sys.argv = [str(spec.origin), '--output', str(args.output)]
    for case in args.case or []:
        sys.argv.extend(['--case', case])
    replay.main()


if __name__ == '__main__':
    main()
