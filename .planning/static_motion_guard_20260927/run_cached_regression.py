"""Replay only cached stages7–9; external reference is scoring-only afterwards."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path('/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python')
CASES = [
    ('dev1', 'steamvr_fusion_validation_20260927/run_1340/take1/fusion/baseline',
     '20260927_133525_steamvr_fusion_validation_take1'),
    ('dev2', 'steamvr_fusion_validation_20260927/metric_rescue_stage_fix/take2/rescue',
     '20260927_133741_steamvr_fusion_validation_take2'),
    *[(f'heldout{i}', f'steamvr_fusion_heldout_20260927/run_frozen_four/take{i}/fusion/baseline', capture)
      for i, capture in enumerate([
          '20260927_150519_steamvr_fusion_heldout_take3_moving_retry',
          '20260927_150812_steamvr_fusion_heldout_take4_of6',
          '20260927_150958_steamvr_fusion_heldout_take5_of6',
          '20260927_151138_steamvr_fusion_heldout_take6_of6'], start=1)],
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=[case[0] for case in CASES], action='append')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('Refuse to overwrite regression output')
    cases = [case for case in CASES if not args.case or case[0] in args.case]
    inputs = {}
    for name, cached, _ in cases:
        report = ROOT / 'reports' / cached / 'mast3r/graph_fusion_report.json'
        inputs[name] = json.loads(report.read_text())['inputs']
        for key, value in inputs[name].items():
            if key != 'external_ground_truth_used' and key not in {'session', 'keyframe_dir'}:
                for path in value if isinstance(value, list) else [value]:
                    if not Path(path).is_file():
                        raise FileNotFoundError(path)
    output.mkdir(parents=True)
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in [
        ROOT / 'scripts' / name for name in (
            'fuse_mast3r_stereo_imu.py', 'fuse_docker2_mast3r_complementary.py',
            'smooth_pose_trajectory.py', 'assess_mast3r_fusion_input_quality.py',
            'score_steamvr_slam.py', 'evaluate_slam_ground_truth.py')
    ]}
    for name, cached, capture in cases:
        target = output / name
        target.mkdir()
        inp = inputs[name]
        manifest = {'cached_inputs': inp, 'source_sha256': source_hashes,
                    'external_ground_truth_used_in_optimization': False,
                    'input_sha256': {}}
        for key, value in inp.items():
            if key == 'external_ground_truth_used' or key in {'session', 'keyframe_dir'}:
                continue
            for path in value if isinstance(value, list) else [value]:
                manifest['input_sha256'][path] = sha(path)
        graph = target / 'trajectory_graph.csv'
        graph_report = target / 'graph_fusion_report.json'
        command = [str(PYTHON), str(ROOT / 'scripts/fuse_mast3r_stereo_imu.py'),
            '--session', inp['session'], '--trajectory', inp['trajectory'],
            '--stream', 'infrared_left', '--stereo-report', inp['stereo_report'],
            '--imu-scale-report', inp['imu_scale_report'],
            '--vins-config', inp['vins_spatiotemporal_calibration'],
            '--imu-calibration', inp['imu_calibration'], '--expected-td-s', '-0.009109323',
            '--orientation-node-stride', '10', '--position-node-stride', '5',
            '--minimum-stereo-sample-hop', '1', '--keyframe-dir', inp['keyframe_dir'],
            '--relative-motion-trajectory', inp['relative_motion_trajectory'],
            '--relative-motion-report', inp['relative_motion_report'],
            '--relative-motion-sigma-m', '0.008', '--auto-visual-position-sigma',
            '--joint-max-correction-mm', '25', '--joint-correction-cap-mode', 'per-node',
            '--full-rate-imu-position-refinement', '--full-rate-max-correction-mm', '20',
            '--metric-scale-mode', 'joint', '--scale-disagreement-policy', 'diagnose',
            '--position-mode', 'keyframe-graph', '--output', str(graph), '--report', str(graph_report)]
        for report in inp['additional_stereo_reports']:
            command += ['--additional-stereo-report', report]
        unsmoothed = target / 'trajectory_fused_unsmoothed.csv'
        final = target / 'trajectory_fused.csv'
        commands = [('graph', command), ('complementary', [str(PYTHON),
            str(ROOT / 'scripts/fuse_docker2_mast3r_complementary.py'),
            '--mast3r', str(graph), '--docker2', inp['relative_motion_trajectory'],
            '--docker2-report', inp['relative_motion_report'],
            '--body-t-camera-yaml', inp['vins_spatiotemporal_calibration'],
            '--scale-horizon-s', '1', '--smoothing-s', '8', '--docker2-local-weight', '0',
            '--docker2-scale-weight', '0.25', '--graph-report', str(graph_report),
            '--roughness-threshold-mm', '9', '--adaptive-weight-strength', '0.45',
            '--output', str(unsmoothed), '--report', str(target / 'fusion_report.json')]),
            ('quality', [str(PYTHON), str(ROOT / 'scripts/assess_mast3r_fusion_input_quality.py'),
                '--graph-report', str(graph_report), '--fusion-report', str(target / 'fusion_report.json'),
                '--output', str(target / 'input_quality_report.json')]),
            ('smooth', [str(PYTHON), str(ROOT / 'scripts/smooth_pose_trajectory.py'),
                '--input', str(unsmoothed), '--output', str(final), '--method', 'gaussian',
                '--gaussian-sigma-s', '0.025', '--report', str(target / 'smoothing_report.json')]),
            ('score', [sys.executable, str(ROOT / 'scripts/score_steamvr_slam.py'),
                '--capture', str(ROOT / 'reports/steamvr_umi_sessions' / capture),
                '--estimate', str(final), '--query-time-domain', 'camera',
                '--output', str(target / 'official_score')])]
        manifest['commands'] = commands
        (target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        for stage, cmd in commands:
            print(f'{name} {stage} START', flush=True)
            with (target / f'{stage}.log').open('w') as log:
                result = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            print(f'{name} {stage} END rc={result.returncode}', flush=True)
            if result.returncode != 0 and not (stage in {'quality', 'score'} and result.returncode == 3):
                raise RuntimeError(f'{name} {stage} failed: see log')
        for path, expected in manifest['input_sha256'].items():
            assert sha(path) == expected, f'Input mutated: {path}'
        for path, expected in source_hashes.items():
            assert sha(ROOT / path) == expected, f'Source changed during batch: {path}'
    print('REGRESSION_FINISHED; inspect all scores, not a precision PASS claim', flush=True)


if __name__ == '__main__':
    main()
