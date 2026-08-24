"""Manual UMI gripper angle-to-state conversion.

The encoder is authoritative for hand motion.  Millimetre values are explicitly
no-load estimates and are not treated as loaded object-size measurements.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class CalibrationError(ValueError):
    pass


def shortest_angle_delta_deg(current: float, previous: float) -> float:
    """Return current - previous in [-180, 180)."""
    return (float(current) - float(previous) + 180.0) % 360.0 - 180.0


@dataclass(frozen=True)
class ManualGripperState:
    angle_deg: float
    direction: str
    estimated_no_load_gap_mm: float | None
    dual_closing_distance_mm: float | None
    single_jaw_travel_mm: float | None
    closure_ratio: float | None
    no_load_uncertainty_mm: float | None
    loaded_object_size_valid: bool
    status: str


class ManualGripperCalibration:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.profile_id = str(document["profile_id"])
        self.fully_open_gap_mm = float(document["fully_open_gap_mm"])
        self.direction_deadband_deg = float(document["direction_deadband_deg"])
        self.no_load_uncertainty_mm = float(document["no_load_uncertainty_mm"])
        self.loaded_object_size_valid = bool(document["loaded_object_size_valid"])
        if self.fully_open_gap_mm <= 0.0:
            raise CalibrationError("fully_open_gap_mm must be positive")
        if self.direction_deadband_deg <= 0.0:
            raise CalibrationError("direction_deadband_deg must be positive")
        if self.loaded_object_size_valid:
            raise CalibrationError("manual soft-pad profile cannot validate loaded object size")
        self.curves = {
            name: self._validate_curve(name, document["curves"][name])
            for name in ("closing", "opening")
        }

    @classmethod
    def load(cls, path: Path) -> "ManualGripperCalibration":
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if document.get("format_version") != 1:
            raise CalibrationError("unsupported manual gripper profile format")
        return cls(document)

    def _validate_curve(
        self, name: str, raw_points: list[dict[str, Any]]
    ) -> tuple[tuple[float, float], ...]:
        if len(raw_points) < 2:
            raise CalibrationError(f"{name} curve needs at least two points")
        points = tuple(
            (float(item["angle_deg"]), float(item["gap_mm"]))
            for item in raw_points
        )
        angles = [item[0] for item in points]
        gaps = [item[1] for item in points]
        if any(right <= left for left, right in zip(angles, angles[1:])):
            raise CalibrationError(f"{name} angles must be strictly increasing")
        if any(right <= left for left, right in zip(gaps, gaps[1:])):
            raise CalibrationError(f"{name} gaps must be strictly increasing")
        if gaps[0] < 0.0 or gaps[-1] > self.fully_open_gap_mm + 1e-9:
            raise CalibrationError(f"{name} gaps exceed physical range")
        return points

    def estimate_gap_mm(self, angle_deg: float, direction: str) -> float:
        if direction not in self.curves:
            raise CalibrationError(f"unknown direction: {direction}")
        points = self.curves[direction]
        reference = (points[0][0] + points[-1][0]) / 2.0
        angle = float(angle_deg) + 360.0 * round((reference - float(angle_deg)) / 360.0)
        angles = [item[0] for item in points]
        if angle <= angles[0]:
            return points[0][1]
        if angle >= angles[-1]:
            return points[-1][1]
        upper = bisect.bisect_right(angles, angle)
        lower = upper - 1
        angle0, gap0 = points[lower]
        angle1, gap1 = points[upper]
        fraction = (angle - angle0) / (angle1 - angle0)
        return gap0 + fraction * (gap1 - gap0)

    def state(self, angle_deg: float, direction: str) -> ManualGripperState:
        gap = min(
            self.fully_open_gap_mm,
            max(0.0, self.estimate_gap_mm(angle_deg, direction)),
        )
        dual = self.fully_open_gap_mm - gap
        return ManualGripperState(
            angle_deg=float(angle_deg) % 360.0,
            direction=direction,
            estimated_no_load_gap_mm=gap,
            dual_closing_distance_mm=dual,
            single_jaw_travel_mm=dual / 2.0,
            closure_ratio=dual / self.fully_open_gap_mm,
            no_load_uncertainty_mm=self.no_load_uncertainty_mm,
            loaded_object_size_valid=False,
            status="MANUAL_NO_LOAD_ESTIMATE",
        )

    def unknown_direction_state(self, angle_deg: float) -> ManualGripperState:
        closing = self.estimate_gap_mm(angle_deg, "closing")
        opening = self.estimate_gap_mm(angle_deg, "opening")
        gap = min(self.fully_open_gap_mm, max(0.0, (closing + opening) / 2.0))
        dual = self.fully_open_gap_mm - gap
        return ManualGripperState(
            angle_deg=float(angle_deg) % 360.0,
            direction="unknown",
            estimated_no_load_gap_mm=gap,
            dual_closing_distance_mm=dual,
            single_jaw_travel_mm=dual / 2.0,
            closure_ratio=dual / self.fully_open_gap_mm,
            no_load_uncertainty_mm=self.no_load_uncertainty_mm,
            loaded_object_size_valid=False,
            status="MANUAL_NO_LOAD_ESTIMATE_DIRECTION_UNKNOWN",
        )


class ManualGripperTracker:
    def __init__(self, calibration: ManualGripperCalibration) -> None:
        self.calibration = calibration
        self._direction_anchor_deg: float | None = None
        self._direction: str | None = None

    def update(
        self, angle_deg: float, *, encoder_valid: bool = True
    ) -> ManualGripperState:
        angle = float(angle_deg) % 360.0
        if not encoder_valid:
            return ManualGripperState(
                angle_deg=angle,
                direction=self._direction or "unknown",
                estimated_no_load_gap_mm=None,
                dual_closing_distance_mm=None,
                single_jaw_travel_mm=None,
                closure_ratio=None,
                no_load_uncertainty_mm=None,
                loaded_object_size_valid=False,
                status="ENCODER_INVALID",
            )
        if self._direction_anchor_deg is None:
            self._direction_anchor_deg = angle
            return self.calibration.unknown_direction_state(angle)
        delta = shortest_angle_delta_deg(angle, self._direction_anchor_deg)
        if delta <= -self.calibration.direction_deadband_deg:
            self._direction = "closing"
            self._direction_anchor_deg = angle
        elif delta >= self.calibration.direction_deadband_deg:
            self._direction = "opening"
            self._direction_anchor_deg = angle
        if self._direction is None:
            return self.calibration.unknown_direction_state(angle)
        return self.calibration.state(angle, self._direction)
