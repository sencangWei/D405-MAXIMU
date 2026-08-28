#!/usr/bin/env python3
"""Run the two pending camera-IMU solves in the isolated Kalibr image."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


APRILGRID_CONTAINER_ALIASES = frozenset({
    Path("/home/robot/ego_vio_humble/config/aprilgrid_6x6_35mm.yaml"),
    Path(
        "/home/robot/releases/ego_vio_humble/product_v1_20260824/"
        "config/aprilgrid_6x6_35mm.yaml"
    ),
})


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def map_container_path(raw: str, host_data_root: Path,
                       target_asset: Path) -> Path:
    path = Path(raw)
    if path == Path("/data") or str(path).startswith("/data/"):
        return host_data_root / path.relative_to("/data")
    if path in APRILGRID_CONTAINER_ALIASES:
        return target_asset
    raise ValueError(f"清单含未授权的容器路径: {path}")


def require_within(path: Path, root: Path, label: str) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise ValueError(f"{label}越出允许目录: {resolved}") from exc
    return resolved


def verified_file(item: dict, path_key: str, hash_key: str,
                  host_data_root: Path, target_asset: Path,
                  *, allowed_root: Path | None = None) -> Path:
    raw = item.get(path_key)
    expected = item.get(hash_key)
    if not raw or not expected:
        raise ValueError(f"清单缺少{path_key}/{hash_key}")
    path = map_container_path(str(raw), host_data_root, target_asset).resolve()
    if allowed_root is not None:
        path = require_within(path, allowed_root, path_key)
    if not path.is_file():
        raise ValueError(f"输入文件不存在: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"输入SHA-256不匹配: {path} expected={expected} actual={actual}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="隔离求解两轮D405相机-IMU标定")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--host-data-root", type=Path, required=True)
    parser.add_argument("--calibration-kit", type=Path, required=True)
    parser.add_argument("--target-asset", type=Path, required=True)
    args = parser.parse_args()

    host_data_root = args.host_data_root.resolve()
    manifest_path = require_within(args.manifest, host_data_root, "分段标定清单")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if manifest.get("format_version") != 1:
        raise ValueError("不支持的分段标定清单版本")
    if manifest.get("status") != "CAPTURED_PENDING_KALIBR_SOLVE":
        raise ValueError("分段标定清单状态错误")
    if manifest.get("solver_contract", {}).get("command") != "kalibr_calibrate_imu_camera":
        raise ValueError("清单求解器合同错误")

    kit = args.calibration_kit.resolve()
    sys.path.insert(0, str(kit))
    from product_calibration.kalibr_pipeline import run_command

    target_asset = args.target_asset.resolve()
    product_id = str(manifest.get("product_id", ""))
    if not product_id or "/" in product_id:
        raise ValueError("清单product_id非法")
    session_root = host_data_root / "calibration_sessions" / product_id
    attempt = require_within(
        map_container_path(manifest.get("attempt", ""), host_data_root, target_asset),
        session_root / "camera_imu/attempts", "camera-IMU attempt",
    )
    if not attempt.is_dir():
        raise ValueError(f"camera-IMU attempt不存在: {attempt}")
    inputs = manifest.get("inputs") or {}
    stereo = verified_file(
        inputs, "stereo_camchain", "stereo_camchain_sha256",
        host_data_root, target_asset, allowed_root=session_root,
    )
    imu = verified_file(
        inputs, "imu_yaml", "imu_yaml_sha256", host_data_root, target_asset,
        allowed_root=session_root,
    )
    target = verified_file(
        inputs, "target", "target_sha256", host_data_root, target_asset,
    )

    runs = manifest.get("runs") or []
    if len(runs) != 2 or [run.get("index") for run in runs] != [1, 2]:
        raise ValueError("清单必须恰好包含run1/run2")
    expected_tag = "ego-vio-kalibr:1f602274-minimal"
    expected_image_id = (
        "sha256:4e1506d4ff12b1c6918441ca514bc0001f4c10bf17efe0283b5db1453640f863"
    )
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("主机缺少docker命令")
    inspected = subprocess.run(
        [docker, "image", "inspect", expected_tag, "--format", "{{.Id}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if inspected != expected_image_id:
        raise ValueError(
            f"Kalibr镜像ID不匹配: tag={expected_tag} expected={expected_image_id} actual={inspected}"
        )
    expected_solver_evidence = require_within(
        map_container_path(
            manifest.get("expected_solver_evidence", ""),
            host_data_root, target_asset,
        ),
        attempt, "Kalibr求解证据",
    )
    if expected_solver_evidence != attempt / "kalibr_solver_evidence.yaml":
        raise ValueError("Kalibr求解证据路径违反合同")
    solved_runs = []
    for index, run in enumerate(runs, 1):
        run_root = attempt / f"run_{index}"
        bag = verified_file(
            run, "kalibr_bag", "kalibr_bag_sha256", host_data_root, target_asset,
            allowed_root=run_root,
        )
        for path_key, hash_key in (
            ("raw_imu", "raw_imu_sha256"),
            ("camera_health_file", "camera_health_file_sha256"),
            ("camera_timestamp_file", "camera_timestamp_file_sha256"),
            ("imu_health_file", "imu_health_file_sha256"),
            ("imu_timestamp_file", "imu_timestamp_file_sha256"),
        ):
            verified_file(
                run, path_key, hash_key, host_data_root, target_asset,
                allowed_root=run_root,
            )
        expected_camchain = map_container_path(
            run["expected_camchain"], host_data_root, target_asset
        ).resolve()
        expected_results = map_container_path(
            run["expected_results"], host_data_root, target_asset
        ).resolve()
        solve_dir = run_root / "solve"
        if expected_camchain != solve_dir / "imucam-camchain-imucam.yaml":
            raise ValueError(f"run{index} camchain输出路径违反合同")
        if expected_results != solve_dir / "imucam-results-imucam.txt":
            raise ValueError(f"run{index} results输出路径违反合同")
        solve_dir.mkdir(parents=True, exist_ok=True)
        local_bag = solve_dir / "imucam.bag"
        if local_bag.exists() or local_bag.is_symlink():
            if local_bag.resolve() != bag:
                raise ValueError(f"run{index}已有imucam.bag指向其他输入")
        else:
            os.symlink(bag, local_bag)
        camchain, results = expected_camchain, expected_results
        command = [
            docker, "run", "--rm", "--init", "--network", "none",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-e", "MPLCONFIGDIR=/tmp/kalibr-matplotlib",
            "--mount", (
                f"type=bind,src={host_data_root},dst={host_data_root},readonly"
            ),
            "--mount", f"type=bind,src={solve_dir},dst={solve_dir}",
            "--mount", (
                f"type=bind,src={target_asset},dst={target_asset},readonly"
            ),
            "--mount", "type=bind,src=/tmp,dst=/tmp",
            "-w", str(solve_dir), expected_image_id,
            "kalibr_calibrate_imu_camera",
            "--bag", str(local_bag), "--cam", str(stereo),
            "--imu", str(imu), "--target", str(target),
            "--dont-show-report",
        ]
        run_command(
            command, solve_dir, solve_dir / "kalibr_imucam.log",
            completed_outputs=(camchain, results),
        )
        if camchain.resolve() != expected_camchain or results.resolve() != expected_results:
            raise ValueError(f"run{index}求解器输出路径违反清单合同")
        log = solve_dir / "kalibr_imucam.log"
        if not log.is_file() or log.stat().st_size == 0:
            raise ValueError(f"run{index} Kalibr日志缺失")
        solved_runs.append({
            "index": index,
            "camchain": run["expected_camchain"],
            "camchain_sha256": sha256_file(camchain),
            "results": run["expected_results"],
            "results_sha256": sha256_file(results),
            "log": str(Path(run["expected_camchain"]).parent / "kalibr_imucam.log"),
            "log_sha256": sha256_file(log),
        })
        print(f"run{index} Kalibr求解完成: {camchain}")
    solver_evidence = {
        "format_version": 1,
        "result": "PASS",
        "input_manifest": str(Path("/data") / manifest_path.relative_to(host_data_root)),
        "input_manifest_sha256": sha256_file(manifest_path),
        "kalibr_image_tag": expected_tag,
        "kalibr_image_id": expected_image_id,
        "network": "disabled_by_explicit_docker_run_flag",
        "runs": solved_runs,
    }
    expected_solver_evidence.write_text(
        yaml.safe_dump(solver_evidence, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"KALIBR_SPLIT_SOLVE_PASS image={expected_tag} id={expected_image_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
