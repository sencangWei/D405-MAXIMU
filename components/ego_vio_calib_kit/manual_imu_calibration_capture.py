#!/usr/bin/env python3
"""终端人工门控的 IMU 标定与验证原始数据采集。

每个项目按一次回车开始，完成动作后再按一次回车结束并立即保存。
IMU 本体标定使用静止偏置、六面静止和三轴已知角度旋转；已知距离
平移仅用于短时惯导验证，不用于求 IMU 内参。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml


PROJECT = Path("/home/robot/ego_vio_humble")
sys.path.insert(0, str(PROJECT))

from ego_vio.config import load_config
from ego_vio.imu.imu_reader import ImuReader


@dataclass(frozen=True)
class Row:
    ts: float
    rx_time: float
    counter: int
    gyro: tuple[float, float, float]
    accel: tuple[float, float, float]
    temp: float


STATIC_BIAS_SEGMENTS = [
    {
        "slug": "static_bias",
        "name": "工作姿态静止偏置",
        "instruction": "按实际安装姿态固定设备，完全不碰设备和线缆，连续静止至少 60 秒。",
        "recommended_duration_s": 60.0,
        "purpose": "当前温度下的陀螺零偏和短时静态噪声",
    },
]

SIX_FACE_SEGMENTS = [
    {
        "slug": f"static_{sign_name}_{axis.lower()}",
        "name": f"六面静止：{sign}{axis} 朝上",
        "instruction": (
            f"用支架让 IMU 标注的 {sign}{axis} 方向竖直朝上，放稳后完全不碰设备和线缆，"
            "连续静止至少 20 秒。"
        ),
        "recommended_duration_s": 20.0,
        "purpose": "加速度计零偏、逐轴比例和轴间串扰检查",
    }
    for axis in "XYZ"
    for sign, sign_name in (("+", "pos"), ("-", "neg"))
]

ROTATION_SEGMENTS = [
    {
        "slug": "rotate_roll",
        "name": "Roll 旋转（绕 X 轴）",
        "instruction": "绕 X/Roll 轴缓慢完成：+90°→0°→-90°→0°，重复 3 轮；每个端点停稳约 2 秒。用支架/把手操作，不要碰 IMU 和附近线缆。",
        "recommended_duration_s": 20.0,
        "purpose": "陀螺 X 轴比例、符号、轴间串扰和角度积分验证",
    },
    {
        "slug": "rotate_pitch",
        "name": "Pitch 旋转（绕 Y 轴）",
        "instruction": "绕 Y/Pitch 轴缓慢完成：+90°→0°→-90°→0°，重复 3 轮；每个端点停稳约 2 秒。用支架/把手操作，不要碰 IMU 和附近线缆。",
        "recommended_duration_s": 20.0,
        "purpose": "陀螺 Y 轴比例、符号、轴间串扰和角度积分验证",
    },
    {
        "slug": "rotate_yaw",
        "name": "Yaw 旋转（绕 Z 轴）",
        "instruction": "绕 Z/Yaw 轴缓慢完成：+90°→0°→-90°→0°，重复 3 轮；每个端点停稳约 2 秒。用支架/把手操作，不要碰 IMU 和附近线缆。",
        "recommended_duration_s": 20.0,
        "purpose": "陀螺 Z 轴比例、符号、轴间串扰和角度积分验证",
    },
]

TRANSLATION_SEGMENTS = [
    {
        "slug": "translate_x_validation",
        "name": "X 轴已知距离验证",
        "instruction": "保持姿态不转，沿 X 轴完成：+5 cm→原点→-5 cm→原点→+8 cm→原点→-8 cm→原点。每个端点停稳约 1 秒。",
        "recommended_duration_s": 12.0,
        "purpose": "短时位置积分验证；不参与 IMU 内参求解",
    },
    {
        "slug": "translate_y_validation",
        "name": "Y 轴已知距离验证",
        "instruction": "保持姿态不转，沿 Y 轴完成：+5 cm→原点→-5 cm→原点→+8 cm→原点→-8 cm→原点。每个端点停稳约 1 秒。",
        "recommended_duration_s": 12.0,
        "purpose": "短时位置积分验证；不参与 IMU 内参求解",
    },
    {
        "slug": "translate_z_validation",
        "name": "Z 轴已知距离验证",
        "instruction": "保持姿态不转，沿 Z 轴完成：+5 cm→原点→-5 cm→原点→+8 cm→原点→-8 cm→原点。每个端点停稳约 1 秒。",
        "recommended_duration_s": 12.0,
        "purpose": "短时位置积分验证；不参与 IMU 内参求解",
    },
]

ALLAN_SEGMENTS = [
    {
        "slug": "static_allan",
        "name": "长静止 Allan 噪声采集",
        "instruction": "固定设备并保持环境温度稳定，完全不碰设备和线缆，连续静止 30～120 分钟；建议至少 60 分钟。",
        "recommended_duration_s": 3600.0,
        "purpose": "噪声密度、随机游走和零偏不稳定性",
    },
]

PROFILE_SEGMENTS = {
    "intrinsic": STATIC_BIAS_SEGMENTS + SIX_FACE_SEGMENTS + ROTATION_SEGMENTS,
    "validation": TRANSLATION_SEGMENTS,
    "allan": ALLAN_SEGMENTS,
    "all": STATIC_BIAS_SEGMENTS + SIX_FACE_SEGMENTS + ROTATION_SEGMENTS + TRANSLATION_SEGMENTS + ALLAN_SEGMENTS,
}


class ManualCapture:
    def __init__(self, port: str, baud: int, output_dir: Path, profile: str):
        self.port = port
        self.baud = baud
        self.output_dir = output_dir
        self.profile = profile
        self.segments = PROFILE_SEGMENTS[profile]
        self.lock = threading.Lock()
        self.rows: list[Row] = []
        self.completed: list[dict] = []
        self.reader = ImuReader(
            port=port,
            baud=baud,
            on_sample=self.on_sample,
            name="manual-imu-calibration",
        )

    def on_sample(self, sample) -> None:
        row = Row(
            ts=float(sample.ts),
            rx_time=float(sample.rx_time),
            counter=int(sample.counter),
            gyro=(float(sample.gx), float(sample.gy), float(sample.gz)),
            accel=(float(sample.ax), float(sample.ay), float(sample.az)),
            temp=float(sample.temp),
        )
        with self.lock:
            self.rows.append(row)

    def snapshot(self) -> list[Row]:
        with self.lock:
            return list(self.rows)

    @staticmethod
    def stable(rows: list[Row]) -> tuple[bool, float, float]:
        if len(rows) < 300:
            return False, float("nan"), float("nan")
        accel = np.asarray([row.accel for row in rows[-400:]])
        gyro = np.asarray([row.gyro for row in rows[-400:]])
        accel_std = float(np.max(np.std(accel, axis=0)))
        gyro_std = float(np.max(np.std(gyro, axis=0)))
        return accel_std < 0.006 and gyro_std < 1.0, accel_std, gyro_std

    def wait_for_baseline(self, segment: dict) -> list[Row]:
        while True:
            print("\n" + "=" * 72)
            print(f"准备：{segment['name']}")
            print(segment["instruction"])
            input("请先回到起始位置并完全放稳，然后按回车检查并开始录制……")
            rows = self.snapshot()
            baseline = rows[-400:]
            passed, accel_std, gyro_std = self.stable(baseline)
            if passed:
                print(
                    f"✓ 起始静止通过：加速度波动 {accel_std:.4f} g，"
                    f"角速度波动 {gyro_std:.3f} °/s"
                )
                return baseline
            print(
                f"✗ 尚未放稳：加速度波动 {accel_std:.4f} g，"
                f"角速度波动 {gyro_std:.3f} °/s。请放稳后重新按回车。"
            )

    @staticmethod
    def arrays(rows: list[Row]) -> dict[str, np.ndarray]:
        return {
            "timestamp_s": np.asarray([row.ts for row in rows]),
            "receiver_timestamp_s": np.asarray([row.rx_time for row in rows]),
            "counter": np.asarray([row.counter for row in rows], dtype=np.uint32),
            "gyro_deg_s": np.asarray([row.gyro for row in rows]),
            "accel_g": np.asarray([row.accel for row in rows]),
            "temperature_c": np.asarray([row.temp for row in rows]),
        }

    def save_segment(
        self,
        index: int,
        segment: dict,
        baseline: list[Row],
        captured: list[Row],
        partial: bool = False,
    ) -> dict:
        path = self.output_dir / f"{index:02d}_{segment['slug']}.npz"
        payload = self.arrays(captured)
        payload.update({f"baseline_{key}": value for key, value in self.arrays(baseline).items()})
        np.savez_compressed(path, **payload)

        counters = payload["counter"].astype(np.int64)
        differences = np.diff(counters)
        resets = int(np.count_nonzero((differences < 0) & (counters[1:] == 1)))
        drops = int(np.count_nonzero((differences != 1) & ~((differences < 0) & (counters[1:] == 1))))
        duration = (
            float(payload["timestamp_s"][-1] - payload["timestamp_s"][0])
            if len(captured) >= 2
            else 0.0
        )
        rate = (len(captured) - 1) / duration if duration > 0.0 else 0.0
        result = {
            "index": index,
            "name": segment["name"],
            "slug": segment["slug"],
            "instruction": segment["instruction"],
            "purpose": segment["purpose"],
            "recommended_duration_s": segment["recommended_duration_s"],
            "recommended_duration_passed": duration >= segment["recommended_duration_s"],
            "file": str(path.resolve()),
            "complete": not partial,
            "samples": len(captured),
            "duration_s": duration,
            "mean_rate_hz": rate,
            "counter_resets": resets,
            "counter_drops": drops,
        }
        self.completed.append(result)
        self.save_manifest(complete=False)
        return result

    def save_manifest(self, complete: bool) -> Path:
        path = self.output_dir / "manifest.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "complete": complete,
                    "port": self.port,
                    "baud": self.baud,
                    "profile": self.profile,
                    "notes": [
                        "intrinsic 用于基础 IMU 本体标定原始数据",
                        "validation 的已知距离平移只作短时积分验证，不用于求 IMU 内参",
                        "allan 应单独长时间静止采集，用于随机噪声参数",
                    ],
                    "segments": self.completed,
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    def run(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=False)
        if not self.reader.start():
            raise RuntimeError(f"无法独占打开 IMU 串口：{self.port}")
        active = None
        try:
            print(f"\nIMU 人工门控采集（模式：{self.profile}，共 {len(self.segments)} 项）")
            print(f"输出目录：{self.output_dir.resolve()}")
            print("每项：第一次回车开始，第二次回车结束并保存。")
            print("警告：不要直接触碰 IMU、固定胶带或附近线缆，请通过刚性支架移动。")
            print("正在等待至少 1 秒 IMU 数据……")
            deadline = time.monotonic() + 5.0
            while len(self.snapshot()) < 400 and time.monotonic() < deadline:
                time.sleep(0.02)
            if len(self.snapshot()) < 300:
                raise RuntimeError("5 秒内未收到足够 IMU 数据")

            for index, segment in enumerate(self.segments, start=1):
                baseline = self.wait_for_baseline(segment)
                with self.lock:
                    start_index = len(self.rows)
                active = (index, segment, baseline, start_index)
                print(f"\n● 正在录制：{segment['name']}")
                print(segment["instruction"])
                input("完成全部动作并放稳后，按回车结束本项录制……")
                with self.lock:
                    captured = list(self.rows[start_index:])
                result = self.save_segment(index, segment, baseline, captured)
                active = None
                print(
                    f"✓ 已保存：{Path(result['file']).name}\n"
                    f"  时长 {result['duration_s']:.2f} s，样本 {result['samples']}，"
                    f"平均 {result['mean_rate_hz']:.1f} Hz，丢帧 {result['counter_drops']}，"
                    f"计数器复位 {result['counter_resets']}"
                )
                if not result["recommended_duration_passed"]:
                    print(
                        f"  ⚠ 本项短于建议的 {result['recommended_duration_s']:.0f} 秒；"
                        "数据已保存，但分析时应判为时长不足并重录。"
                    )

            manifest = self.save_manifest(complete=True)
            print("\n" + "=" * 72)
            print(f"✓ {self.profile} 模式的 {len(self.segments)} 个单元全部录制完成")
            print(f"索引文件：{manifest.resolve()}")
            return manifest
        except KeyboardInterrupt:
            if active is not None:
                index, segment, baseline, start_index = active
                with self.lock:
                    captured = list(self.rows[start_index:])
                if captured:
                    self.save_segment(index, segment, baseline, captured, partial=True)
                    print("\n当前未完成项已保存为 partial。")
            manifest = self.save_manifest(complete=False)
            print(f"\n采集已中止；已完成项仍保留在 {manifest.resolve()}")
            return manifest
        finally:
            self.reader.stop()


def self_test() -> None:
    assert [segment["slug"] for segment in PROFILE_SEGMENTS["intrinsic"]] == [
        "static_bias",
        "static_pos_x",
        "static_neg_x",
        "static_pos_y",
        "static_neg_y",
        "static_pos_z",
        "static_neg_z",
        "rotate_roll",
        "rotate_pitch",
        "rotate_yaw",
    ]
    assert len(PROFILE_SEGMENTS["validation"]) == 3
    assert len(PROFILE_SEGMENTS["allan"]) == 1
    assert len(PROFILE_SEGMENTS["all"]) == 14
    rows = [
        Row(float(index) / 400.0, float(index) / 400.0, index + 1, (0.0, 0.0, 0.0), (0.0, -1.0, 0.0), 25.0)
        for index in range(400)
    ]
    passed, accel_std, gyro_std = ManualCapture.stable(rows)
    assert passed and accel_std == 0.0 and gyro_std == 0.0
    print("SELF_TEST_OK: profiles, ten intrinsic segments, stable baseline, enter-to-start/stop flow")


def main() -> int:
    parser = argparse.ArgumentParser(description="中文回车式 IMU 标定/验证原始数据采集")
    parser.add_argument("--config", default=str(PROJECT / "config/devices_ubuntu.yaml"), help="设备配置 YAML")
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILE_SEGMENTS),
        default="intrinsic",
        help="intrinsic=本体标定；validation=已知距离验证；allan=长静止噪声；all=依次全部采集",
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="输出目录（默认按时间创建）")
    parser.add_argument("--self-test", action="store_true", help="不连接硬件，只运行内置自检")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    unit = load_config(args.config).units[0]
    output_dir = args.output_dir or Path("imu_manual_calibration") / f"{args.profile}_{datetime.now():%Y%m%d_%H%M%S}"
    ManualCapture(unit.imu.port, unit.imu.baud, output_dir, args.profile).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
