#!/usr/bin/env python3
"""Robust multi-segment VIO/TCP calibration diagnostics.

This tool fits a *diagnostic* smooth correction to a recorded VIO trajectory.
It jointly estimates a small hand-eye rotation/translation update, TCP lever
arm update, robot/UMI clock offset, global scale, and slowly varying local
scale/attitude/translation terms over several continuous windows.  The
original VIO stream is never overwritten and no parameter is applied to the
online estimator.

The local terms are intentionally regularized and validated on held-out
frames.  A lower training residual alone is not evidence of a valid SLAM
fix; callers must require both train and hold-out improvement before using
the output for a diagnostic overlay.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from compare_robot_umi_precision import (  # noqa: E402
    _pose_matrices,
    interpolate_rotation,
    interpolate_vector,
    kabsch,
    load_gripper_tcp_offset,
    load_robot,
    load_transform,
    load_umi,
)


DEFAULT_WINDOWS = "5:15,20:30,35:45,50.8:53.4,55:65,68:77"


def parse_windows(spec: str) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for item in spec.split(","):
        start_s, end_s = item.strip().split(":", 1)
        start, end = float(start_s), float(end_s)
        if not (np.isfinite(start) and np.isfinite(end) and end > start >= 0.0):
            raise ValueError(f"invalid relative window: {item}")
        result.append((start, end))
    if not result:
        raise ValueError("at least one optimization window is required")
    return result


def _rotvec_matrix(rotvec: np.ndarray) -> np.ndarray:
    return Rotation.from_rotvec(np.asarray(rotvec, dtype=float)).as_matrix()


def _metrics(values: np.ndarray) -> dict[str, float | int]:
    values = np.asarray(values, dtype=float)
    return {
        "count": int(values.size),
        "rmse_mm": float(np.sqrt(np.mean(values * values)) * 1000.0),
        "median_mm": float(np.median(values) * 1000.0),
        "p95_mm": float(np.percentile(values, 95.0) * 1000.0),
        "max_mm": float(np.max(values) * 1000.0),
    }


def _orientation_metrics(rotvecs: np.ndarray) -> dict[str, float | int]:
    values = np.degrees(np.linalg.norm(rotvecs, axis=1))
    return {
        "count": int(values.size),
        "rmse_deg": float(np.sqrt(np.mean(values * values))),
        "median_deg": float(np.median(values)),
        "p95_deg": float(np.percentile(values, 95.0)),
        "max_deg": float(np.max(values)),
    }


@dataclass(frozen=True)
class Layout:
    knots: int

    @property
    def local_count(self) -> int:
        return self.knots - 1

    @property
    def size(self) -> int:
        return 3 + 3 + 3 + 3 + 3 + 1 + 1 + self.local_count * (1 + 3 + 3)

    def unpack(self, x: np.ndarray) -> dict[str, np.ndarray | float]:
        i = 0

        def take(count: int) -> np.ndarray:
            nonlocal i
            value = np.asarray(x[i : i + count], dtype=float)
            i += count
            return value

        return {
            "align_rot": take(3),
            "align_trans": take(3),
            "handeye_rot": take(3),
            "handeye_trans": take(3),
            "lever_delta": take(3),
            "dt_s": float(take(1)[0]),
            "global_log_scale": float(take(1)[0]),
            "local_log_scale": np.r_[0.0, take(self.local_count)],
            "local_rot": np.vstack((np.zeros(3), take(self.local_count * 3).reshape(-1, 3))),
            "local_trans": np.vstack((np.zeros(3), take(self.local_count * 3).reshape(-1, 3))),
        }


class JointOptimizer:
    def __init__(
        self,
        umi_t: np.ndarray,
        umi_p: np.ndarray,
        umi_q_xyzw: np.ndarray,
        robot_t: np.ndarray,
        robot_p: np.ndarray,
        robot_q_xyzw: np.ndarray,
        body_to_camera: np.ndarray,
        handeye: np.ndarray,
        tcp_offset: np.ndarray,
        base_offset_s: float,
        windows: list[tuple[float, float]],
        knots: int,
    ) -> None:
        self.umi_t = np.asarray(umi_t, dtype=float)
        self.umi_p = np.asarray(umi_p, dtype=float)
        self.umi_q_xyzw = np.asarray(umi_q_xyzw, dtype=float)
        self.robot_t = np.asarray(robot_t, dtype=float)
        self.robot_p = np.asarray(robot_p, dtype=float)
        self.robot_R = Rotation.from_quat(np.asarray(robot_q_xyzw, dtype=float))
        self.body_to_camera = np.asarray(body_to_camera, dtype=float)
        self.handeye0 = np.asarray(handeye, dtype=float)
        self.tcp_offset0 = np.asarray(tcp_offset, dtype=float)
        self.base_offset_s = float(base_offset_s)
        self.layout = Layout(knots)
        self.windows = windows

        self.body_world = _pose_matrices(
            self.umi_p, Rotation.from_quat(self.umi_q_xyzw)
        )
        self.rel_t = self.umi_t - self.umi_t[0]
        self.knot_u = np.linspace(0.0, 1.0, knots)
        self.u = np.clip(
            (self.rel_t - self.rel_t[0]) / max(self.rel_t[-1], 1e-9), 0.0, 1.0
        )
        self.initial_mask = self._valid_mask(self.base_offset_s)
        if int(self.initial_mask.sum()) < 50:
            raise ValueError("insufficient timestamp-overlapped samples")

        initial_gripper = self._map_gripper(self.handeye0)
        initial_tcp = self._apply_tcp(initial_gripper, self.tcp_offset0)
        self.initial_robot_p, self.initial_robot_R = self._robot_at(
            self.umi_t[self.initial_mask] + self.base_offset_s
        )
        align_R, align_t = kabsch(
            initial_tcp[self.initial_mask, :3, 3], self.initial_robot_p
        )
        self.initial_align_R = Rotation.from_matrix(align_R)
        self.initial_align_t = align_t
        self.initial_tcp = initial_tcp
        self.initial_gripper = initial_gripper
        self.train_mask = self._window_mask() & self.initial_mask
        # Every fifth sample is held out.  It remains in each continuous
        # window, so the validation tests interpolation/generalization rather
        # than a disjoint motion regime.
        valid_indices = np.flatnonzero(self.train_mask)
        self.holdout_mask = np.zeros_like(self.train_mask)
        self.holdout_mask[valid_indices[::5]] = True
        self.train_mask &= ~self.holdout_mask
        if int(self.train_mask.sum()) < 40 or int(self.holdout_mask.sum()) < 10:
            raise ValueError("windows leave too few train/hold-out samples")

    def _window_mask(self) -> np.ndarray:
        mask = np.zeros_like(self.rel_t, dtype=bool)
        for start, end in self.windows:
            mask |= (self.rel_t >= start) & (self.rel_t <= end)
        return mask

    def _valid_mask(self, offset_s: float) -> np.ndarray:
        query = self.umi_t + offset_s
        _, _, valid = interpolate_vector(
            query, self.robot_t, self.robot_p, max_gap_s=0.060
        )
        _, valid_rot = interpolate_rotation(
            query, self.robot_t, self.robot_R, max_gap_s=0.060
        )
        return valid & valid_rot

    def _robot_at(self, query: np.ndarray) -> tuple[np.ndarray, Rotation]:
        positions, _, valid = interpolate_vector(
            query, self.robot_t, self.robot_p, max_gap_s=0.060
        )
        quats, valid_rot = interpolate_rotation(
            query, self.robot_t, self.robot_R, max_gap_s=0.060
        )
        if not np.all(valid & valid_rot):
            # Bounds keep this rare.  Clipped interpolation is still returned;
            # an explicit invalid penalty is added by residual().
            pass
        return positions, Rotation.from_quat(quats)

    def _map_gripper(self, handeye: np.ndarray) -> np.ndarray:
        return self.body_world @ self.body_to_camera @ np.linalg.inv(handeye)

    @staticmethod
    def _apply_tcp(gripper: np.ndarray, tcp_offset: np.ndarray) -> np.ndarray:
        return gripper @ tcp_offset

    @staticmethod
    def _interp(values: np.ndarray, u: np.ndarray, knots: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=float)
        if values.ndim == 1:
            return np.interp(u, knots, values)
        return np.column_stack([np.interp(u, knots, values[:, j]) for j in range(values.shape[1])])

    def predict(
        self, x: np.ndarray, indices: np.ndarray
    ) -> tuple[np.ndarray, Rotation, np.ndarray, np.ndarray, np.ndarray]:
        p = self.layout.unpack(x)
        handeye = self.handeye0.copy()
        handeye[:3, :3] = handeye[:3, :3] @ _rotvec_matrix(p["handeye_rot"])
        handeye[:3, 3] += np.asarray(p["handeye_trans"])
        lever = self.tcp_offset0[:3, 3] + np.asarray(p["lever_delta"])
        gripper = self._map_gripper(handeye)

        local_log_scale = self._interp(
            np.asarray(p["local_log_scale"]), self.u, self.knot_u
        )
        local_rotvec = self._interp(
            np.asarray(p["local_rot"]), self.u, self.knot_u
        )
        local_trans = self._interp(
            np.asarray(p["local_trans"]), self.u, self.knot_u
        )
        local_R = Rotation.from_rotvec(local_rotvec)
        gripper_R = Rotation.from_matrix(gripper[:, :3, :3])
        corrected_R = local_R * gripper_R
        gripper_p = gripper[:, :3, 3]
        anchor = gripper_p[0]
        corrected_gripper_p = (
            anchor
            + np.exp(float(p["global_log_scale"]) + local_log_scale)[:, None]
            * (gripper_p - anchor)
            + local_trans
        )
        tcp_p = corrected_gripper_p + corrected_R.apply(lever)
        align_R = Rotation.from_rotvec(np.asarray(p["align_rot"])) * self.initial_align_R
        align_t = self.initial_align_t + np.asarray(p["align_trans"])
        predicted_p = align_R.apply(tcp_p) + align_t
        predicted_R = align_R * corrected_R

        query = self.umi_t + self.base_offset_s + float(p["dt_s"])
        robot_p, robot_R = self._robot_at(query)
        valid = self._valid_mask(self.base_offset_s + float(p["dt_s"]))
        return predicted_p[indices], predicted_R[indices], robot_p[indices], robot_R[indices], valid[indices]

    def _regularization(self, x: np.ndarray) -> np.ndarray:
        p = self.layout.unpack(x)
        local_scale = np.asarray(p["local_log_scale"])
        local_rot = np.asarray(p["local_rot"])
        local_trans = np.asarray(p["local_trans"])
        pieces = [
            np.asarray(p["handeye_rot"]) / 0.030,
            np.asarray(p["handeye_trans"]) / 0.010,
            np.asarray(p["lever_delta"]) / 0.010,
            np.asarray([p["dt_s"]]) / 0.010,
            np.asarray([p["global_log_scale"]]) / 0.030,
            local_scale / 0.030,
            local_rot.ravel() / 0.030,
            local_trans.ravel() / 0.008,
        ]
        if self.layout.knots >= 3:
            pieces.extend(
                [
                    np.diff(local_scale, n=2) / 0.010,
                    np.diff(local_rot, n=2, axis=0).ravel() / 0.020,
                    np.diff(local_trans, n=2, axis=0).ravel() / 0.005,
                ]
            )
        return np.concatenate(pieces)

    def residual(self, x: np.ndarray, indices: np.ndarray) -> np.ndarray:
        predicted_p, predicted_R, robot_p, robot_R, valid = self.predict(x, indices)
        position_residual = (predicted_p - robot_p) / 0.005
        rotation_error = (robot_R.inv() * predicted_R).as_rotvec() / 0.035
        if not np.all(valid):
            invalid_rows = np.flatnonzero(~valid)
            position_residual[invalid_rows] = 100.0
            rotation_error[invalid_rows] = 100.0
        return np.concatenate(
            [position_residual.ravel(), rotation_error.ravel(), self._regularization(x)]
        )

    def evaluate(self, x: np.ndarray, mask: np.ndarray) -> dict[str, object]:
        indices = np.flatnonzero(mask)
        predicted_p, predicted_R, robot_p, robot_R, valid = self.predict(x, indices)
        pos_error = np.linalg.norm(predicted_p - robot_p, axis=1)
        rot_error = (robot_R.inv() * predicted_R).as_rotvec()
        return {
            "samples": int(indices.size),
            "valid_at_optimized_offset": int(np.count_nonzero(valid)),
            "position": _metrics(pos_error),
            "orientation": _orientation_metrics(rot_error),
        }

    def corrected_rows(self, x: np.ndarray) -> list[dict[str, float]]:
        p = self.layout.unpack(x)
        indices = np.flatnonzero(self.initial_mask)
        predicted_p, _, robot_p, _, valid = self.predict(x, indices)
        query = self.umi_t[indices] + self.base_offset_s + float(p["dt_s"])
        _, nearest_delta, _ = interpolate_vector(
            query, self.robot_t, self.robot_p, max_gap_s=0.060
        )
        rows: list[dict[str, float]] = []
        for i, index in enumerate(indices):
            if not valid[i]:
                continue
            rows.append(
                {
                    "t_sec": float(self.umi_t[index]),
                    "association_delta_ms": float(nearest_delta[i] * 1000.0),
                    "robot_tcp_x": float(robot_p[i, 0]),
                    "robot_tcp_y": float(robot_p[i, 1]),
                    "robot_tcp_z": float(robot_p[i, 2]),
                    "umi_tcp_x": float(predicted_p[i, 0]),
                    "umi_tcp_y": float(predicted_p[i, 1]),
                    "umi_tcp_z": float(predicted_p[i, 2]),
                    "tcp_error_mm": float(np.linalg.norm(predicted_p[i] - robot_p[i]) * 1000.0),
                }
            )
        return rows


def _load_inputs(args: argparse.Namespace) -> JointOptimizer:
    umi_t, umi_p, umi_q = load_umi(args.slam_dir / args.umi_name)
    robot_t, robot_T, _, _ = load_robot(args.robot.resolve())
    body_to_camera = load_transform(args.body_to_camera.resolve(), "body_T_cam0")
    handeye = load_transform(args.handeye.resolve(), "T_gripper_camera")
    tcp_offset = load_gripper_tcp_offset(args.gripper_tcp_offset.resolve())
    # The optimizer compares the same physical right-jaw TCP on both sides.
    # Applying the lever arm only to the UMI mapping would create a false
    # 117 mm model discrepancy and drive every calibration variable to bounds.
    robot_tcp = robot_T["actual"] @ tcp_offset
    robot_p = robot_tcp[:, :3, 3]
    robot_q = Rotation.from_matrix(robot_tcp[:, :3, :3]).as_quat()
    clock = json.loads(args.clock_offset_file.resolve().read_text(encoding="utf-8"))
    base_offset_s = float(clock["robot_query_offset_ms"]) / 1000.0
    return JointOptimizer(
        umi_t,
        umi_p,
        umi_q,
        robot_t,
        robot_p,
        robot_q,
        body_to_camera,
        handeye,
        tcp_offset,
        base_offset_s,
        parse_windows(args.windows),
        args.knots,
    )


def _write_csv(path: Path, rows: list[dict[str, float]]) -> None:
    fields = [
        "t_sec",
        "association_delta_ms",
        "robot_tcp_x",
        "robot_tcp_y",
        "robot_tcp_z",
        "umi_tcp_x",
        "umi_tcp_y",
        "umi_tcp_z",
        "tcp_error_mm",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slam-dir", type=Path, required=True)
    parser.add_argument("--umi-name", default="vio_corrected_stream.csv")
    parser.add_argument("--robot", type=Path, required=True)
    parser.add_argument("--clock-offset-file", type=Path, required=True)
    parser.add_argument("--handeye", type=Path, required=True)
    parser.add_argument("--body-to-camera", type=Path, required=True)
    parser.add_argument("--gripper-tcp-offset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--windows", default=DEFAULT_WINDOWS)
    parser.add_argument("--knots", type=int, default=9)
    parser.add_argument("--max-nfev", type=int, default=80)
    parser.add_argument(
        "--handeye-rotation-bound-deg",
        type=float,
        default=2.0,
        help="hand-eye rotation correction bound per axis (diagnostic prior)",
    )
    parser.add_argument(
        "--handeye-translation-bound-mm",
        type=float,
        default=5.0,
        help="hand-eye translation correction bound per axis (diagnostic prior)",
    )
    parser.add_argument(
        "--lever-bound-mm",
        type=float,
        default=5.0,
        help="TCP lever-arm correction bound per axis (diagnostic prior)",
    )
    parser.add_argument(
        "--time-offset-bound-ms",
        type=float,
        default=10.0,
        help="robot/UMI query offset correction bound",
    )
    parser.add_argument(
        "--global-scale-bound-percent",
        type=float,
        default=3.0,
        help="global scale correction bound in percent",
    )
    parser.add_argument(
        "--local-scale-bound-percent",
        type=float,
        default=5.0,
        help="local scale correction bound in percent",
    )
    parser.add_argument(
        "--local-rotation-bound-deg",
        type=float,
        default=5.0,
        help="local smooth attitude correction bound per axis",
    )
    parser.add_argument(
        "--local-translation-bound-mm",
        type=float,
        default=15.0,
        help="local smooth translation correction bound per axis",
    )
    args = parser.parse_args()
    if args.knots < 3:
        parser.error("--knots must be at least 3")
    for name in (
        "handeye_rotation_bound_deg",
        "handeye_translation_bound_mm",
        "lever_bound_mm",
        "time_offset_bound_ms",
        "global_scale_bound_percent",
        "local_scale_bound_percent",
        "local_rotation_bound_deg",
        "local_translation_bound_mm",
    ):
        if not np.isfinite(getattr(args, name)) or getattr(args, name) <= 0.0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")

    optimizer = _load_inputs(args)
    x0 = np.zeros(optimizer.layout.size, dtype=float)
    # Bounds are deliberately small: this is a calibration/diagnostic layer,
    # not permission to invent a new trajectory.
    lower = np.full_like(x0, -np.inf)
    upper = np.full_like(x0, np.inf)
    i = 0

    def bounds(count: int, lo: float, hi: float) -> None:
        nonlocal i
        lower[i : i + count] = lo
        upper[i : i + count] = hi
        i += count

    bounds(3, -0.20, 0.20)   # display/world alignment rotation delta
    bounds(3, -0.20, 0.20)   # display/world alignment translation delta
    handeye_rot_bound = np.deg2rad(args.handeye_rotation_bound_deg)
    handeye_trans_bound = args.handeye_translation_bound_mm / 1000.0
    lever_bound = args.lever_bound_mm / 1000.0
    dt_bound = args.time_offset_bound_ms / 1000.0
    global_scale_bound = np.log1p(args.global_scale_bound_percent / 100.0)
    local_scale_bound = np.log1p(args.local_scale_bound_percent / 100.0)
    local_rot_bound = np.deg2rad(args.local_rotation_bound_deg)
    local_trans_bound = args.local_translation_bound_mm / 1000.0
    bounds(3, -handeye_rot_bound, handeye_rot_bound)
    bounds(3, -handeye_trans_bound, handeye_trans_bound)
    bounds(3, -lever_bound, lever_bound)
    bounds(1, -dt_bound, dt_bound)
    bounds(1, -global_scale_bound, global_scale_bound)
    bounds(optimizer.layout.local_count, -local_scale_bound, local_scale_bound)
    bounds(optimizer.layout.local_count * 3, -local_rot_bound, local_rot_bound)
    bounds(optimizer.layout.local_count * 3, -local_trans_bound, local_trans_bound)
    if i != optimizer.layout.size:
        raise AssertionError(f"bounds cover {i} variables, expected {optimizer.layout.size}")

    train_indices = np.flatnonzero(optimizer.train_mask)
    result = least_squares(
        lambda x: optimizer.residual(x, train_indices),
        x0,
        bounds=(lower, upper),
        loss="soft_l1",
        f_scale=1.0,
        max_nfev=args.max_nfev,
        x_scale="jac",
        verbose=1,
    )
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = optimizer.corrected_rows(result.x)
    _write_csv(out / "matched_tcp_joint_optimized.csv", rows)
    params = optimizer.layout.unpack(result.x)
    report = {
        "schema": "multi_segment_vio_tcp_joint_optimization_v1",
        "diagnostic_only": True,
        "source_vio_unchanged": True,
        "windows_relative_s": optimizer.windows,
        "knots": args.knots,
        "bounds": {
            "handeye_rotation_deg": args.handeye_rotation_bound_deg,
            "handeye_translation_mm": args.handeye_translation_bound_mm,
            "lever_mm": args.lever_bound_mm,
            "time_offset_ms": args.time_offset_bound_ms,
            "global_scale_percent": args.global_scale_bound_percent,
            "local_scale_percent": args.local_scale_bound_percent,
            "local_rotation_deg": args.local_rotation_bound_deg,
            "local_translation_mm": args.local_translation_bound_mm,
        },
        "base_robot_query_offset_ms": optimizer.base_offset_s * 1000.0,
        "optimized_robot_query_offset_ms": (optimizer.base_offset_s + float(params["dt_s"])) * 1000.0,
        "optimized_parameters": {
            "alignment_rotation_delta_deg": float(np.degrees(np.linalg.norm(params["align_rot"]))),
            "alignment_translation_delta_mm": (np.linalg.norm(params["align_trans"]) * 1000.0),
            "handeye_rotation_delta_deg": float(np.degrees(np.linalg.norm(params["handeye_rot"]))),
            "handeye_translation_delta_mm": (np.linalg.norm(params["handeye_trans"]) * 1000.0),
            "tcp_lever_delta_mm": (np.asarray(params["lever_delta"]) * 1000.0).tolist(),
            "global_scale": float(np.exp(float(params["global_log_scale"]))),
            "local_scale_range": [
                float(np.exp(np.min(params["global_log_scale"] + params["local_log_scale"]))),
                float(np.exp(np.max(params["global_log_scale"] + params["local_log_scale"]))),
            ],
        },
        "solver": {
            "success": bool(result.success),
            "status": int(result.status),
            "message": str(result.message),
            "cost": float(result.cost),
            "optimality": float(result.optimality),
            "nfev": int(result.nfev),
        },
        "before": {
            "train": optimizer.evaluate(x0, optimizer.train_mask),
            "holdout": optimizer.evaluate(x0, optimizer.holdout_mask),
            "all_windows": optimizer.evaluate(x0, optimizer.initial_mask & optimizer._window_mask()),
        },
        "after": {
            "train": optimizer.evaluate(result.x, optimizer.train_mask),
            "holdout": optimizer.evaluate(result.x, optimizer.holdout_mask),
            "all_windows": optimizer.evaluate(result.x, optimizer.initial_mask & optimizer._window_mask()),
        },
        "artifacts": {
            "matched_csv": str(out / "matched_tcp_joint_optimized.csv"),
            "source_slam_dir": str(args.slam_dir.resolve()),
            "source_robot": str(args.robot.resolve()),
            "handeye": str(args.handeye.resolve()),
            "body_to_camera": str(args.body_to_camera.resolve()),
            "tcp_offset": str(args.gripper_tcp_offset.resolve()),
        },
    }
    (out / "joint_optimization_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md = [
        "# 多段联合 VIO/TCP 优化（诊断输出）",
        "",
        "该结果只改变重合诊断中的 UMI 映射副本，不覆盖原始 VIO，也不作为在线 SLAM 参数。",
        "",
        f"- 窗口：{optimizer.windows}",
        f"- 设备级偏移：{optimizer.base_offset_s * 1000.0:.3f} → {report['optimized_robot_query_offset_ms']:.3f} ms",
        f"- 优化成功：{result.success}，迭代评估 {result.nfev} 次",
        "",
        f"- 训练位置 RMSE：{report['before']['train']['position']['rmse_mm']:.3f} → {report['after']['train']['position']['rmse_mm']:.3f} mm",
        f"- 留出位置 RMSE：{report['before']['holdout']['position']['rmse_mm']:.3f} → {report['after']['holdout']['position']['rmse_mm']:.3f} mm",
        f"- 全窗口位置 RMSE：{report['before']['all_windows']['position']['rmse_mm']:.3f} → {report['after']['all_windows']['position']['rmse_mm']:.3f} mm",
        "",
        "只有训练和留出窗口同时改善，且全局最大重合指标不恶化时，才可考虑把该结果用于后续诊断。",
    ]
    (out / "joint_optimization_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
