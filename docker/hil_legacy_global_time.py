#!/usr/bin/env python3
"""Audit the unchanged product-1 camera timestamp path for two minutes."""

import collections
import threading

import numpy as np

from ego_vio.camera.realsense_capture import RealSenseCapture


TARGET_FRAMES = 3600
rows = []
done = threading.Event()


def on_frame(frame):
    rows.append((
        int(frame.frame_number), float(frame.ts), float(frame.ts_arrival),
        str(frame.ts_domain),
    ))
    if len(rows) >= TARGET_FRAMES:
        done.set()


camera = RealSenseCapture(
    serial="260322279785",
    width=1280,
    height=720,
    fps=30,
    enable_depth=False,
    stereo_ir=True,
    auto_exposure=False,
    exposure_us=30000.0,
    gain=100.0,
    warmup_consecutive_frames=60,
    on_frame=on_frame,
)
camera.start()
try:
    if not done.wait(130.0):
        raise SystemExit(f"FAIL: only received {len(rows)} formal frames")
finally:
    camera.stop()

rows = rows[:TARGET_FRAMES]
numbers = np.asarray([row[0] for row in rows], dtype=np.float64)
stamps = np.asarray([row[1] for row in rows], dtype=np.float64)
arrivals = np.asarray([row[2] for row in rows], dtype=np.float64)


def fit(values):
    design = np.vstack([numbers, np.ones(len(numbers))]).T
    slope, intercept = np.linalg.lstsq(design, values, rcond=None)[0]
    residual = values - (slope * numbers + intercept)
    return 1.0 / slope, float(np.std(residual) * 1000.0)


rate, sigma = fit(stamps)
arrival_rate, arrival_sigma = fit(arrivals)
drops = int(np.sum(np.maximum(0.0, np.diff(numbers) - 1.0)))
print(f"formal_frames={len(rows)}")
print(f"device_frame_drops={drops}")
print(f"timestamp_domains={dict(collections.Counter(row[3] for row in rows))}")
print(f"legacy_timestamp_rate_hz={rate:.6f}")
print(f"legacy_timestamp_fit_sigma_ms={sigma:.6f}")
print(f"arrival_rate_hz={arrival_rate:.6f}")
print(f"arrival_fit_sigma_ms={arrival_sigma:.6f}")
if drops != 0:
    raise SystemExit("FAIL: device frame drops")
if not (29.0 <= rate <= 31.0):
    raise SystemExit("FAIL: legacy timestamp rate")
if sigma > 8.0:
    raise SystemExit("FAIL: legacy timestamp residual exceeds formal gate")
print("UNCHANGED PRODUCT-1 TIMEBASE HIL PASS")
