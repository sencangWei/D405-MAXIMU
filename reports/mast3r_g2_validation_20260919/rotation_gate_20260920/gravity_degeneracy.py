#!/usr/bin/env python3
"""**否定结果**：加速度计（重力方向）在**本数据上**分辨不了恒定体轴姿态偏移。

这条弯路值得记下来，免得以后又重新试一遍 —— 加速度计是唯一与视觉/tracker 无关的
绝对姿态参考，看上去最有希望，但实测灵敏度不够。注意结论是「这条数据不够」，
**不是**「原理上不可能」：一阶分析下表明确给出了信号，只是被污染盖住了。

## 机理

设真姿态 `R_true(t)`，世界系里固定的重力方向单位矢 `u`（含符号）。低动态时
加速度计测的是比力：`a_hat(t) = R_true(t)ᵀ u`。给轨迹右乘恒定体轴偏移 `C`
（`R_S = R_true · C`，正是本报告那个「常量」的形式），用 `R_S` 拟合重力：

    min_u  Σ_t ‖ Cᵀ R_true(t)ᵀ u − R_true(t)ᵀ u_true ‖²

**注意 `Cᵀ` 与 `R_true(t)ᵀ` 不可交换** —— 这一项正是信号来源。
一阶展开（`C = I + θN`，`N` 反对称）在 `u = u_true` 处的残差是
`θ·‖N R_true(t)ᵀ u_true‖`，随姿态扫过而变。所以：

  * 姿态**铺得越开**，重力越能看见 `C`；
  * 姿态**不动**（静止），重力对 `C` 完全失明（`R_trueᵀ u_true` 不动，
    `N R_trueᵀ u_true` 成为可被 `u` 吸收的常量）。

⇒ 不是「原理上不可能」，而是**灵敏度取决于姿态激励**，且必须先把
   平移加速度从加速度计读数里清掉（它比信号大一个量级）。

## 数值演示

对真实轨迹施加已知的恒定体轴偏移 `C`，看重力散度是否随 `C` 变化。
分两种取法对照：
  * **全部样本**（只按 `|‖a‖−g|` 选）—— 平移加速度污染严重；
  * **近静止段**（`|ω|` 小 **且** 轨迹位移小）—— 每段取均值，污染被平均掉。
"""
import json
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
sys.path.insert(0, "/home/robot/ego_vio_humble")
import evaluate_slam_ground_truth as E  # noqa: E402
from ego_vio.imu.vins_transform import load_vins_imu_rotation  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
IMU_CAL = Path("/home/robot/ego_vio_humble/config"
               "/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml")
IMU_DTYPE = np.dtype([("ts", "<f8"), ("counter", "<u4"),
                      ("gx", "<f4"), ("gy", "<f4"), ("gz", "<f4"),
                      ("ax", "<f4"), ("ay", "<f4"), ("az", "<f4"),
                      ("temp", "<f4")])


def load_imu(recording, epoch_offset):
    """→ (ts_epoch, accel_body)。imu.bin 的 ts 是宿主单调钟，轨迹 CSV 是 unix 纪元。"""
    rec = np.fromfile(recording / "external_imu" / "imu.bin", dtype=IMU_DTYPE)
    cal = yaml.safe_load(IMU_CAL.read_text(encoding="utf-8"))["accelerometer"]
    raw = np.column_stack((rec["ax"], rec["ay"], rec["az"])).astype(float)
    acc = (raw @ np.asarray(cal["matrix"]).T + np.asarray(cal["offset_g"])) * 9.805
    gyr = np.column_stack((rec["gx"], rec["gy"], rec["gz"])).astype(float)
    return (rec["ts"].astype(float) + epoch_offset,
            acc @ load_vins_imu_rotation().T, gyr @ load_vins_imu_rotation().T)


def _runs(mask):
    d = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def gravity_spread(R, a_hat, omega, P, t, static=False):
    """重力散度（度）。static=True 时用「近静止段」每段取均值。"""
    if not static:
        sel = np.abs(np.linalg.norm(a_hat, axis=1) - 1.0) < 0.30 / 9.805
        if sel.sum() < 200:
            return None
        W = R.apply(a_hat)
        n = int(sel.sum())
    else:
        keep = []
        for s, e in _runs(omega < 0.30):
            if e - s < 160:                       # ≥0.4 s
                continue
            v = np.linalg.norm(P[e - 1] - P[s]) / max(1e-9, t[e - 1] - t[s])
            if v < 0.002:                          # 位移 < 2 mm/s
                keep.append((s, e))
        if len(keep) < 5:
            return None
        W = Rotation.concatenate([R[s:e].mean() for s, e in keep]).apply(
            np.array([a_hat[s:e].mean(axis=0) for s, e in keep]))
        n = len(keep)
    u = W.mean(axis=0)
    u /= np.linalg.norm(u)
    ang = np.degrees(np.arccos(np.clip(W @ u / np.linalg.norm(W, axis=1), -1, 1)))
    return float(np.sqrt(np.mean(ang ** 2))), n


def main():
    rng = np.random.default_rng(0)
    print("对每条真实轨迹施加已知的恒定体轴偏移 C，看重力散度是否变化")
    print("若散度随 |C| 明显上升 ⇒ 该估计器对 C 有灵敏度（可用于判定归属）\n")
    print(f"{'cell':<40}{'取法':>6}{'无 C':>9}{'+1.5°':>10}{'+3°':>9}{'增量':>9}")
    print("-" * 84)
    for g in sorted({f.parents[2] for f in
                     ROOT.glob("**/fusion_current/tight/trajectory_fused.csv")}):
        prov = g / "lighthouse_ground_truth_provenance.json"
        if not prov.exists():
            continue
        cm = json.loads(prov.read_text())["clock_mapping"]
        rec = Path(cm["d405_frames"]).parent
        if not (rec / "external_imu" / "imu.bin").exists():
            continue
        ts, acc, gyr = load_imu(rec, cm["epoch_minus_monotonic_s"])
        a_hat = acc / np.linalg.norm(acc, axis=1, keepdims=True)
        gw = gyr

        tgt, Pgt, Qg = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        keep = (ts >= tgt[0]) & (ts <= tgt[-1])
        tt = ts[keep]
        R0 = Slerp(tgt, Rotation.from_quat(Qg))(tt)
        P = np.column_stack([np.interp(tt, tgt, Pgt[:, k]) for k in range(3)])
        ah = a_hat[keep]
        om = np.linalg.norm(gw[keep], axis=1)
        name = str(g).replace(str(ROOT) + "/", "")

        for static in (False, True):
            out = []
            for mag in (0.0, 1.5, 3.0):
                R = R0 if mag == 0.0 else R0 * Rotation.from_rotvec(
                    np.radians(mag) * (lambda a: a / np.linalg.norm(a))(rng.normal(size=3)))
                out.append(gravity_spread(R, ah, om, P, tt, static))
            if any(o is None for o in out):
                print(f"{name:<44}{'(样本不足)':>20}  static={static}")
                continue
            (v0, n0), (v1, _), (v2, _) = out
            print(f"{name:<40}{'静止段' if static else '全样本':>6}"
                  f"{v0:>9.3f}{v1:>10.3f}{v2:>9.3f}"
                  f"{v2 - v0:>9.3f}   n={n0}")

    print("\n⇒ 两种取法都不够用（判定标准：给 1.5° 的 C 应产生同向、量级相当的增量）：")
    print("   全样本 —— 位移加速度把散度抬到 2–5°，比 1.5° 的信号大一个量级，")
    print("             增量在 −0.29 ~ +0.56° 间乱跳；")
    print("   近静止段 —— 噪声底低到 0.05–0.8°，但每条 take 只剩 7–12 段，")
    print("             段间散度本身 ~0.5°，与信号同量级，增量 −0.52 ~ +0.72° 无规律。")
    print("\n   结论：**本数据上加速度计判不了常量归属**。要让它可用，需要")
    print("   (a) 更多/更长的静止段且**姿态彼此拉开**，或 (b) 把位移加速度")
    print("   从读数里显式减掉（需要一条足够准的速度）。目前都不具备。")


if __name__ == "__main__":
    main()
