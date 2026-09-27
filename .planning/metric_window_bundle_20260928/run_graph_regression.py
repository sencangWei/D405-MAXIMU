"""Replay all ten frozen SIFT-LM/gyro candidates with new window factors only.

No source recording/report is rewritten. Estimation completes before the
unchanged official scoring command reads external reference. No case-specific
parameters, candidate selection, new smoothing, or scale/weight sweeps.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / 'reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1'
CONTROLS = ROOT / 'reports/metric_window_bundle_20260928/controls_ten_v6_joint_geometry/summary.json'
CASES = ['fresh1', 'dev1', 'dev2', 'heldout1', 'heldout2', 'heldout3', 'heldout4', 'fresh2', 'fresh3', 'fresh4']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_hashes(hashes):
    for path, expected in hashes.items():
        if digest(path) != expected:
            raise ValueError(f'Frozen source/input changed: {path}')


def replay_commands(stages, validated_graph, old, new, controls, case):
    result = []
    for stage, command in stages:
        command = list(validated_graph if stage == 'graph' else command)
        command = [str(new) + value[len(str(old)):] if value.startswith(str(old) + '/') else value
                   for value in command]
        if stage == 'graph':
            command[1] = str(ROOT / 'scripts/fuse_mast3r_metric_windows.py')
            command += ['--metric-window-controls', str(controls), '--metric-window-case', case]
        result.append((stage, command))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('Refuse to overwrite existing regression output')
    # Preflight every case before starting one; reuse EXACT frozen graph commands.
    entries = {}
    for case in CASES:
        old = BASELINE / case
        manifest_path = old / 'manifest.json'
        command_path = old / 'validated_graph_command.json'
        manifest = json.loads(manifest_path.read_text())
        for path, expected in manifest['source_sha256'].items():
            if digest(ROOT / path) != expected:
                raise ValueError(f'Baseline source changed: {path}')
        for path, expected in manifest['input_sha256'].items():
            if digest(path) != expected:
                raise ValueError(f'Baseline input changed: {path}')
        command = json.loads(command_path.read_text())['command']
        hashes = {str(manifest_path): digest(manifest_path), str(command_path): digest(command_path)}
        hashes.update(manifest['input_sha256'])
        hashes.update({str(ROOT / path): expected for path, expected in manifest['source_sha256'].items()})
        for flag in ['--stereo-report', '--additional-stereo-report']:
            for index, value in enumerate(command):
                if value == flag:
                    path = command[index + 1]
                    hashes[path] = digest(path)
        entries[case] = (manifest, command, hashes)
    source_paths = [Path(__file__), ROOT / 'scripts/fuse_mast3r_metric_windows.py', CONTROLS]
    source_hashes = {str(path): digest(path) for path in source_paths}
    output.mkdir(parents=True)
    results = []
    for case in CASES:
        started = time.monotonic()
        target = output / case
        target.mkdir()
        manifest, graph_command, hashes = entries[case]
        commands = replay_commands(manifest['commands'], graph_command, BASELINE / case, target, CONTROLS, case)
        record = dict(case=case, baseline=str(BASELINE / case), controls=str(CONTROLS),
                      input_sha256=hashes, source_sha256=source_hashes,
                      commands=commands, external_reference_used_in_optimization=False,
                      window_sigma_m=.004, window_sigma_is_calibrated=False,
                      policy='accepted uniform v6 endpoints; fixed native sigma; all original graph parameters unchanged')
        (target / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
        status = dict(case=case, completed=True, stages=[])
        for stage, command in commands:
            print(f'{case} {stage} START', flush=True)
            with (target / f'{stage}.log').open('w') as log:
                run = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            status['stages'].append(dict(stage=stage, returncode=run.returncode))
            print(f'{case} {stage} END rc={run.returncode}', flush=True)
            if run.returncode != 0 and not (stage in {'quality', 'score'} and run.returncode == 3):
                status.update(completed=False, failure_stage=stage)
                break  # retain failure; still evaluate other independent cases
        verify_hashes(hashes)
        verify_hashes(source_hashes)
        status['runtime_s'] = time.monotonic() - started
        results.append(status)
        (output / 'batch_status.json').write_text(json.dumps(dict(cases=results, precision_pass_claimed=False), indent=2) + '\n')
    print('ALL_TEN_FINISHED; read scores before any acceptance claim', flush=True)


if __name__ == '__main__':
    main()
