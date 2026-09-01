"""Convert the UMI magnetic encoder angle into training-friendly gripper state.

The millimetre output is a no-load soft-pad gap estimate.  Raw angle and
closure ratio remain the authoritative model-training signals when gripping an
object because pad compression was not force-calibrated.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_PROFILE = Path(__file__).resolve().parents[2] / "config" / "gripper" / "umi_manual_gripper_20260824.yaml"


class CalibrationError(ValueError):
    pass


def shortest_angle_delta_deg(current: float, previous: float) -> float:
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
        self.gap_direction_mode = str(
            document.get("gap_direction_mode", "direction_specific")
        )
        if self.gap_direction_mode == "independent":
            self.curves = {
                "independent": self._validate_curve(
                    "independent", document["curves"]["independent"]
                )
            }
        elif self.gap_direction_mode == "direction_specific":
            self.curves = {
                name: self._validate_curve(name, document["curves"][name])
                for name in ("closing", "opening")
            }
        else:
            raise CalibrationError(
                "gap_direction_mode must be independent or direction_specific"
            )

    @classmethod
    def load(cls, path: Path = DEFAULT_PROFILE) -> "ManualGripperCalibration":
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if document.get("format_version") != 1:
            raise CalibrationError("unsupported manual gripper profile format")
        return cls(document)

    def _validate_curve(self, name: str, raw_points: list[dict[str, Any]]):
        points = tuple(
            (float(item["angle_deg"]), float(item["gap_mm"]))
            for item in raw_points
        )
        if len(points) < 2:
            raise CalibrationError(f"{name} curve needs at least two points")
        if any(b[0] <= a[0] or b[1] <= a[1] for a, b in zip(points, points[1:])):
            raise CalibrationError(f"{name} curve must be strictly increasing")
        return points

    def estimate_gap_mm(self, angle_deg: float, direction: str) -> float:
        curve_name = "independent" if self.gap_direction_mode == "independent" else direction
        if curve_name not in self.curves:
            raise CalibrationError(f"unknown direction: {direction}")
        points = self.curves[curve_name]
        reference = (points[0][0] + points[-1][0]) / 2.0
        angle = float(angle_deg) + 360.0 * round((reference - float(angle_deg)) / 360.0)
        angles = [point[0] for point in points]
        if angle <= angles[0]:
            return points[0][1]
        if angle >= angles[-1]:
            return points[-1][1]
        upper = bisect.bisect_right(angles, angle)
        angle0, gap0 = points[upper - 1]
        angle1, gap1 = points[upper]
        return gap0 + (angle - angle0) / (angle1 - angle0) * (gap1 - gap0)

    def state(self, angle_deg: float, direction: str) -> ManualGripperState:
        gap = min(self.fully_open_gap_mm, max(0.0, self.estimate_gap_mm(angle_deg, direction)))
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
        if self.gap_direction_mode == "independent":
            gap = min(
                self.fully_open_gap_mm,
                max(0.0, self.estimate_gap_mm(angle_deg, "independent")),
            )
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
                status="MANUAL_NO_LOAD_ESTIMATE_DIRECTION_INDEPENDENT",
            )
        gap = (
            self.estimate_gap_mm(angle_deg, "closing")
            + self.estimate_gap_mm(angle_deg, "opening")
        ) / 2.0
        gap = min(self.fully_open_gap_mm, max(0.0, gap))
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
        self._anchor: float | None = None
        self._direction: str | None = None

    def update(self, angle_deg: float, *, encoder_valid: bool = True) -> ManualGripperState:
        angle = float(angle_deg) % 360.0
        if not encoder_valid:
            return ManualGripperState(
                angle, self._direction or "unknown", None, None, None, None,
                None, False, "ENCODER_INVALID",
            )
        if self._anchor is None:
            self._anchor = angle
            return self.calibration.unknown_direction_state(angle)
        delta = shortest_angle_delta_deg(angle, self._anchor)
        if delta <= -self.calibration.direction_deadband_deg:
            self._direction, self._anchor = "closing", angle
        elif delta >= self.calibration.direction_deadband_deg:
            self._direction, self._anchor = "opening", angle
        if self._direction is None:
            return self.calibration.unknown_direction_state(angle)
        return self.calibration.state(angle, self._direction)
