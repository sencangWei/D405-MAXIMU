"""Verify actual cached production motion inputs, not just another diagnostic."""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/stereo_spatial_repeatability_20260927/production_depth_raw_ten_v2'


def main():
    rows = []
    for row in json.loads((OUT / 'summary.json').read_text())['cases']:
        case = json.loads((OUT / f'{row["case"]}.json').read_text())
        assert case['replay_parameters'] == dict(num_disparities=128, min_depth_m=0.07, max_depth_m=0.6)
        reports = {Path(p).name: json.loads(Path(p).read_text()) for p in case['source_sha256']}
        differences = []
        for item in case['measurements']:
            cached = next(e for e in reports[item['family']]['observations']
                          if e['first_index'] == item['first'] and e['second_index'] == item['second'])
            replay = item['free']['measurement']
            bad = []
            for key in ('accepted', 'method', 'metric_displacement_camera_i_m',
                        'pnp_rotation_quaternion_xyzw', 'tracked_points', 'pnp_inliers'):
                if key not in cached:
                    continue
                expected, actual = cached[key], replay.get(key)
                if isinstance(expected, (bool, str)):
                    same = actual == expected
                else:
                    same = actual is not None and np.allclose(actual, expected, rtol=0, atol=1e-8)
                if not same:
                    bad.append(key)
            if bad:
                differences.append(dict(first=item['first'], second=item['second'], fields=bad))
        rows.append(dict(case=case['case'], sampled_edges=len(case['measurements']),
                         exact_cached_motion_matches=len(case['measurements'])-len(differences),
                         mismatches=differences))
    output = dict(cases=rows, external_reference_used=False, trajectory_modified=False,
                  total_edges=sum(r['sampled_edges'] for r in rows),
                  total_cached_matches=sum(r['exact_cached_motion_matches'] for r in rows))
    (OUT / 'cached_motion_audit.json').write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps(output, indent=2))
    assert output['total_edges'] == output['total_cached_matches'], 'Not a fully faithful production replay'


if __name__ == '__main__':
    main()
