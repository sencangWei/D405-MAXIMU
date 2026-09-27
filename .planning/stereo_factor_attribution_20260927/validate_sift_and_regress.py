"""One fixed bidirectional SIFT contract on ten cached cases; no GT tuning.

Global scales, existing LK edges, VINS inputs, and all source trajectories are
frozen. Re-estimate only missing reverse SIFT measurements, then stages7–9.
"""
import argparse
import hashlib
import importlib.util
import json
from collections import OrderedDict
from pathlib import Path
import subprocess
import sys
import time

import cv2
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def validate_reports(stereo, cached, target):
    graph = json.loads((ROOT / 'reports' / cached / 'mast3r/graph_fusion_report.json').read_text())
    inputs = graph['inputs']
    sources = [inputs['stereo_report']] + inputs['additional_stereo_reports']
    originals = [json.loads(Path(path).read_text()) for path in sources]
    trajectory_path = Path(originals[0]['trajectory'])
    times, positions, quaternions, _ = stereo.load_trajectory(trajectory_path)
    rotations = Rotation.from_quat(quaternions)
    calibration = dict(originals[0]['factory_stereo_calibration'])
    calibration['left'] = calibration['left_intrinsics']
    calibration['right'] = calibration['right_intrinsics']
    frame_csv = Path(inputs['session']) / 'd405_frames.csv'
    left_numbers, right_numbers, _ = stereo.match_trajectory_to_stereo_frames(frame_csv, times, trajectory_frame='infrared_left')
    edges = [edge for report in originals for edge in report['observations']
             if edge.get('accepted') and edge.get('method') == 'sift' and 'bidirectional_relative_disagreement' not in edge]
    indices = {edge[key] for edge in edges for key in ('first_index', 'second_index')}
    left, right = stereo.load_selected_prepared_stereo_images(trajectory_path.parent / 'dataset', frame_csv,
                   {int(left_numbers[index]) for index in indices}, {int(right_numbers[index]) for index in indices})
    disparity_cache = OrderedDict()
    validation_cache = {}
    stats = {'attempted': 0, 'accepted': 0, 'rejections': {}, 'external_reference_used': False,
             'scale_policy': 'preserve original global scale to isolate displacement validation',
             'source_sha256': {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in sources}}
    target.mkdir(parents=True)
    rewritten = []
    for source, report in zip(sources, originals):
        updated = []
        for edge in report['observations']:
            if not (edge.get('accepted') and edge.get('method') == 'sift' and 'bidirectional_relative_disagreement' not in edge):
                updated.append(edge)
                continue
            first, second = edge['first_index'], edge['second_index']
            pair = (first, second)
            if pair not in validation_cache:
                if second not in disparity_cache:
                    disparity_cache[second] = stereo.stereo_disparity(left[int(left_numbers[second])], right[int(right_numbers[second])], 128)
                    if len(disparity_cache) > 24:
                        disparity_cache.popitem(last=False)
                disparity_cache.move_to_end(second)
                disparity_left, disparity_right = disparity_cache[second]
                cv2.setRNGSeed(0)
                reverse = stereo.estimate_sift_fallback(left[int(left_numbers[second])], left[int(left_numbers[first])],
                    disparity_left, disparity_right, positions[second], positions[first], rotations[second], rotations[first],
                    calibration, 0.07, 1.5, 'infrared_left', 'free')
                # First apply the existing scalar-scale acceptance contract,
                # then validate inverse vector direction. Keep forward scale
                # and vector untouched so the experiment changes only checks.
                scalar = stereo.combine_bidirectional_scale(edge, reverse)
                validated = stereo.validate_bidirectional_motion(edge, reverse)
                if not scalar.get('accepted'):
                    validated = dict(edge, accepted=False, reason=scalar['reason'],
                                     reverse_failure_reason=scalar.get('reverse_failure_reason'))
                else:
                    validated['bidirectional_relative_disagreement'] = scalar['bidirectional_relative_disagreement']
                validated['reverse_validation'] = reverse
                validation_cache[pair] = validated
                stats['attempted'] += 1
                if validated.get('accepted'):
                    stats['accepted'] += 1
                else:
                    reason = validated.get('reason', 'unknown')
                    stats['rejections'][reason] = stats['rejections'].get(reason, 0) + 1
                if stats['attempted'] % 25 == 0:
                    print(target.parent.name, 'SIFT', stats['attempted'], '/', len(edges), stats['rejections'], flush=True)
            updated.append(validation_cache[pair])
        report['observations'] = updated
        report['reverse_sift_validation'] = stats.copy()
        path = target / Path(source).name
        path.write_text(json.dumps(report, indent=2) + '\n')
        rewritten.append(str(path.resolve()))
    (target / 'validation_summary.json').write_text(json.dumps(stats, indent=2) + '\n')
    for path, expected in stats['source_sha256'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
    return rewritten


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', action='append')
    args = parser.parse_args()
    if args.output.exists() or args.output.with_name(args.output.name + '_measurements').exists():
        raise ValueError('Refuse to overwrite output')
    cv2.setNumThreads(2)
    stereo = module('stereo_validation', ROOT / 'scripts/align_mast3r_scale_with_stereo.py')
    replay = module('cached_regression', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    captures = ['20260927_181211_joint_scale_independent_take1_of4',
                '20260927_181447_joint_scale_independent_take2_of4',
                '20260927_181736_joint_scale_independent_take3_of4',
                '20260927_182002_joint_scale_independent_take4_of4']
    fresh = [(f'fresh{i}', f'joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}', capture)
             for i, capture in enumerate(captures, 1)]
    replay.CASES = [fresh[0]] + replay.CASES + fresh[1:]
    if args.case:
        replay.CASES = [case for case in replay.CASES if case[0] in args.case]
        if len(replay.CASES) != len(set(args.case)):
            raise ValueError('Unknown case')
    original_run = subprocess.run
    measurement_root = args.output.with_name(args.output.name + '_measurements')
    source_hash = hashlib.sha256((ROOT / 'scripts/align_mast3r_scale_with_stereo.py').read_bytes()).hexdigest()

    def run_with_validated_stereo(command, **kwargs):
        if Path(command[1]).name == 'fuse_mast3r_stereo_imu.py':
            case = Path(command[command.index('--output') + 1]).parent.name
            cached = next(cached for name, cached, _ in replay.CASES if name == case)
            start = time.monotonic()
            rewritten = validate_reports(stereo, cached, measurement_root / case / 'stereo')
            command = list(command)
            locations = [index + 1 for index, value in enumerate(command) if value in {'--stereo-report', '--additional-stereo-report'}]
            assert len(locations) == len(rewritten)
            for index, report in zip(locations, rewritten):
                command[index] = report
            target = Path(command[command.index('--output') + 1]).parent
            (target / 'validated_graph_command.json').write_text(json.dumps({'command': command, 'stereo_code_sha256': source_hash,
                'measurement_seconds': time.monotonic() - start, 'external_reference_used': False}, indent=2) + '\n')
            assert hashlib.sha256((ROOT / 'scripts/align_mast3r_scale_with_stereo.py').read_bytes()).hexdigest() == source_hash
        return original_run(command, **kwargs)

    replay.subprocess.run = run_with_validated_stereo
    sys.argv = [str(replay.__file__), '--output', str(args.output)]
    replay.main()


if __name__ == '__main__':
    main()
