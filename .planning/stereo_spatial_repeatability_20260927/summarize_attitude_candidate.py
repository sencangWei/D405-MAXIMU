"""Frozen all-case evaluation, no GT input to trajectory generation."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/stereo_spatial_repeatability_20260927/attitude_alignment_candidate_v1'


def main():
    spec = importlib.util.spec_from_file_location('attitude_case_summary', ROOT / '.planning/joint_metric_scale_20260927/run_cached_regression.py')
    cached = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cached)
    cases = [(name, ROOT / 'reports' / source / 'mast3r/graph_fusion_report.json',
              ROOT / f'reports/joint_metric_scale_20260927/verified_v1_six/{name}/official_score/precision.json') for name, source, _ in cached.CASES]
    cases += [(f'fresh{i}', ROOT / f'reports/joint_scale_independent_four_20260927/take{i}/fusion/{"rescue" if i == 2 else "baseline"}/mast3r/graph_fusion_report.json',
               ROOT / f'reports/joint_scale_independent_four_20260927/take{i}/official_score/precision.json') for i in range(1, 5)]
    rows = []
    for name, graph_path, score_path in cases:
        before = json.loads(score_path.read_text())
        target = OUT / name
        after_path = target / 'official_score/precision.json'
        row = dict(case=name, baseline_result=before['result'], baseline_max_mm=before['ate_translation_max_m']*1000,
                   candidate_complete=after_path.exists())
        if after_path.exists():
            after = json.loads(after_path.read_text())
            original_graph = json.loads(graph_path.read_text())
            graph = json.loads((target / 'graph_fusion_report.json').read_text())
            assert after['samples'] == before['samples'] and after['alignment'] == before['alignment']
            assert not graph['external_ground_truth_used'] and not graph['slam_supervision']
            old_sigma = original_graph['position_fusion_selection']['visual_position_sigma']['selected_sigma_m']
            new_sigma = graph['position_fusion_selection']['visual_position_sigma']['selected_sigma_m']
            assert old_sigma == new_sigma, 'Secondary auto-weight change would confound this experiment'
            manifest = json.loads((target / 'manifest.json').read_text())
            for path, expected in manifest['input_sha256'].items():
                assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
            row.update(candidate_result=after['result'], candidate_max_mm=after['ate_translation_max_m']*1000,
                       candidate_mean_mm=after['ate_translation_mean_m']*1000,
                       candidate_p95_mm=after['ate_translation_p95_m']*1000,
                       within_10mm_ratio=after['ate_translation_within_10mm_ratio'], samples=after['samples'],
                       selected_visual_sigma_m=new_sigma,
                       baseline_alignment=original_graph['relative_motion_alignment'],
                       candidate_alignment=graph['relative_motion_alignment'])
        rows.append(row)
    code = [Path(__file__).with_name(name) for name in ('attitude_alignment.py', 'graph_attitude_proxy.py', 'run_attitude_regression.py')]
    result = dict(completed=sum(r['candidate_complete'] for r in rows), total=len(rows),
                  pass_count=sum(r.get('candidate_result')=='PASS' for r in rows), production_changed=False,
                  optimizer_external_reference_used=False,
                  candidate_source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in code}, cases=rows)
    (OUT / 'comparison.json').write_text(json.dumps(result, indent=2)+'\n')
    lines = ['# Single world-basis alignment candidate', '',
             'SE(3) scored after UMI-only optimization; sample counts and auto visual sigma unchanged.', '',
             '|Case|Baseline max mm|Candidate max mm|Mean mm|P95 mm|Result|', '|---|---:|---:|---:|---:|---|']
    for row in rows:
        if row['candidate_complete']:
            lines.append(f'|{row["case"]}|{row["baseline_max_mm"]:.3f}|{row["candidate_max_mm"]:.3f}|{row["candidate_mean_mm"]:.3f}|{row["candidate_p95_mm"]:.3f}|{row["candidate_result"]}|')
    (OUT / 'comparison.md').write_text('\n'.join(lines)+'\n')
    print({k:v for k,v in result.items() if k not in ('cases','candidate_source_sha256')})
    for row in rows:
        print({k:v for k,v in row.items() if 'alignment' not in k})


if __name__ == '__main__':
    main()
