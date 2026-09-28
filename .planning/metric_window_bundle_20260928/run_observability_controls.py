"""Frozen fullcoverage Jacobian instrumentation; no GT reads or selection."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_full_coverage_controls as coverage
import stereo_window_bundle as bundle
import stereo_window_observability as diagnostic


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output', type=Path, required=True)
    args, _ = parser.parse_known_args(argv)
    sources = [Path(__file__).resolve(), Path(diagnostic.__file__).resolve()]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    original = coverage.base.solve_stereo_window
    def solve(*args, **kwargs):
        return diagnostic.solve_with_observability(bundle, *args, **kwargs)
    coverage.base.solve_stereo_window = solve
    try:
        result = coverage.main(argv)
    finally:
        coverage.base.solve_stereo_window = original
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
               for p, h in hashes.items()), 'diagnostic source changed during census'
    summary = args.output/'summary.json'
    report = json.loads(summary.read_text())
    report['source_sha256'].update(hashes)
    report['interpretation'] = 'UMI-only optimized endpoint observability diagnostics, not trajectory accuracy or calibrated covariance'
    report['endpoint_observability_adapter'] = dict(
        diagnostic_only=True, source_sha256=hashes,
        external_reference_used=False, used_for_selection_or_graph_weights=False,
        noise_assumption='provisional normalized robust pixel/gyro/bias residuals',
        all_factors='nuisance-marginalized optimized robust Jacobian',
        pixel_rows_only='same solution and robust pixel weights; gyro/bias rows excluded')
    summary.write_text(json.dumps(report, indent=2)+'\n')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
