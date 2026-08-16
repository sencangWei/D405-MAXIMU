#!/usr/bin/env python3
"""Classify this audited recording set and optionally quarantine legacy sessions.

Default is read-only. --apply-quarantine only renames an explicit, frozen set of
91 reviewed session directories to a sibling directory on the same filesystem.
Unknown/new sessions are never moved.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path


RECORDINGS = Path("/home/robot/ego_vio_humble/recordings")
QUARANTINE = Path("/home/robot/ego_vio_recordings_legacy_quarantine_20260816")
HANDOFF = Path("/home/robot/ego_vio_humble/JAZZY_HANDOFF_20260816")

RERECORDABLE = {
    "d405_720p_all_20260804_152741",
    "d405_720p_all_20260804_201730",
    "d405_720p_all_20260804_202306",
    "d405_720p_all_20260804_202935",
    "d405_720p_rgb_stereo_ir_20260808_205555",
    "d405_720p_rgb_stereo_ir_20260808_205703",
    "d405_720p_rgb_stereo_ir_20260810_200101",
    "d405_720p_rgb_stereo_ir_20260811_192956",
    "d405_720p_rgb_stereo_ir_20260812_101410",
    "d405_mp4_inline_20260810_200218",
}

EVIDENCE = {
    "calib_20260808_192211",
    "d405_720p_all_20260803_203800",
    "d405_720p_all_20260803_204009",
    "d405_720p_all_20260803_204726",
    "d405_720p_all_20260804_145508",
    "d405_720p_all_20260804_145842",
    "d405_720p_all_20260804_150612",
    "d405_720p_all_20260804_151127",
    "d405_720p_all_20260804_152711",
    "d405_720p_all_20260804_215042",
    "d405_720p_all_20260804_215229",
    "d405_720p_all_20260807_115453",
    "d405_720p_all_20260810_001100",
    "d405_720p_rgb_stereo_ir_20260806_192749",
    "d405_720p_rgb_stereo_ir_20260807_115333",
    "d405_720p_rgb_stereo_ir_20260808_134948",
    "d405_720p_rgb_stereo_ir_20260808_230503",
    "d405_720p_rgb_stereo_ir_20260809_111538",
    "d405_720p_rgb_stereo_ir_20260810_000943",
    "d405_720p_rgb_stereo_ir_20260810_000943_ffv1prod",
    "imu_static_20260807_234253",
}

LEGACY = {
    "calib_20260801_211449", "calib_20260801_211544",
    "calib_20260802_125948", "calib_20260802_130435",
    "calib_20260802_130920", "calib_20260802_132504",
    "calib_20260802_134324", "calib_20260802_135754",
    "calib_20260802_135943", "calib_20260802_141404",
    "calib_20260802_141820", "calib_20260802_142013",
    "calib_20260802_142734", "calib_20260802_142929",
    "calib_20260802_143041", "calib_20260804_192306",
    "calib_20260804_192406", "calib_20260804_192618",
    "calib_20260804_193636", "calib_20260804_194345",
    "calib_20260808_173952", "calib_20260808_174103",
    "calib_20260808_174150", "calib_20260808_174245",
    "calib_20260808_174547", "calib_20260808_174801",
    "calib_20260808_174903", "calib_20260808_180739",
    "calib_20260808_180755", "calib_20260808_182106",
    "calib_20260808_182310", "d405_720p_all_20260803_203706",
    "d405_720p_all_20260803_204624", "d405_720p_all_20260804_150359",
    "d405_720p_all_20260804_201834", "d405_720p_all_20260804_202202",
    "d405_720p_all_20260804_202832", "d405_720p_all_20260806_192437",
    "d405_720p_rgb_stereo_ir_20260806_233939",
    "d405_720p_rgb_stereo_ir_20260809_094342",
    "d405_720p_rgb_stereo_ir_20260809_094450",
    "d405_720p_rgb_stereo_ir_20260809_094811",
    "d405_720p_rgb_stereo_ir_20260809_102729",
    "d405_720p_rgb_stereo_ir_20260810_000826",
    "d405_720p_rgb_stereo_ir_20260810_000853",
    "d405_720p_rgb_stereo_ir_20260811_231527",
    "d405_720p_rgb_stereo_ir_20260811_231818",
    "d405_720p_rgb_stereo_ir_20260811_232602",
    "d405_720p_rgb_stereo_ir_20260811_234953",
    "d405_720p_rgb_stereo_ir_20260811_235350",
    "d405_720p_rgb_stereo_ir_20260812_101310",
    "d405_720p_rgb_stereo_ir_20260812_151059",
    "d405_720p_rgb_stereo_ir_20260812_155253",
    "d405_720p_rgb_stereo_ir_20260815_101756",
    "d405_four_stream_20260803_212414",
    "d405_four_stream_20260803_212647",
    "d405_four_stream_20260804_143635",
    "d405_mp4_inline_20260808_163227", "d405_mp4_inline_20260808_163454",
    "d405_mp4_inline_20260808_163546", "d405_mp4_inline_20260808_163609",
    "d405_mp4_inline_20260808_163752", "d405_mp4_inline_20260808_164853",
    "d405_mp4_inline_20260808_164915", "d405_mp4_inline_20260808_164938",
    "d405_mp4_inline_20260810_183547", "d405_mp4_inline_20260810_183707",
    "d405_mp4_inline_20260810_184028", "d405_native_smoke_20260803_2114",
    "d405_nvenc_20260808_123222", "d405_supported3_20260803_220756",
    "d405_supported3_20260803_221623", "diagnostic_ab_20260802_164311",
    "imu_only_formal_20260802_165628", "motion_accept_20260802",
    "pre_calib_720p_smoke", "session_20260802_002210",
    "session_20260802_004908", "session_20260804_182652",
    "static_clock_20260802", "static_clock_frozen_20260802",
} | RERECORDABLE


def stats(path: Path) -> tuple[int, int]:
    count = 0
    size = 0
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
            size += item.stat().st_size
    return count, size


def acceptance(path: Path) -> tuple[str, str]:
    report = path / "acceptance.json"
    if not report.is_file():
        return "", ""
    try:
        raw = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "INVALID", ""
    backend = raw.get("capture_backend") or raw.get("camera", {}).get(
        "capture_backend", ""
    )
    return str(raw.get("result", "")), str(backend)


def classify(path: Path) -> tuple[str, str]:
    result, backend = acceptance(path)
    if result == "PASS" and "RSUSB" in backend:
        return "CURRENT_ACTIVE", "RSUSB正式采集PASS"
    if path.name in EVIDENCE:
        return "REGRESSION_EVIDENCE", "被回归/标定/AB/故障报告引用"
    if path.name in RERECORDABLE:
        return "LEGACY_DO_NOT_RUN", "结果报告已保留，可在新机按需重录"
    if path.name in LEGACY:
        return "LEGACY_DO_NOT_RUN", "已由当前标定或RSUSB正式数据替代"
    return "REVIEW_REQUIRED", "审计后新增或未识别，绝不自动移动"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-quarantine", action="store_true")
    args = parser.parse_args()

    root = RECORDINGS.resolve(strict=True)
    if root != RECORDINGS:
        raise RuntimeError(f"录制目录解析异常: {root}")
    active_sessions = sorted(path for path in root.iterdir() if path.is_dir())
    quarantined_sessions = (
        sorted(path for path in QUARANTINE.iterdir() if path.is_dir())
        if QUARANTINE.is_dir()
        else []
    )
    active_names = {path.name for path in active_sessions}
    quarantined_names = {path.name for path in quarantined_sessions}
    overlap = active_names & quarantined_names
    if overlap:
        raise RuntimeError("活跃区与隔离区重名，拒绝继续: " + ", ".join(sorted(overlap)))
    sessions = sorted(active_sessions + quarantined_sessions, key=lambda path: path.name)
    rows = []
    for path in sessions:
        category, reason = classify(path)
        files, size = stats(path)
        result, backend = acceptance(path)
        rows.append((path.name, category, reason, result, backend, files, size))

    review = [row for row in rows if row[1] == "REVIEW_REQUIRED"]
    if review:
        raise RuntimeError(
            "存在未分类会话，拒绝隔离: " + ", ".join(row[0] for row in review)
        )
    legacy_rows = [row for row in rows if row[1] == "LEGACY_DO_NOT_RUN"]
    if {row[0] for row in legacy_rows} != LEGACY:
        raise RuntimeError("实际legacy集合与冻结审计集合不一致，拒绝移动")
    unexpected_quarantine = quarantined_names - LEGACY
    if unexpected_quarantine:
        raise RuntimeError(
            "隔离区含未审计目录，拒绝继续: "
            + ", ".join(sorted(unexpected_quarantine))
        )

    for category in ("CURRENT_ACTIVE", "REGRESSION_EVIDENCE", "LEGACY_DO_NOT_RUN"):
        subset = [row for row in rows if row[1] == category]
        print(
            f"{category}: {len(subset)} sessions, "
            f"{sum(row[6] for row in subset) / 2**30:.3f} GiB"
        )

    manifest = HANDOFF / "LEGACY_QUARANTINE_MANIFEST.tsv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ["session", "category", "reason", "acceptance", "backend", "files", "bytes"]
        )
        writer.writerows(rows)

    if not args.apply_quarantine:
        print(f"DRY-RUN: manifest={manifest}")
        return 0

    targets = [
        (root / name, QUARANTINE / name) for name in sorted(active_names & LEGACY)
    ]
    if not targets and quarantined_names == LEGACY:
        print(f"ALREADY_QUARANTINED: {len(LEGACY)} sessions -> {QUARANTINE}")
        return 0
    if QUARANTINE.exists() and (QUARANTINE.is_symlink() or not QUARANTINE.is_dir()):
        raise RuntimeError(f"隔离路径不是普通目录，拒绝继续: {QUARANTINE}")
    quarantine_parent = QUARANTINE if QUARANTINE.is_dir() else QUARANTINE.parent
    if os.stat(root).st_dev != os.stat(quarantine_parent).st_dev:
        raise RuntimeError("目标父目录不在同一文件系统，拒绝非原子移动")
    for source, target in targets:
        if source.parent.resolve() != root or source.is_symlink() or not source.is_dir():
            raise RuntimeError(f"源目标验证失败: {source}")
        if target.exists():
            raise RuntimeError(f"目标已存在: {target}")

    QUARANTINE.mkdir(mode=0o755, exist_ok=True)
    for source, target in targets:
        source.rename(target)
    moved_names = {target.name for _, target in targets}
    if any(source.exists() for source, _ in targets) or not all(
        target.is_dir() and not target.is_symlink() for _, target in targets
    ):
        raise RuntimeError("移动后验证失败，请保留现场并人工检查")
    print(
        f"QUARANTINED: {len(moved_names)} new sessions, "
        f"{len(LEGACY)} total -> {QUARANTINE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
