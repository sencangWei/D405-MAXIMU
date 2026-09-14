#!/usr/bin/env python3
"""Publish one sealed UMI v4 session into the immutable EGO M02 catalog."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_recording_root(recording_root: Path, device_id: str) -> None:
    recording_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = recording_root / ".ego-recordings-root.json"
    expected = {
        "schema_version": 1,
        "application": "ego-recorder",
        "device_id": device_id,
    }
    if marker.exists():
        if _json(marker) != expected:
            raise ValueError("recording root belongs to another device")
    else:
        _atomic_json(marker, expected)


def publish_session(
    *,
    session: Path,
    recording_root: Path,
    catalog_db_path: Path,
    device_id: str,
    job_id: str,
    request_id: str,
    boot_id: str,
) -> dict[str, Any]:
    """Atomically publish a native session, ledger, and CatalogDb row."""
    if not session.is_dir() or session.is_symlink():
        raise ValueError("sealed native session directory is unavailable")
    manifest = _json(session / "manifest.json")
    if manifest.get("status") != "SEALED" or manifest.get("session_id") != session.name:
        raise ValueError("native session is not sealed or its identity changed")
    if SAFE_ID.fullmatch(session.name) is None or SAFE_ID.fullmatch(job_id) is None:
        raise ValueError("recording or job identifier is invalid")
    counts = manifest.get("counts")
    metrics = manifest.get("metrics")
    if not isinstance(counts, dict) or not isinstance(metrics, dict):
        raise ValueError("native session metrics are incomplete")
    pairs = counts.get("ir_frames")
    rgb_frames = counts.get("rgb_input_frames")
    imu_samples = counts.get("stm32_packets")
    duration_s = metrics.get("formal_host_span_s")
    if (
        not isinstance(pairs, int)
        or isinstance(pairs, bool)
        or pairs <= 0
        or rgb_frames != pairs
        or not isinstance(imu_samples, int)
        or isinstance(imu_samples, bool)
        or imu_samples <= 0
        or not isinstance(duration_s, (int, float))
        or isinstance(duration_s, bool)
        or duration_s <= 0
    ):
        raise ValueError("native session counters cannot prove STEREO_IMU completion")

    ensure_recording_root(recording_root, device_id)
    completed = recording_root / "recordings-v2" / "completed"
    ledger_dir = recording_root / "recordings-v2" / ".publication-ledger"
    completed.mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The frozen EGO catalog namespace admits only recording_* identifiers.
    # Preserve the native session identity as the suffix rather than changing
    # any UMI payload or its internal manifest.
    recording_id = f"recording_{session.name}"
    if SAFE_ID.fullmatch(recording_id) is None:
        raise ValueError("EGO recording identifier is invalid")
    prepared = completed / f".{recording_id}.publishing"
    final = completed / recording_id
    if prepared.exists() or final.exists():
        raise FileExistsError("recording publication target already exists")

    os.rename(session, prepared)
    try:
        metadata_dir = prepared / "metadata"
        metadata_dir.mkdir(mode=0o700)
        recorded_at = _recorded_at(recording_id)
        metadata = {
            "schema_version": 2,
            "device_id": device_id,
            "recording_id": recording_id,
            "job_id": job_id,
            "request_id": request_id,
            "state": "COMPLETE_LOCAL",
            "sensor_mode": "STEREO_IMU",
            "capture_duration_ns": round(float(duration_s) * 1_000_000_000),
            "published_at": recorded_at,
            "pairing": {"accepted_pairs": pairs},
            "streams": [
                {"camera": "rgb", "segments": ["rgb.h265"]},
                {"camera": "infrared-left", "segments": ["infrared-left-y8.h265"]},
                {"camera": "infrared-right", "segments": ["infrared-right-y8.h265"]},
            ],
            "imu": {"path": "stm32.bin", "samples": imu_samples},
            "native_session_schema": manifest.get("schema"),
        }
        _atomic_json(metadata_dir / "recording.json", metadata)

        assets: list[dict[str, Any]] = []
        for path in sorted(prepared.rglob("*")):
            if not path.is_file() or path.name == "MANIFEST.sha256":
                continue
            relative = path.relative_to(prepared).as_posix()
            if path.is_symlink() or "\n" in relative or "\r" in relative:
                raise ValueError("unsafe recording asset")
            assets.append(
                {
                    "relative_path": relative,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        manifest_text = "".join(
            f"{item['sha256']}  {item['relative_path']}\n" for item in assets
        )
        manifest_path = prepared / "MANIFEST.sha256"
        manifest_path.write_text(manifest_text, encoding="utf-8")
        with manifest_path.open("rb") as stream:
            os.fsync(stream.fileno())
        manifest_sha256 = _sha256(manifest_path)
        if HEX64.fullmatch(manifest_sha256) is None:
            raise ValueError("manifest digest is invalid")
        _fsync_dir(metadata_dir)
        _fsync_dir(prepared)
        os.rename(prepared, final)
        _fsync_dir(completed)

        final_relpath = f"recordings-v2/completed/{recording_id}"
        ledger = {
            "schema_version": 1,
            "state": "PUBLISHED",
            "job_id": job_id,
            "request_id": request_id,
            "recording_id": recording_id,
            "prepared_relpath": f"recordings-v2/completed/.{recording_id}.publishing",
            "final_relpath": final_relpath,
            "manifest_sha256": manifest_sha256,
        }
        _atomic_json(ledger_dir / f"{recording_id}.json", ledger)

        from catalog_db import CatalogDb

        catalog = CatalogDb(catalog_db_path)
        catalog.migrate()
        catalog.publish_recording(
            recording_id=recording_id,
            device_id=device_id,
            recorded_at=recorded_at,
            duration_ms=round(float(duration_s) * 1000),
            total_bytes=sum(item["size_bytes"] for item in assets),
            state="COMPLETE_LOCAL",
            manifest_sha256=manifest_sha256,
            save_state="LOCAL_ONLY",
            display_name=recording_id,
            recorded_at_source="device_clock",
            time_source="system_utc",
            job_id=job_id,
            boot_id=boot_id,
            assets=tuple(assets),
            final_relpath=final_relpath,
            capture_mode="STEREO_IMU",
            imu_quality_status="PASSED",
        )
    except BaseException:
        if prepared.exists() and not session.exists():
            os.rename(prepared, session)
        raise
    return {
        "recording_id": recording_id,
        "output_dir": str(final),
        "manifest_sha256": manifest_sha256,
        "stereo_pairs": pairs,
        "imu_samples": imu_samples,
        "duration_s": float(duration_s),
        "ledger": ledger,
    }


def _recorded_at(recording_id: str) -> str:
    match = re.search(r"(20\d{6}T\d{6}Z)", recording_id)
    if match is None:
        raise ValueError("recording identifier has no UTC acquisition time")
    stamp = match.group(1)
    return (
        f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T"
        f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z"
    )
