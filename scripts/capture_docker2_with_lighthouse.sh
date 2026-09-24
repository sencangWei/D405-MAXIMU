#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER2_RUNNER="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/umi-device2-d405.sh"
DOCKER2_DATA_ROOT="/home/robot/umi_ego_vio_data_device2_c48df736"
DOCKER2_CAPTURE_IMAGE="${DOCKER2_CAPTURE_IMAGE:-umi-ego-vio:device2-c48df736-d405-lighthouse-calibration-v1-20260908}"
FROZEN_LIGHTHOUSE_CONFIG="$ROOT_DIR/reports/lighthouse_recalibration_world_v12_20260924_consensus/libsurvive_config_frozen_v12.json"
FROZEN_LIGHTHOUSE_CONFIG_SHA256="63bd7d886bf6f8b46735a5f38047af8902d1f79b60a6feb57b3e3233e5bc6e99"
DURATION_S="${1:-60}"
LABEL="${2:-calibration}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${3:-$ROOT_DIR/reports/lighthouse_umi_sessions/${STAMP}_${LABEL}}"
TRACKER_DURATION_S="$(python3 -c 'import sys; print(float(sys.argv[1]) + 30.0)' "$DURATION_S")"
D405_READY_MARKER="[全流采集] 预热:"
D405_FORMAL_MARKER="[全流采集] 正式采集"
mkdir -p "$OUT_DIR"
if [[ ! -f "$FROZEN_LIGHTHOUSE_CONFIG" ]]; then
    echo "冻结 Lighthouse 配置不存在: $FROZEN_LIGHTHOUSE_CONFIG" >&2
    exit 2
fi
frozen_lighthouse_sha256="$(sha256sum "$FROZEN_LIGHTHOUSE_CONFIG" | awk '{print $1}')"
if [[ "$frozen_lighthouse_sha256" != "$FROZEN_LIGHTHOUSE_CONFIG_SHA256" ]]; then
    echo "冻结 Lighthouse 配置校验失败，拒绝采集" >&2
    exit 2
fi
LIGHTHOUSE_RUNTIME_CONFIG="$OUT_DIR/libsurvive_runtime_config.json"
install -m 0644 "$FROZEN_LIGHTHOUSE_CONFIG" "$LIGHTHOUSE_RUNTIME_CONFIG"

TRACKER_PID=""
D405_PID=""
GUIDE_PID=""
cleanup() {
    if [[ -n "$GUIDE_PID" ]] && kill -0 "$GUIDE_PID" 2>/dev/null; then
        kill -TERM "$GUIDE_PID" 2>/dev/null || true
        wait "$GUIDE_PID" 2>/dev/null || true
    fi
    if [[ -n "$TRACKER_PID" ]] && kill -0 "$TRACKER_PID" 2>/dev/null; then
        kill -TERM "$TRACKER_PID" 2>/dev/null || true
        wait "$TRACKER_PID" 2>/dev/null || true
    fi
    if [[ -n "$D405_PID" ]] && kill -0 "$D405_PID" 2>/dev/null; then
        kill -TERM "$D405_PID" 2>/dev/null || true
        wait "$D405_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

run_d405_capture() {
    set +e
    UMI_DEVICE2_D405_IMAGE="$DOCKER2_CAPTURE_IMAGE" \
    "$DOCKER2_RUNNER" capture "$DURATION_S" \
        2>&1 | tee "$OUT_DIR/d405_capture.log"
    runner_status="${PIPESTATUS[0]}"
    set -e
    return "$runner_status"
}

run_d405_capture &
D405_PID="$!"

D405_READY=0
for _ in $(seq 1 1200); do
    if grep -Fq "$D405_READY_MARKER" "$OUT_DIR/d405_capture.log" 2>/dev/null; then
        D405_READY=1
        break
    fi
    if ! kill -0 "$D405_PID" 2>/dev/null; then
        wait "$D405_PID" 2>/dev/null || true
        D405_PID=""
        echo "D405在设备就绪前退出" >&2
        tail -30 "$OUT_DIR/d405_capture.log" >&2 || true
        exit 2
    fi
    sleep 0.1
done
if [[ "$D405_READY" -ne 1 ]]; then
    echo "120 秒内D405未进入预热阶段，拒绝继续" >&2
    exit 2
fi

# Start Tracker only after D405 has finished its potentially slow container and
# storage setup.  The camera/IMU warmup still leaves enough time for Tracker to
# become ready before the formal capture window, while avoiding a fixed timer
# expiring when D405 startup is delayed by I/O pressure.
python3 "$ROOT_DIR/scripts/lighthouse_reference_check.py" record \
    --warmup 0 \
    --duration "$TRACKER_DURATION_S" \
    --output "$OUT_DIR/tracker.csv" \
    --report "$OUT_DIR/tracker_integrity.json" \
    --config "$LIGHTHOUSE_RUNTIME_CONFIG" \
    --lighthouse-gen 2 \
    >"$OUT_DIR/tracker_stdout.log" 2>&1 &
TRACKER_PID="$!"

TRACKER_READY=0
for _ in $(seq 1 150); do
    if ! kill -0 "$TRACKER_PID" 2>/dev/null; then
        echo "Lighthouse 采集进程在就绪前退出" >&2
        tail -30 "$OUT_DIR/tracker_stdout.log" >&2 || true
        exit 2
    fi
    if [[ -f "$OUT_DIR/tracker.csv" ]] && [[ $(wc -l < "$OUT_DIR/tracker.csv") -ge 2 ]]; then
        TRACKER_READY=1
        break
    fi
    sleep 0.1
done
if [[ "$TRACKER_READY" -ne 1 ]]; then
    echo "15 秒内未收到 Lighthouse 有效位姿，终止本轮联合采集" >&2
    exit 2
fi

if [[ "${APRILGRID_GUIDED_CAPTURE:-0}" == 1 ]]; then
    if [[ -z "${DISPLAY:-}" || ! -d /tmp/.X11-unix ]]; then
        echo "AprilGrid引导模式需要可用的DISPLAY和X11 socket" >&2
        exit 2
    fi
    FORMAL_READY=0
    for _ in $(seq 1 300); do
        if grep -Fq "$D405_FORMAL_MARKER" "$OUT_DIR/d405_capture.log" 2>/dev/null; then
            FORMAL_READY=1
            break
        fi
        if ! kill -0 "$D405_PID" 2>/dev/null; then
            echo "D405在正式采集提示前退出" >&2
            exit 2
        fi
        sleep 0.1
    done
    if [[ "$FORMAL_READY" -ne 1 ]]; then
        echo "30秒内D405未进入正式采集，拒绝启动动作引导" >&2
        exit 2
    fi
    python3 "$ROOT_DIR/scripts/aprilgrid_motion_prompt.py" \
        --duration "$DURATION_S" \
        >"$OUT_DIR/aprilgrid_motion_prompt.log" 2>&1 &
    GUIDE_PID="$!"
fi

set +e
wait "$D405_PID"
d405_status="$?"
set -e
D405_PID=""

if [[ -n "$GUIDE_PID" ]]; then
    wait "$GUIDE_PID" 2>/dev/null || true
    GUIDE_PID=""
fi

SESSION_IN_CONTAINER="$(sed -n 's/^\[全流采集\] 输出目录: //p' "$OUT_DIR/d405_capture.log" | tail -1)"
SESSION=""
if [[ -n "$SESSION_IN_CONTAINER" ]]; then
    SESSION_NAME="$(basename -- "$SESSION_IN_CONTAINER")"
    SESSION="$DOCKER2_DATA_ROOT/recordings/$SESSION_NAME"
fi
if [[ -n "$SESSION" && -d "$SESSION" ]]; then
    printf '%s\n' "$SESSION" > "$OUT_DIR/d405_session.txt"
fi
if [[ "$d405_status" -ne 0 ]]; then
    if [[ -n "$SESSION" && -s "$SESSION/d405_720p_rgb_stereo_ir.db3" ]]; then
        echo "D405采集质量验收失败，但DB3已搬运完成: $SESSION" >&2
    else
        echo "D405采集或DB3搬运失败；详见 $OUT_DIR/d405_capture.log" >&2
    fi
    exit 2
fi
if [[ -z "$SESSION" || ! -d "$SESSION" ]]; then
    echo "无法从采集日志确认 D405 会话目录" >&2
    exit 2
fi

if ! wait "$TRACKER_PID"; then
    TRACKER_PID=""
    echo "Lighthouse位姿流完整性验收失败；D405数据保留在: $SESSION" >&2
    tail -30 "$OUT_DIR/tracker_stdout.log" >&2 || true
    exit 2
fi
TRACKER_PID=""

python3 "$ROOT_DIR/scripts/lighthouse_reference_check.py" overlap \
    --tracker "$OUT_DIR/tracker.csv" \
    --d405-frames "$SESSION/d405_frames.csv" \
    --minimum-ratio 0.98 \
    --report "$OUT_DIR/tracker_d405_overlap.json"

python3 - "$OUT_DIR" "$SESSION" "$FROZEN_LIGHTHOUSE_CONFIG" "$LIGHTHOUSE_RUNTIME_CONFIG" "$DOCKER2_CAPTURE_IMAGE" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

out_dir = Path(sys.argv[1])
session = Path(sys.argv[2]).resolve()
frozen_lighthouse_config = Path(sys.argv[3]).resolve()
runtime_lighthouse_config = Path(sys.argv[4]).resolve()
capture_image = sys.argv[5]

# Two capture images now exist and both name their sessions `d405_720p_*`, so
# the path alone cannot tell 720p30 from 848x480@90.  Record the image and the
# geometry the camera actually negotiated, or a 90 fps session would be
# indistinguishable from a 720p one after the fact.
image_id = None
try:
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", capture_image],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip() or None
except Exception:
    image_id = None
tracker = out_dir / "tracker.csv"
acceptance_path = session / "acceptance.json"
d405_frames = session / "d405_frames.csv"
tracker_integrity = json.loads((out_dir / "tracker_integrity.json").read_text())
overlap_report = json.loads((out_dir / "tracker_d405_overlap.json").read_text())
if not acceptance_path.is_file() or not d405_frames.is_file():
    raise SystemExit("D405 capture evidence is incomplete")
capture_acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
if capture_acceptance.get("result") != "PASS":
    raise SystemExit("D405/IMU capture acceptance did not pass")
if tracker_integrity.get("status") != "PASS":
    raise SystemExit("Lighthouse pose geometry/integrity did not pass")
if overlap_report.get("status") != "PASS":
    raise SystemExit("D405/Lighthouse timestamp overlap did not pass")
manifest = {
    "schema": "docker2_lighthouse_capture_v1",
    "d405_session": str(session),
    "tracker_csv": str(tracker.resolve()),
    "tracker_sha256": hashlib.sha256(tracker.read_bytes()).hexdigest(),
    "d405_frames_sha256": hashlib.sha256(d405_frames.read_bytes()).hexdigest(),
    "d405_acceptance": str(acceptance_path.resolve()),
    "d405_acceptance_sha256": hashlib.sha256(acceptance_path.read_bytes()).hexdigest(),
    "d405_acceptance_result": capture_acceptance["result"],
    "tracker_integrity": tracker_integrity,
    "tracker_d405_overlap": overlap_report,
    "clock_alignment": {
        "alignment_domain": "host_monotonic_s",
        "tracker_timestamp_column": "host_monotonic_ns",
        "d405_clock_map": str((session / "d405_frames.csv").resolve()),
        "residual_offset_policy": "estimate once during rigid calibration, then freeze",
    },
    "lighthouse_configuration": {
        "frozen_master": str(frozen_lighthouse_config),
        "frozen_master_sha256": hashlib.sha256(
            frozen_lighthouse_config.read_bytes()
        ).hexdigest(),
        "runtime_copy": str(runtime_lighthouse_config),
        "runtime_copy_sha256_after_capture": hashlib.sha256(
            runtime_lighthouse_config.read_bytes()
        ).hexdigest(),
        "policy": (
            "per-process copy passed with -c and --disable-calibrate; "
            "Lighthouse generation forced to 2"
        ),
    },
    "capture_image": {
        "reference": capture_image,
        "image_id": image_id,
        "negotiated_geometry": capture_acceptance.get("negotiated_stream_profiles"),
        "declared_resolution": capture_acceptance.get("resolution"),
        "declared_fps": capture_acceptance.get("fps_requested"),
        "policy": (
            "session directories are named d405_720p_* regardless of geometry, "
            "so this block is the authoritative record of what was captured"
        ),
    },
    "slam_supervision": False,
    "note": "Lighthouse is recorded independently and is used only for rigid calibration/scoring.",
}
(out_dir / "capture_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(manifest, ensure_ascii=False, indent=2))
PY

echo "联合采集完成: $OUT_DIR"
