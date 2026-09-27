"""Full-coverage metric-window controls adapter; no GT or tuning changes."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_observation_controls as base


def windows(count):
    if count < 21:
        raise ValueError('recording too short for full-coverage controls')
    return [np.arange(start,start+21,5) for start in range(0,count-20,20)]


def main(argv=None):
    old_windows = base.windows
    base.windows = windows
    old_argv = sys.argv[:]
    if argv is not None:
        sys.argv = [str(Path(__file__).resolve())] + list(argv)
    try:
        result = base.main()
    finally:
        sys.argv = old_argv
        base.windows = old_windows
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output',type=Path,required=True)
    args,_ = parser.parse_known_args(argv)
    summary = args.output/'summary.json'
    data = json.loads(summary.read_text())
    adapter_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    data['source_sha256'][str(Path(__file__).resolve())] = adapter_hash
    counts = [len(case.get('windows',[])) for case in data.get('cases',[])]
    data['full_coverage_adapter'] = dict(
        source=str(Path(__file__).resolve()),
        source_sha256=adapter_hash,
        interval_frames=20,
        ba_node_offsets=[0,5,10,15,20],
        windows_per_case=counts,
        leftover_tail_policy='explicitly_uncovered_no_correlated_duplicate_edge',
        external_ground_truth_used=False,
        production_selection=False)
    summary.write_text(json.dumps(data,indent=2)+'\n')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
