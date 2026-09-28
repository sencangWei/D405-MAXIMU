"""Summarize frozen all-ten controls and optional evaluation; never select."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def stats(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return dict(count=0, mean_mm=None, median_mm=None, p95_mm=None, max_mm=None)
    if not np.isfinite(values).all():
        raise ValueError('nonfinite evaluation')
    return dict(count=len(values), mean_mm=float(values.mean()), median_mm=float(np.median(values)),
                p95_mm=float(np.quantile(values, .95)), max_mm=float(values.max()))


def compare(first, second, first_score, second_score):
    if first['external_reference_used'] or second['external_reference_used']:
        raise ValueError('estimation used external reference')
    a = {c['case']: c for c in first['cases']}
    b = {c['case']: c for c in second['cases']}
    if len(a) != 10 or set(a) != set(b) or len(first['cases']) != 10 or len(second['cases']) != 10:
        raise ValueError('same exact ten unique cases required')
    sa = {c['case']: c for c in first_score['cases']}
    sb = {c['case']: c for c in second_score['cases']}
    if set(sa) != set(a) or set(sb) != set(a):
        raise ValueError('evaluation cases differ')
    rows, pooled_a, pooled_b = [], [], []
    independent_keys = ('indices', 'accepted', 'reason', 'initial_endpoint_m', 'endpoint_m')
    for name in a:
        ca, cb = a[name], b[name]
        if ca['input_sha256'] != cb['input_sha256'] or ca['decoded_grayscale_frame_sha256'] != cb['decoded_grayscale_frame_sha256']:
            raise ValueError('recording/calibration/decoded inputs changed:'+name)
        wa = {r['window']: r for r in ca['windows']}
        wb = {r['window']: r for r in cb['windows']}
        if len(wa) != 10 or set(wa) != set(wb):
            raise ValueError('ten matched endpoints required:'+name)
        if any(wa[w]['indices'] != wb[w]['indices'] for w in wa):
            raise ValueError('time schedule changed:'+name)
        ia = [{k: r.get(k) for k in independent_keys} for r in ca['independent_windows']]
        ib = [{k: r.get(k) for k in independent_keys} for r in cb['independent_windows']]
        if ia != ib:
            raise ValueError('independent control changed:'+name)
        ea = {r['window']: r for r in sa[name]['windows'] if r.get('scored')}
        eb = {r['window']: r for r in sb[name]['windows'] if r.get('scored')}
        common = sorted(set(ea) & set(eb))
        va = [ea[w]['optimized_local_error_mm'] for w in common]
        vb = [eb[w]['optimized_local_error_mm'] for w in common]
        pooled_a.extend(va)
        pooled_b.extend(vb)
        rows.append(dict(case=name, previous_accepted=sum(r['accepted'] for r in wa.values()),
            candidate_accepted=sum(r['accepted'] for r in wb.values()),
            independent_accepted=sum(r['accepted'] for r in cb['independent_windows']),
            previous_all_scored=stats([r['optimized_local_error_mm'] for r in ea.values()]),
            candidate_all_scored=stats([r['optimized_local_error_mm'] for r in eb.values()]),
            paired_previous=stats(va), paired_candidate=stats(vb),
            paired_improved=sum(y < x for x, y in zip(va, vb)), independent_control_exact=True))
    return dict(cases=rows, paired_previous=stats(pooled_a), paired_candidate=stats(pooled_b),
        paired_improved=sum(y < x for x, y in zip(pooled_a, pooled_b)),
        previous_accepted=sum(r['previous_accepted'] for r in rows),
        candidate_accepted=sum(r['candidate_accepted'] for r in rows),
        independent_accepted=sum(r['independent_accepted'] for r in rows),
        warning='local displacements, NOT full-trajectory ATE; refusals retained; no selection or calibrated confidence')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('refuse to overwrite comparison')
    paths = [args.previous/'summary.json', args.candidate/'summary.json',
             args.previous/'local_joint_evaluation.json', args.candidate/'local_joint_evaluation.json']
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths+[Path(__file__)]}
    result = compare(*(json.loads(p.read_text()) for p in paths))
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
        raise ValueError('frozen comparison input changed')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({**result, 'source_sha256': hashes}, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, indent=2), flush=True)


if __name__ == '__main__':
    main()
