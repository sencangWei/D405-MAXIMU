#!/usr/bin/env python3
"""Show a timed, always-on-top motion guide during AprilGrid capture."""

from __future__ import annotations

import argparse
import time

import cv2
import numpy as np


def stage_schedule(duration_s: float):
    initial_hold_s = min(8.0, duration_s / 4.0)
    final_hold_s = min(8.0, duration_s / 5.0)
    motion_s = max(0.0, duration_s - initial_hold_s - final_hold_s)
    round_s = motion_s / 2.0
    return [
        (
            "ALIGN + HOLD STILL",
            "Use IR preview: keep the full AprilGrid visible",
            initial_hold_s,
            (0, 180, 255),
        ),
        (
            "ROUND 1",
            "ROLL + PITCH + YAW, then small XYZ motion",
            round_s,
            (0, 220, 0),
        ),
        (
            "ROUND 2",
            "REPEAT ROLL + PITCH + YAW and small XYZ motion",
            round_s,
            (255, 180, 0),
        ),
        (
            "FINISH: HOLD STILL",
            "Keep AprilGrid visible",
            final_hold_s,
            (0, 180, 255),
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, required=True)
    args = parser.parse_args()
    if args.duration <= 0.0:
        raise ValueError("duration must be positive")

    window = "APRILGRID CALIBRATION MOTION GUIDE"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 1000, 260)
    cv2.moveWindow(window, 20, 20)
    cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 1)
    capture_start = time.monotonic()
    stage_start = capture_start
    schedule = stage_schedule(args.duration)

    for stage_index, (title, detail, stage_duration, color) in enumerate(schedule):
        stage_start = time.monotonic()
        while True:
            now = time.monotonic()
            elapsed = now - stage_start
            if elapsed >= stage_duration:
                break
            remaining = max(0.0, stage_duration - elapsed)
            canvas = np.full((260, 1000, 3), 22, dtype=np.uint8)
            cv2.putText(
                canvas,
                title,
                (40, 85),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.8,
                color,
                4,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                detail,
                (40, 145),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (240, 240, 240),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                f"Stage {stage_index + 1}/4     {remaining:04.1f} s",
                (40, 210),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (180, 180, 180),
                2,
                cv2.LINE_AA,
            )
            progress = min(1.0, (now - capture_start) / args.duration)
            cv2.rectangle(canvas, (40, 232), (960, 247), (70, 70, 70), -1)
            cv2.rectangle(
                canvas,
                (40, 232),
                (40 + int(920 * progress), 247),
                color,
                -1,
            )
            cv2.imshow(window, canvas)
            key = cv2.waitKey(25) & 0xFF
            if key in (ord("q"), 27):
                cv2.destroyWindow(window)
                return 0

    cv2.destroyWindow(window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
