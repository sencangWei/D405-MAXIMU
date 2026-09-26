"""Bounded offline validation; no device access or ground-truth tuning."""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/home/robot/ego_vio_humble')
OUT = Path(os.environ.get('PARTIAL_VALIDATION_OUT', str(Path(__file__).resolve().parent)))
OUT.mkdir(parents=True, exist_ok=True)
BUILD = Path('/tmp/libsurvive_prediction_diagnostic_20260927')
BASE_BUILD = Path('/tmp/libsurvive_fourway_candidate_20260926')
WORLD = ROOT / 'reports/lighthouse_gss_coverage_20260926_valid_v1/joint_optical_ba/candidate_nfev200.json'
CAPTURES = {
    'board_old': ROOT / 'reports/lighthouse_umi_sessions/20260927_023856_lighthouse_world_board_validation_retry',
    'board_new': ROOT / 'reports/lighthouse_umi_sessions/20260927_040615_lighthouse_orientation_control',
}
SOURCES = {name: capture / 'lighthouse_raw.rec' for name, capture in CAPTURES.items()}
SOURCES.update({take: ROOT / f'reports/lighthouse_gate_check_{take}/lighthouse_raw.rec'
                for take in ['gP8kSJ', 'ucf7GS', 'rAt98l']})
spec = importlib.util.spec_from_file_location('runner', ROOT / 'reports/lighthouse_gss_coverage_20260926_valid_v1/validate_three_new.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def validate_covariance_path(record, enabled):
    options = {}
    with record.open() as stream:
        for index, line in enumerate(stream):
            if index >= 2000:
                break
            f = line.split(maxsplit=4)
            if len(f) == 5 and f[1] == 'OPTION':
                options[f[2]] = f[4]
    assert float(options['mpfit-partial-observation-cov-scale']) == (16 if enabled else 0)
    assert float(options['mpfit-use-cov']) == 1
    assert float(options['kalman-obj-obs-adaptive']) == 0


def replay(task):
    take, enabled = task
    label = 'partial' if enabled else 'default_off'
    target = OUT / f'{take}_{label}'
    target.mkdir(exist_ok=False)
    config = target / 'runtime.json'
    config.write_bytes(WORLD.read_bytes())
    command = [str(BUILD / 'survive-cli'), '-c', str(config), '--playback', str(SOURCES[take]),
               '--playback-factor', '0', '--disable-calibrate', '--lighthouse-gen', '2',
               '--poser', 'MPFIT', '--disambiguator', 'StateBased', '--show-raw-obs',
               '--mpfit-record-reprojection-error', '1', '--record', str(target / 'validation.rec'),
               '--min-lighthouse-count', '2', '--min-measurements-per-lighthouse-axis', '1',
               '--mpfit-partial-observation-cov-scale', '16' if enabled else '0', '-v', '1']
    env = dict(os.environ, SURVIVE_PLUGINS=str(BUILD / 'plugins'), LD_LIBRARY_PATH=str(BUILD))
    with (target / 'replay.log').open('w') as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=150, check=True)
    record = target / 'validation.rec'
    validate_covariance_path(record, enabled)
    audit = runner.audit_module.audit(record)
    # Keep all original config/startup/finite/output guards. The ONLY amended
    # support rule is >=3 axes/2 stations when the new opt-in fallback is on.
    if enabled:
        groups = audit['solve_support_by_discontinuity']
        for group in groups.values():
            assert group['min'][2] >= 3 and group['min'][3] >= 2, group
        validation = dict(audit, solve_support_by_discontinuity={})
        runner.validate_replay(validation, record, config, 'fourway')
    else:
        runner.validate_replay(audit, record, config, 'fourway')
    events = 0
    with record.open() as stream:
        for line in stream:
            f = line.split()
            if len(f) >= 7 and f[1:3] == ['WM0', 'PARTIAL_SUPPORT']:
                assert int(f[3]) >= 3 and int(f[4]) >= 2 and float(f[6]) == 16
                events += 1
    assert enabled or events == 0
    audit.update(command=command, input_sha256=hashlib.sha256(SOURCES[take].read_bytes()).hexdigest(),
                 world_sha256=hashlib.sha256(WORLD.read_bytes()).hexdigest(), partial_events_wm0=events,
                 plugin_sha256=hashlib.sha256((BUILD / 'plugins/poser_mpfit.so').read_bytes()).hexdigest())
    (target / 'audit.json').write_text(json.dumps(audit, indent=2, allow_nan=False))
    summary = {'take': take, 'mode': label, 'partial_events_wm0': events,
               'invalid_values': audit['invalid_values'],
               'raw_jumps': sum(j['stream'] == 'WM0-raw-obs' for j in audit['recorded_pose_jumps']),
               'final_jumps': sum(j['stream'] == 'WM0' for j in audit['recorded_pose_jumps'])}
    print(json.dumps(summary), flush=True)
    return summary


def compare_boards():
    import numpy as np
    spec = importlib.util.spec_from_file_location('board', CAPTURES['board_old'] / 'board_validation/compare_fixed_board.py')
    board = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(board)
    calibration = json.loads(board.CALIBRATION.read_text())
    external = np.asarray(calibration['tracker_T_d405_left_camera'])
    report = {'external_fit': False, 'time_fit': False, 'scale_fit': False, 'captures': {}}
    sessions = {'board_old': '20260927_023859', 'board_new': '20260927_040620'}
    for take, capture in CAPTURES.items():
        board.CAPTURE = capture
        offset, _ = board.clock_offset(board.read_record(capture / 'lighthouse_raw.rec'))
        ct, truth, _ = board.load_camera_poses(capture / 'board_validation/aprilgrid_camera_poses.csv')
        session = Path('/home/robot/umi_ego_vio_data_device2_c48df736/recordings') / ('d405_720p_rgb_stereo_ir_' + sessions[take])
        host, _ = board.map_camera_times(ct, 'host_monotonic', session / 'd405_frames.csv')
        query = host + calibration['tracker_query_offset_ms'] / 1000
        paths = {'baseline': capture / 'board_validation/replay/board_joint_candidate_fourway/validation.rec',
                 'partial': OUT / f'{take}_partial/validation.rec'}
        if (OUT / f'{take}_default_off/validation.rec').exists():
            paths['default_off'] = OUT / f'{take}_default_off/validation.rec'
        samples, masks = {}, {}
        for name, path in paths.items():
            rows = board.read_record(path)
            t, p, q = board.clean_poses(rows[:, 0] + offset, rows[:, 1:4], rows[:, [5, 6, 7, 4]], path)
            poses, valid = board.interpolate_tracker(query, t, p, q, .030)
            full = np.full((len(ct), 4, 4), np.nan)
            full[valid] = poses @ external
            samples[name], masks[name] = full, valid
        # No candidate loss of baseline coverage is allowed.
        common = masks['baseline']
        assert all(np.all(valid[common]) for valid in masks.values()), 'Candidate lost baseline samples'
        r, tr = board.align(samples['baseline'][common, :3, 3], truth[common])
        result = {'samples': int(common.sum()), 'total_board_samples': len(ct),
                  'alignment': 'One SE3 fitted to baseline and applied unchanged to candidate', 'modes': {}}
        for name, full in samples.items():
            errors = np.linalg.norm(full[common, :3, 3] @ r.T + tr - truth[common], axis=1) * 1000
            result['modes'][name] = board.stats(errors)
        if 'default_off' in samples:
            a = board.read_record(paths['baseline']); b = board.read_record(paths['default_off'])
            result['default_off_pose_values_identical'] = bool(np.array_equal(a[:, 1:], b[:, 1:]))
            assert result['default_off_pose_values_identical'], 'Opt-out path changed'
        report['captures'][take] = result
    (OUT / 'board_precision.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    tasks = [('board_new', True), ('board_old', True), ('board_new', False)]
    tasks += [(take, True) for take in ['gP8kSJ', 'ucf7GS', 'rAt98l']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        summary = list(pool.map(replay, tasks))
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
    compare_boards()
