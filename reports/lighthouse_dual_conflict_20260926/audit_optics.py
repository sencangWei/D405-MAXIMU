"""Read-only raw-record audit; no fitting, hardware access or pose replacement."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def distribution(values):
    a = np.asarray(values, dtype=float)
    return {"n": len(a), "signed_median_deg": float(np.median(a)),
            "abs_p95_deg": float(np.percentile(np.abs(a), 95)),
            "over_0_1_deg_fraction": float(np.mean(np.abs(a) > 0.1))}


def audit(path):
    events, channels, identities = Counter(), Counter(), defaultdict(set)
    configs, residuals, bins, sensors = set(), defaultdict(list), defaultdict(list), defaultdict(list)
    angles, qnorms, jumps = defaultdict(list), [], []
    previous = {}
    pending_measurements, coverage = [], defaultdict(list)
    invalid = 0
    with path.open() as stream:
        for line in stream:
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                t = float(parts[0])
            except ValueError:
                continue
            op = parts[2]
            events[op] += 1
            if op == "CONFIG" and parts[1] == "WM0":
                payload = line.split(" CONFIG ", 1)[1].strip()
                configs.add(hashlib.sha256(payload.encode()).hexdigest())
            elif op == "LH_POSE":
                identities[parts[1]].add(parts[-1])
                qnorms.append(float(np.linalg.norm(np.array(parts[6:10], dtype=float))))
            elif parts[1] == "WM0" and op in {"Y", "W", "B"}:
                channels[parts[3]] += 1
                if op == "B":
                    angles[(int(parts[3]), int(parts[6]))].append(float(parts[7]))
            elif parts[1] == "WM0" and op == "RA":
                sensor, axis, value, lh = int(parts[3]), int(parts[4]), math.degrees(float(parts[5])), int(parts[6])
                if not math.isfinite(value):
                    invalid += 1
                    continue
                residuals[(lh, axis)].append(value)
                sensors[(lh, axis, sensor)].append(value)
                bins[(int(t // 2), lh)].append(value)
                pending_measurements.append((lh, axis, sensor))
            elif (parts[1] == "WM0" and op == "POSE") or (parts[1] == "WM0-raw-obs" and op == "EXTERNAL_POSE"):
                name = parts[1]
                p = np.array(parts[3:6], dtype=float)
                q = np.array(parts[6:10], dtype=float)
                norm = float(np.linalg.norm(q))
                if not np.all(np.isfinite(p)) or not math.isfinite(norm) or norm < 1e-12:
                    invalid += 1
                    continue
                qnorms.append(norm)
                q /= norm
                flagged = False
                if name in previous:
                    old_t, old_p, old_q = previous[name]
                    dt = t - old_t
                    distance = float(np.linalg.norm(p - old_p) * 1000)
                    angle = math.degrees(2 * math.acos(min(1, abs(float(q @ old_q)))))
                    # Distinct observations may share a record timestamp. Keep
                    # their discontinuities, but never infer a physical velocity.
                    if 0 <= dt <= 0.020 and (distance > 20 or angle > 10):
                        flagged = True
                        jumps.append({"stream": name, "record_time_s": t, "record_dt_ms": dt * 1000,
                                      "position_step_mm": distance, "rotation_step_deg": angle})
                previous[name] = (t, p, q)
                if name == "WM0-raw-obs":
                    coverage["flagged" if flagged else "other"].append([
                        len(pending_measurements), len(set(pending_measurements)),
                        len({(lh, axis) for lh, axis, _ in pending_measurements}),
                        len({lh for lh, _, _ in pending_measurements})])
                    pending_measurements.clear()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "event_counts": dict(events), "optical_channels": dict(channels),
            "channel_station_ids": {k: sorted(v) for k, v in identities.items()},
            "wm0_config_payload_hashes": sorted(configs), "invalid_values": invalid,
            "quaternion_norm_range": [min(qnorms), max(qnorms)] if qnorms else None,
            "recorded_pose_jumps": jumps,
            "solve_support_columns": ["measurements", "unique_measurements", "station_axes", "stations"],
            "solve_support_by_discontinuity": {k: {"n": len(v), "median": np.median(v, axis=0).tolist(),
                                                     "min": np.min(v, axis=0).tolist()}
                                               for k, v in coverage.items()},
            "residual_by_station_axis": {f"{lh}/{axis}": distribution(v) for (lh, axis), v in sorted(residuals.items())},
            "residual_by_station_axis_sensor": {f"{lh}/{axis}/{sensor}": distribution(v)
                                                  for (lh, axis, sensor), v in sorted(sensors.items())},
            "residual_2s_bins": {f"{b * 2}/{lh}": distribution(v) for (b, lh), v in sorted(bins.items())},
            "angle_coverage": {f"{ch}/{axis}": {"n": len(v), "p5_p95_rad": np.percentile(v, [5, 95]).tolist()}
                               for (ch, axis), v in sorted(angles.items())}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("records", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps([audit(path) for path in args.records], indent=2, allow_nan=False))
