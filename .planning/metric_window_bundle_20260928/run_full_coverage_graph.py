"""Same frozen all-ten graph replay, using all accepted uniform raw windows."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_graph_regression as base


def main():
    base.CONTROLS = base.ROOT / 'reports/metric_window_bundle_20260928/controls_full_ten_v1/summary.json'
    controls = json.loads(base.CONTROLS.read_text())
    names = [case['case'] for case in controls['cases']]
    if len(names) != 10 or set(names) != set(base.CASES):
        raise ValueError('Full-coverage controls must include all ten cases')
    if controls.get('full_coverage_adapter', {}).get('interval_frames') != 20:
        raise ValueError('Unexpected observation coverage contract')
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    base.main()
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != digest:
        raise ValueError('Coverage replay adapter changed during batch')
    # Source evidence belongs to output, never edits measurements or old reports.
    output = Path(sys.argv[sys.argv.index('--output') + 1])
    (output / 'coverage_replay_adapter.json').write_text(json.dumps(dict(
        source=str(Path(__file__).resolve()), source_sha256=digest,
        controls=str(base.CONTROLS), controls_sha256=base.digest(base.CONTROLS),
        old_parameters_unchanged=True, external_reference_used_in_estimation=False), indent=2) + '\n')


if __name__ == '__main__':
    main()
