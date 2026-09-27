"""Evaluation-only localization of residuals; never feeds a SLAM estimator."""
import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def errors(evaluator, precision_path):
    precision = json.loads(precision_path.read_text())
    times, positions, rotations = evaluator.load_trajectory(Path(precision['estimate']))
    gt_times, gt_positions, gt_rotations = evaluator.load_trajectory(Path(precision['ground_truth']))
    inside, valid, reference, _ = evaluator.interpolate_ground_truth(
        times, gt_times, gt_positions, gt_rotations, precision['max_interpolation_gap_s'])
    positions, rotations = positions[inside][valid], rotations[inside][valid]
    alignment, offset = evaluator.rigid_align(positions, reference[:, 1:])
    aligned = positions @ alignment.T + offset
    residual = aligned - reference[:, 1:]
    distance_mm = np.linalg.norm(residual, axis=1) * 1000
    assert len(distance_mm) == precision['samples']
    assert abs(distance_mm.max() - precision['ate_translation_max_m'] * 1000) < 1e-7
    return reference[:, 0], aligned, rotations, reference[:, 1:], residual, distance_mm


def blocks(times, mask):
    indices = np.flatnonzero(mask)
    if not len(indices):
        return []
    return np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    evaluator = module(ROOT / 'scripts/evaluate_slam_ground_truth.py')
    comparison = json.loads((args.candidate / 'comparison.json').read_text())
    output = dict(scope='evaluation_only_no_estimator_input', cases=[])
    for row in comparison['cases']:
        if not row['complete']:
            continue
        name = row['case']
        baseline = (ROOT / f'reports/joint_scale_independent_four_20260927/take{name[-1]}/official_score/precision.json'
                    if name.startswith('fresh') else
                    ROOT / f'reports/joint_metric_scale_20260927/verified_v1_six/{name}/official_score/precision.json')
        old_t, old_p, _, old_gt, _, old_error = errors(evaluator, baseline)
        t, p, rotations, gt, residual, error = errors(evaluator, args.candidate / name / 'official_score/precision.json')
        assert np.array_equal(old_t, t) and np.array_equal(old_gt, gt), 'Changed evaluation reference'
        step = np.diff(t)
        speed = np.linalg.norm(np.diff(p, axis=0), axis=1) / step
        angular_speed = np.degrees((Rotation.from_quat(rotations[:-1]).inv()
                                   * Rotation.from_quat(rotations[1:])).magnitude()) / step
        intervals = []
        for indices in blocks(t, error > 10):
            start, end = int(indices[0]), int(indices[-1])
            peak = int(indices[np.argmax(error[indices])])
            steps = slice(max(0, start - 1), min(len(step), end + 1))
            intervals.append(dict(start_index=start, end_index=end, samples=len(indices),
                start_elapsed_s=float(t[start] - t[0]), end_elapsed_s=float(t[end] - t[0]),
                peak_index=peak, peak_mm=float(error[peak]),
                baseline_at_peak_mm=float(old_error[peak]),
                residual_at_peak_mm=(residual[peak] * 1000).tolist(),
                candidate_vs_baseline_aligned_at_peak_mm=float(np.linalg.norm(p[peak] - old_p[peak]) * 1000),
                slam_speed_median_m_s=float(np.median(speed[steps])),
                slam_angular_speed_median_deg_s=float(np.median(angular_speed[steps]))))
        peak = int(np.argmax(error))
        output['cases'].append(dict(case=name, baseline_max_mm=float(old_error.max()),
            candidate_max_mm=float(error.max()), candidate_peak_index=peak,
            candidate_peak_elapsed_s=float(t[peak] - t[0]),
            baseline_over_10mm_samples=int(np.sum(old_error > 10)),
            candidate_over_10mm_samples=int(np.sum(error > 10)),
            over_10mm_blocks=intervals))
    (args.candidate / 'residual_localization.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
