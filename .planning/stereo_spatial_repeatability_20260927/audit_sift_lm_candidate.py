"""Audit isolated SIFT candidate scope and compare all ten full trajectories."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('sift_audit_cases', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    cached = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cached)
    cases = [(n, ROOT/'reports'/p/'mast3r/graph_fusion_report.json',
              ROOT/f'reports/joint_metric_scale_20260927/verified_v1_six/{n}/official_score/precision.json')
             for n, p, _ in cached.CASES]
    cases += [(f'fresh{i}', ROOT/f'reports/joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i==2 else "baseline"}/mast3r/graph_fusion_report.json',
               ROOT/f'reports/joint_scale_independent_four_20260927/take{i}/official_score/precision.json') for i in range(1, 5)]
    rows = []
    for name, graph_path, score_path in cases:
        before = json.loads(score_path.read_text())
        target = args.candidate/name
        precision = target/'official_score/precision.json'
        row = dict(case=name, complete=precision.exists(), baseline_max_mm=before['ate_translation_max_m']*1000)
        if precision.exists():
            after = json.loads(precision.read_text())
            old_graph = json.loads(graph_path.read_text())
            new_graph = json.loads((target/'graph_fusion_report.json').read_text())
            assert before['samples'] == after['samples'] and before['alignment'] == after['alignment']
            assert not new_graph['external_ground_truth_used'] and not new_graph['slam_supervision']
            assert old_graph['position_fusion_selection']['visual_position_sigma']['selected_sigma_m'] == new_graph['position_fusion_selection']['visual_position_sigma']['selected_sigma_m']
            old_paths = [old_graph['inputs']['stereo_report']] + old_graph['inputs']['additional_stereo_reports']
            new_paths = [new_graph['inputs']['stereo_report']] + new_graph['inputs']['additional_stereo_reports']
            assert len(old_paths) == len(new_paths)
            changed = 0
            for old_path, new_path in zip(old_paths, new_paths):
                old, new = json.loads(Path(old_path).read_text()), json.loads(Path(new_path).read_text())
                assert old['scale_m_per_mast3r_unit'] == new['scale_m_per_mast3r_unit']
                assert len(old['observations']) == len(new['observations'])
                policy = new['refinement_policy']
                assert policy['max_depth_m'] == .6 and policy['imu_td_s'] == -.009109323
                assert policy['pnp_refine_enabled'] and not policy['rotation_fixed'] and not policy['external_reference_used']
                for a, b in zip(old['observations'], new['observations']):
                    if not (a.get('accepted') and a.get('method') == 'sift'):
                        assert a == b, 'LK or original rejected observation changed'
                    else:
                        assert b['rotation_gate_reference'] == 'raw_calibrated_gyro'
                        changed += 1
            manifest = json.loads((target/'manifest.json').read_text())
            for path, digest in manifest['input_sha256'].items():
                assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
            row.update(result=after['result'], max_mm=after['ate_translation_max_m']*1000,
                       mean_mm=after['ate_translation_mean_m']*1000, p95_mm=after['ate_translation_p95_m']*1000,
                       within_10mm_ratio=after['ate_translation_within_10mm_ratio'], samples=after['samples'],
                       reestimated_sift_occurrences=changed)
        rows.append(row)
    result = dict(completed=sum(r['complete'] for r in rows), total=10,
                  pass_count=sum(r.get('result') == 'PASS' for r in rows), production_changed=False,
                  external_reference_used_in_optimization=False, cases=rows)
    (args.candidate/'comparison.json').write_text(json.dumps(result, indent=2)+'\n')
    lines = ['# SIFT free-PnP LM with raw-gyro validation', '',
             'Cached LK, global scales and all other modules unchanged. GT evaluation only.', '',
             '|Case|Baseline max mm|Candidate max mm|Mean mm|P95 mm|Result|', '|---|---:|---:|---:|---:|---|']
    for r in rows:
        if r['complete']:
            lines.append(f'|{r["case"]}|{r["baseline_max_mm"]:.3f}|{r["max_mm"]:.3f}|{r["mean_mm"]:.3f}|{r["p95_mm"]:.3f}|{r["result"]}|')
    (args.candidate/'comparison.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
