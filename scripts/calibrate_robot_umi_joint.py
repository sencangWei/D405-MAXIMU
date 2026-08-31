#!/usr/bin/env python3
"""Jointly estimate robot/UMI clock offset and rigid body transform.

The UMI trajectory is a VIO pose in an arbitrary world gauge.  For each run
the gauge cancels when forming relative motions, leaving the fixed body to
robot-TCP transform ``X`` in the hand-eye equation::

    A_umi X = X B_robot

where ``A_umi`` and ``B_robot`` are relative poses over the same time window.
The command accepts several independently recorded runs, searches one shared
device-level clock offset, and solves ``X`` with a Huber-robust least-squares
fit.  It deliberately never uses an endpoint or a trajectory Kabsch fit.

This is a calibration *candidate* and is not applied to a VINS configuration
automatically.  A run may be supplied as ``name=umi.csv:robot.jsonl`` or just
``umi.csv:robot.jsonl``.  Robot timestamps must be from the event-time or
legacy host-wall-time contract already enforced by compare_robot_umi_precision.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares, minimize_scalar
from scipy.spatial.transform import Rotation


_COMPARE_PATH = Path(__file__).with_name("compare_robot_umi_precision.py")
_SPEC = importlib.util.spec_from_file_location("compare_robot_umi_precision", _COMPARE_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover
    raise ImportError(f"cannot import {_COMPARE_PATH}")
_COMPARE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_COMPARE)


@dataclass
class RunData:
    name: str
    umi_path: Path
    robot_path: Path
    umi_t: np.ndarray
    umi_T: np.ndarray
    robot_t: np.ndarray
    robot_T: np.ndarray
    uses_event_time: bool


def _pose_matrices(p: np.ndarray, q_xyzw: np.ndarray) -> np.ndarray:
    out = np.tile(np.eye(4), (len(p), 1, 1))
    out[:, :3, :3] = Rotation.from_quat(q_xyzw).as_matrix()
    out[:, :3, 3] = p
    return out


def _parse_pair(value: str) -> tuple[str, Path, Path]:
    name = ""
    spec = value
    if "=" in value:
        name, spec = value.split("=", 1)
        name = name.strip()
    if ":" not in spec:
        raise ValueError(f"--pair 格式应为 name=umi.csv:robot.jsonl，实际为 {value}")
    umi, robot = spec.rsplit(":", 1)
    umi_path, robot_path = Path(umi).expanduser().resolve(), Path(robot).expanduser().resolve()
    if not umi_path.is_file() or not robot_path.is_file():
        raise FileNotFoundError(f"pair 文件不存在: {umi_path} / {robot_path}")
    if not name:
        name = umi_path.parent.name
    return name, umi_path, robot_path


def _load_run(value: str) -> RunData:
    name, umi_path, robot_path = _parse_pair(value)
    umi_t, umi_p, umi_q = _COMPARE.load_umi(umi_path)
    robot_t, robot_T, _, robot_meta = _COMPARE.load_robot(robot_path)
    return RunData(
        name=name,
        umi_path=umi_path,
        robot_path=robot_path,
        umi_t=umi_t,
        umi_T=_pose_matrices(umi_p, umi_q),
        robot_t=robot_t,
        robot_T=robot_T["actual"],
        uses_event_time=bool(robot_meta.get("uses_event_time")),
    )


def _interpolate_robot(run: RunData, offset_s: float, max_gap_s: float) -> tuple[np.ndarray, np.ndarray]:
    query = run.umi_t + offset_s
    p, _, valid_p = _COMPARE.interpolate_vector(
        query, run.robot_t, run.robot_T[:, :3, 3], max_gap_s
    )
    q_xyzw, valid_q = _COMPARE.interpolate_rotation(
        query,
        run.robot_t,
        Rotation.from_matrix(run.robot_T[:, :3, :3]),
        max_gap_s,
    )
    valid = valid_p & valid_q
    if int(valid.sum()) < 40:
        raise ValueError(f"{run.name}: 时间偏移 {offset_s * 1000.0:.3f} ms 后有效配对不足")
    return _pose_matrices(p[valid], q_xyzw[valid]), valid


def _relative_samples(
    run: RunData, offset_s: float, max_gap_s: float
) -> tuple[list[tuple[np.ndarray, np.ndarray]], dict]:
    robot_T, valid = _interpolate_robot(run, offset_s, max_gap_s)
    umi_T = run.umi_T[valid]
    if len(umi_T) != len(robot_T):  # defensive; both are masked identically
        raise RuntimeError("UMI/robot mask mismatch")
    dt = np.diff(run.umi_t[valid])
    median_dt = float(np.median(dt[dt > 0.0])) if np.any(dt > 0.0) else 1.0 / 30.0
    samples: list[tuple[np.ndarray, np.ndarray]] = []
    motion_translation = []
    motion_rotation = []
    motion_vertical = []
    for horizon_s in (0.20, 0.50, 1.00):
        lag = max(1, int(round(horizon_s / median_dt)))
        # Striding avoids overweighting one slow section while preserving the
        # complete 30-fps source trajectory and all genuine motion.
        stride = max(1, lag // 3)
        for i in range(0, len(umi_T) - lag, stride):
            j = i + lag
            A = np.linalg.inv(umi_T[i]) @ umi_T[j]
            B = np.linalg.inv(robot_T[i]) @ robot_T[j]
            trans = float(np.linalg.norm(A[:3, 3]))
            angle = float(Rotation.from_matrix(A[:3, :3]).magnitude())
            if trans < 1.0e-3 and angle < math.radians(0.25):
                continue
            samples.append((A, B))
            motion_translation.append(trans)
            motion_rotation.append(angle)
            motion_vertical.append(abs(float(A[2, 3])))
    if len(samples) < 30:
        raise ValueError(f"{run.name}: 可观测相对运动不足 ({len(samples)} 对)")
    return samples, {
        "paired_samples": int(valid.sum()),
        "pair_coverage": float(valid.mean()),
        "relative_pairs": len(samples),
        "translation_excitation_m": float(np.percentile(motion_translation, 95)),
        "rotation_excitation_deg": float(np.degrees(np.percentile(motion_rotation, 95))),
        "vertical_excitation_m": float(np.percentile(motion_vertical, 95)),
        "timestamp_source": "can_kernel_rx_timestamp" if run.uses_event_time else "host_wall_epoch_s_legacy",
    }


def _initial_transform(path: Path | None) -> np.ndarray:
    if path is None:
        return np.eye(4)
    value = json.loads(path.resolve().read_text(encoding="utf-8"))
    if "T_body_gripper" in value:
        matrix = np.asarray(value["T_body_gripper"], dtype=float)
    elif "T_gripper_camera" in value:
        body_path = Path(value.get("body_to_camera", ""))
        if not body_path.is_file():
            raise ValueError("手眼 JSON 只有 T_gripper_camera；请同时用 --body-to-camera 提供 VINS 外参")
        body_to_camera = _COMPARE.load_transform(body_path, "body_T_cam0")
        matrix = body_to_camera @ np.linalg.inv(
            np.asarray(value["T_gripper_camera"], dtype=float)
        )
    else:
        matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 4):
        raise ValueError(f"初值必须是4x4矩阵: {path}")
    return _COMPARE._validate_transform(matrix, path.resolve(), "T_body_gripper")


def _residual_vector(x: np.ndarray, relative: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    R_x = Rotation.from_rotvec(x[:3]).as_matrix()
    t_x = x[3:6]
    residuals: list[np.ndarray] = []
    # 0.10 m/rad keeps rotation and translation numerically comparable for a
    # roughly hand-held lever arm, while the Huber loss rejects bad segments.
    rotation_scale_m = 0.10
    for A, B in relative:
        r_err = Rotation.from_matrix(
            A[:3, :3] @ R_x @ B[:3, :3].T @ R_x.T
        ).as_rotvec()
        t_err = A[:3, :3] @ t_x + A[:3, 3] - R_x @ B[:3, 3] - t_x
        residuals.append(np.r_[rotation_scale_m * r_err, t_err])
    return np.concatenate(residuals) if residuals else np.zeros(0)


def _fit_transform(relative: list[tuple[np.ndarray, np.ndarray]], initial: np.ndarray) -> tuple[np.ndarray, dict]:
    x0 = np.r_[Rotation.from_matrix(initial[:3, :3]).as_rotvec(), initial[:3, 3]]
    fit = least_squares(
        _residual_vector,
        x0,
        args=(relative,),
        loss="huber",
        f_scale=0.005,
        max_nfev=250,
    )
    out = np.eye(4)
    out[:3, :3] = Rotation.from_rotvec(fit.x[:3]).as_matrix()
    out[:3, 3] = fit.x[3:6]
    raw = _residual_vector(fit.x, relative).reshape((-1, 6))
    translation = np.linalg.norm(raw[:, 3:], axis=1)
    rotation = np.linalg.norm(raw[:, :3], axis=1) / 0.10
    combined = np.sqrt(translation * translation + (0.10 * rotation) ** 2)
    return out, {
        "solver_success": bool(fit.success),
        "solver_message": str(fit.message),
        "iterations": int(fit.nfev),
        "translation_residual_mm": _COMPARE._summary(translation, 1000.0),
        "rotation_residual_deg": _COMPARE._summary(np.degrees(rotation)),
        "combined_residual_mm": _COMPARE._summary(combined, 1000.0),
        "inlier_ratio_10mm": float(np.mean(combined <= 0.010)),
    }


def _evaluate_offset(
    runs: list[RunData], offset_s: float, max_gap_s: float, initial: np.ndarray
) -> tuple[float, np.ndarray, list[dict], list[tuple[np.ndarray, np.ndarray]]]:
    all_relative: list[tuple[np.ndarray, np.ndarray]] = []
    profiles: list[dict] = []
    for run in runs:
        relative, profile = _relative_samples(run, offset_s, max_gap_s)
        all_relative.extend(relative)
        profiles.append(profile)
    estimate, metrics = _fit_transform(all_relative, initial)
    # Translation dominates the product requirement; a small rotational
    # residual is reported separately and still participates in the score.
    score_m = metrics["combined_residual_mm"]["median"] / 1000.0
    score_m += 0.02 * math.radians(metrics["rotation_residual_deg"]["median"])
    return score_m, estimate, profiles, all_relative


def solve(
    runs: list[RunData],
    output: Path,
    *,
    search_min_ms: float,
    search_max_ms: float,
    initial: np.ndarray,
    max_gap_ms: float,
) -> dict:
    if not runs:
        raise ValueError("至少需要一组 --pair")
    if search_min_ms >= search_max_ms:
        raise ValueError("时间偏移搜索区间无效")
    if any(run.uses_event_time for run in runs):
        # Event timestamps already share the host epoch; only a small residual
        # is searched to catch transport calibration bias, never hundreds of ms.
        search_min_ms = max(search_min_ms, -50.0)
        search_max_ms = min(search_max_ms, 50.0)
    max_gap_s = max_gap_ms / 1000.0

    coarse_step_ms = 5.0
    offsets = np.arange(search_min_ms, search_max_ms + coarse_step_ms * 0.5, coarse_step_ms)
    coarse: list[tuple[float, float]] = []
    failures: list[dict] = []
    for candidate_ms in offsets:
        try:
            score, _, _, _ = _evaluate_offset(runs, candidate_ms / 1000.0, max_gap_s, initial)
            coarse.append((score, float(candidate_ms)))
        except (ValueError, RuntimeError) as exc:
            failures.append({"offset_ms": float(candidate_ms), "reason": str(exc)})
    if not coarse:
        raise ValueError("时间偏移搜索没有任何有效重叠")
    coarse.sort()
    best_coarse_score, best_coarse_ms = coarse[0]
    half_window_ms = max(10.0, coarse_step_ms * 2.0)

    def objective(offset_ms: float) -> float:
        try:
            score, _, _, _ = _evaluate_offset(
                runs, float(offset_ms) / 1000.0, max_gap_s, initial
            )
            return score
        except (ValueError, RuntimeError):
            return 1.0e6

    refined = minimize_scalar(
        objective,
        bounds=(max(search_min_ms, best_coarse_ms - half_window_ms), min(search_max_ms, best_coarse_ms + half_window_ms)),
        method="bounded",
        options={"xatol": 0.01},
    )
    offset_ms = float(refined.x)
    score, estimate, profiles, relative = _evaluate_offset(
        runs, offset_ms / 1000.0, max_gap_s, initial
    )
    transform_metrics = _fit_transform(relative, estimate)[1]

    translation = estimate[:3, 3]
    rotation_deg = float(np.degrees(Rotation.from_matrix(estimate[:3, :3]).magnitude()))
    has_translation = any(p["translation_excitation_m"] >= 0.02 for p in profiles)
    has_rotation = any(p["rotation_excitation_deg"] >= 5.0 for p in profiles)
    has_vertical = any(p["vertical_excitation_m"] >= 0.005 for p in profiles)
    diversity = {
        "translation": has_translation,
        "rotation": has_rotation,
        "vertical": has_vertical,
        "requirement": "至少包含平移、旋转、升降激励；缺失时只输出候选，不自动启用",
    }
    robust_ok = (
        len(runs) >= 3
        and has_translation
        and has_rotation
        and has_vertical
        and transform_metrics["combined_residual_mm"]["p95"] <= 15.0
        and transform_metrics["inlier_ratio_10mm"] >= 0.70
    )
    result = {
        "schema": "robot_umi_joint_calibration_v1",
        "result": "PASS_CANDIDATE" if robust_ok else "DIAGNOSTIC_CANDIDATE",
        "auto_apply": False,
        "estimator": "joint_shared_offset_and_SE3_handeye_lever_arm_huber",
        "pair_count": len(runs),
        "robot_query_offset_ms": offset_ms,
        "coarse_offset_ms": best_coarse_ms,
        "offset_score_m": score,
        "offset_search_ms": [search_min_ms, search_max_ms],
        "timestamp_policy": "event-time runs use only small residual search; legacy runs use shared host-wall epoch offset",
        "T_body_gripper_end": estimate.tolist(),
        "handeye_rotation_deg": rotation_deg,
        "lever_arm_body_m": translation.tolist(),
        "motion_diversity": diversity,
        "residual": transform_metrics,
        "runs": [
            {
                "name": run.name,
                "umi": str(run.umi_path),
                "robot": str(run.robot_path),
                **profile,
            }
            for run, profile in zip(runs, profiles)
        ],
        "search_diagnostics": {
            "coarse_best_score_m": best_coarse_score,
            "evaluated_offsets": len(coarse),
            "failed_offsets": failures,
            "coarse_profile": [{"offset_ms": ms, "score_m": sc} for sc, ms in sorted(coarse)[:10]],
        },
        "limitations": [
            "相对运动方程消除每次运行的世界坐标原点，但不能凭空提供绝对基座位置",
            "杠杆臂可观测性依赖多方向旋转；只有单一平移时结果应视为退化候选",
            "该文件不会覆盖 Docker2 formal_runtime_calibration 或 VINS 配置",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="多段平移/旋转/升降数据联合鲁棒估计时间偏移、手眼旋转和TCP杠杆臂")
    parser.add_argument("--pair", action="append", required=True, help="name=vio_corrected_stream.csv:robot_joints.jsonl，可重复")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--search-min-ms", type=float, default=-250.0)
    parser.add_argument("--search-max-ms", type=float, default=250.0)
    parser.add_argument("--max-interpolation-gap-ms", type=float, default=80.0)
    parser.add_argument("--initial-handeye", type=Path, help="可选4x4 JSON，作为SE(3)初值，不作为强制答案")
    args = parser.parse_args()
    runs = [_load_run(value) for value in args.pair]
    initial = _initial_transform(args.initial_handeye)
    result = solve(
        runs,
        args.out.resolve(),
        search_min_ms=args.search_min_ms,
        search_max_ms=args.search_max_ms,
        initial=initial,
        max_gap_ms=args.max_interpolation_gap_ms,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
