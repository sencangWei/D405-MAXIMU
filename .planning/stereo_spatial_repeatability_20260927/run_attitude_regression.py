"""One fixed attitude-alignment candidate, all ten cached cases."""
import importlib.util
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    spec = importlib.util.spec_from_file_location('attitude_regression', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    captures = ['20260927_181211_joint_scale_independent_take1_of4', '20260927_181447_joint_scale_independent_take2_of4',
                '20260927_181736_joint_scale_independent_take3_of4', '20260927_182002_joint_scale_independent_take4_of4']
    fresh = [(f'fresh{i}', f'joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}', capture)
             for i, capture in enumerate(captures, 1)]
    replay.CASES = [fresh[0]] + replay.CASES + fresh[1:]
    original_run = subprocess.run

    def candidate_run(command, **kwargs):
        if Path(command[1]).name == 'fuse_mast3r_stereo_imu.py':
            command = list(command)
            command[1] = str(Path(__file__).with_name('graph_attitude_proxy.py'))
        return original_run(command, **kwargs)

    replay.subprocess.run = candidate_run
    try:
        replay.main()
    finally:
        replay.subprocess.run = original_run


if __name__ == '__main__':
    main()
