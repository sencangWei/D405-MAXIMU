"""UMI-only new-factor residuals, separate from official trajectory scoring."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from align_mast3r_scale_with_stereo import load_trajectory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite diagnostic')
    baseline = ROOT / 'reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1'
    controls_path = ROOT / 'reports/metric_window_bundle_20260928/controls_ten_v6_joint_geometry/summary.json'
    controls = json.loads(controls_path.read_text())
    paths = [Path(__file__), controls_path]
    rows = []
    for case in controls['cases']:
        name = case['case']
        old_path = baseline / name / 'trajectory_graph.csv'
        new_path = args.candidate / name / 'trajectory_graph.csv'
        if not new_path.exists():
            rows.append(dict(case=name, diagnostic_available=False))
            continue
        paths += [old_path, new_path]
        old_times, old_pos, old_quat, _ = load_trajectory(old_path)
        new_times, new_pos, new_quat, _ = load_trajectory(new_path)
        if not np.array_equal(old_times, new_times):
            raise ValueError('candidate timestamps differ from baseline')
        old_rot, new_rot = Rotation.from_quat(old_quat), Rotation.from_quat(new_quat)
        windows = []
        for window in case['windows']:
            row = dict(window=window['window'], accepted=window['accepted'])
            if window['accepted']:
                i, j = window['indices'][0], window['indices'][-1]
                delta = np.asarray(window['endpoint_m'])
                old_delta = old_rot[i].inv().apply(old_pos[j] - old_pos[i])
                new_delta = new_rot[i].inv().apply(new_pos[j] - new_pos[i])
                row.update(first_index=i, second_index=j,
                    old_factor_residual_mm=float(1000 * np.linalg.norm(old_delta - delta)),
                    new_factor_residual_mm=float(1000 * np.linalg.norm(new_delta - delta)),
                    graph_delta_change_mm=float(1000 * np.linalg.norm(new_delta - old_delta)))
            windows.append(row)
        rows.append(dict(case=name, diagnostic_available=True, windows=windows,
                         graph_displacement_change_max_mm=float(1000 * np.max(np.linalg.norm(new_pos - old_pos, axis=1)))))
    args.output.write_text(json.dumps(dict(cases=rows, external_reference_used=False,
        input_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        interpretation='graph camera_i displacement residual, not whole trajectory ATE'), indent=2) + '\n')
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
