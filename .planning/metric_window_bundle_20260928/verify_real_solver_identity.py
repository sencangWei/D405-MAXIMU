"""Uniform first-window real paired proof in one runtime; no GT inputs."""
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import scipy

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
import run_observation_controls as base
from run_full_coverage_controls import windows
import stereo_window_bundle as bundle
from stereo_window_observability import solve_with_observability


def paired(*args, **kwargs):
    observed = solve_with_observability(bundle, *args, **kwargs)
    original = bundle.solve_stereo_window(*args, **kwargs)
    if (observed['accepted'],observed['reason']) != (original['accepted'],original['reason']):
        raise ValueError('instrumentation changed acceptance/reason')
    if 'diagnostics' not in observed:
        return observed
    proof = dict(accepted_reason_equal=True, original={}, instrumented={})
    for name, result in [('original',original),('instrumented',observed)]:
        proof[name] = {k:result['diagnostics'][k] for k in ('nfev','cost','solver_status')}
        proof[name]['endpoint_m'] = result['centers'][-1].tolist()
    for key in ('centers','landmarks','gyro_bias'):
        proof[key+'_bit_equal'] = bool(np.array_equal(original[key],observed[key]))
        proof[key+'_max_absolute_difference'] = float(np.max(np.abs(original[key]-observed[key])))
        if not proof[key+'_bit_equal']:
            raise ValueError('instrumentation changed '+key+' within one runtime')
    observed['diagnostics']['same_input_paired_proof'] = proof
    return observed


def main():
    original_windows,original_solver = base.windows,base.solve_stereo_window
    base.windows = lambda count:[windows(count)[0]]
    base.solve_stereo_window = paired
    try:
        base.main()
    finally:
        base.windows,base.solve_stereo_window = original_windows,original_solver
    import argparse
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output',type=Path,required=True)
    args,_=parser.parse_known_args()
    path=args.output/'summary.json'
    report=json.loads(path.read_text())
    report['source_sha256'][str(Path(__file__).resolve())]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['runtime']=dict(executable=sys.executable,numpy=np.__version__,
        scipy=scipy.__version__,opencv=cv2.__version__,python=sys.version)
    report['interpretation']='Same-input original vs instrumented solver proof; uniform first window per recording, not accuracy'
    path.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    main()
