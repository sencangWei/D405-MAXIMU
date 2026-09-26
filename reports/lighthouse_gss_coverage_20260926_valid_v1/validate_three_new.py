"""Offline paired replay; never opens hardware or installs calibration."""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/home/robot/ego_vio_humble')
OUT = ROOT / 'reports/lighthouse_gss_coverage_20260926_valid_v1/three_new_validation_v2'
BUILD = Path('/tmp/libsurvive_fourway_candidate_20260926')
spec = importlib.util.spec_from_file_location('audit_optics', ROOT / 'reports/lighthouse_dual_conflict_20260926/audit_optics.py')
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)
TAKES = ['gP8kSJ', 'ucf7GS', 'rAt98l']
WORLDS = {
    'formal': ROOT / 'reports/lighthouse_recalibration_world_v13_20260926_consensus/libsurvive_config_frozen_v13.json',
    'joint_candidate': ROOT / 'reports/lighthouse_gss_coverage_20260926_valid_v1/joint_optical_ba/candidate_nfev200.json',
}


def validate_replay(report, record, config, gate):
    options = {}
    initial_station_ids = set()
    tracker_added = False
    with record.open() as stream:
        for index, line in enumerate(stream):
            if index >= 2000:
                break
            fields = line.strip().split(maxsplit=4)
            if len(fields) == 5 and fields[1] == 'OPTION':
                options[fields[2]] = fields[4]
            pose_fields = line.split()
            if len(pose_fields) >= 3 and pose_fields[1:3] == ['WM0', 'CONFIG']:
                tracker_added = True
            if (len(pose_fields) >= 11 and pose_fields[2] == 'LH_POSE'
                    and not tracker_added):
                initial_station_ids.add(pose_fields[-1])
    # libsurvive records option strings through a 128-byte buffer; runtime
    # loading uses the full path. Check the recorded prefix and startup poses.
    expected = {'configfile': str(config)[:127], 'disable-calibrate': '1',
                'min-lighthouse-count': '2' if gate == 'fourway' else '0',
                'min-measurements-per-lighthouse-axis': '1',
                'mpfit-record-reprojection-error': '1.000000'}
    for key, value in expected.items():
        actual = options.get(key)
        if key == 'mpfit-record-reprojection-error' and actual is not None:
            if float(actual) == 1:
                continue
        if actual != value:
            raise RuntimeError(f'{record}: {key}={actual!r}, expected {value!r}')
    if initial_station_ids != {'2640831677', '1850292303'}:
        raise RuntimeError(f'{record}: frozen station poses were not loaded at startup')
    if report['event_counts'].get('EXTERNAL_POSE', 0) == 0:
        raise RuntimeError(f'{record}: no optical solves; replay is not a valid comparison')
    if report['event_counts'].get('RA', 0) == 0:
        raise RuntimeError(f'{record}: no per-solve observations; support cannot be validated')
    if gate == 'fourway':
        for group in report['solve_support_by_discontinuity'].values():
            if group['min'][2:] != [4, 2]:
                raise RuntimeError(f'{record}: an accepted solve lacks four-way support')


def replay(task, source=None, extra_args=()):
    take, world, gate = task
    source = source or ROOT / f'reports/lighthouse_gate_check_{take}/lighthouse_raw.rec'
    target = OUT / f'{take}_{world}_{gate}'
    target.mkdir(parents=True, exist_ok=False)
    config = target / 'runtime.json'
    config.write_bytes(WORLDS[world].read_bytes())
    command = [str(BUILD / 'survive-cli'), '-c', str(config),
               '--playback', str(source), '--playback-factor', '0',
               '--disable-calibrate', '--lighthouse-gen', '2',
               '--poser', 'MPFIT', '--disambiguator', 'StateBased',
               '--show-raw-obs', '--mpfit-record-reprojection-error', '1',
               '--record', str(target / 'validation.rec'),
               '--min-lighthouse-count', '2' if gate == 'fourway' else '0',
               '--min-measurements-per-lighthouse-axis', '1', '-v', '1', *extra_args]
    env = dict(os.environ, SURVIVE_PLUGINS=str(BUILD / 'plugins'), LD_LIBRARY_PATH=str(BUILD))
    with (target / 'replay.log').open('w') as log:
        result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=150)
    if result.returncode != 0:
        raise RuntimeError(f'{target}: replay rc={result.returncode}')
    record = target / 'validation.rec'
    report = audit_module.audit(record)
    report['command'] = command
    report['world_sha256'] = hashlib.sha256(WORLDS[world].read_bytes()).hexdigest()
    report['input_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    (target / 'audit.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    validate_replay(report, record, config, gate)
    jumps = report['recorded_pose_jumps']
    summary = {'take': take, 'world': world, 'gate': gate,
               'raw_solves': report['event_counts'].get('EXTERNAL_POSE', 0),
               'final_poses': report['event_counts'].get('POSE', 0),
               'raw_jumps': sum(j['stream'] == 'WM0-raw-obs' for j in jumps),
               'final_jumps': sum(j['stream'] == 'WM0' for j in jumps),
               'support': report['solve_support_by_discontinuity'],
               'invalid_values': report['invalid_values']}
    print(json.dumps(summary), flush=True)
    return summary


if __name__ == '__main__':
    tasks = [(t, w, g) for t in TAKES for w in WORLDS for g in ['no_gate', 'fourway']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        summaries = list(pool.map(replay, tasks))
    (OUT / 'summary.json').write_text(json.dumps(summaries, indent=2))
