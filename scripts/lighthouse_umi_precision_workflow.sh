#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_ROOT="$ROOT_DIR/reports/lighthouse_umi_workflow"
# 默认是宿主 release 的 720p 集。采集几何一变必须覆盖成同几何的集：run_slam() 把
# 它交给 test_vins_auto_loop.py，而后者按 config 所在目录解析 left.yaml/right.yaml，
# 所以用 848 数据配 720p 集会让 VINS 静默用错内参（fx 差 1.5 倍）。
CONFIG="${UMI_PRECISION_VINS_CONFIG:-/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml}"

source /opt/ros/humble/setup.bash
source /home/robot/ros2_ws/install/setup.bash
set -u

usage() {
    echo "用法:"
    echo "  $0 preflight [采集时长秒]"
    echo "  $0 calibrate [标定采集时长秒]"
    echo "  $0 evaluate <lighthouse_d405_aprilgrid_calibration.json> [测试采集时长秒]"
    echo "  $0 compare <lighthouse_d405_aprilgrid_calibration.json> [测试采集时长秒]"
    echo ""
    echo "环境变量:"
    echo "  UMI_PRECISION_VINS_CONFIG  SLAM 臂用的 vins_config.yaml"
    echo "                            默认 720p 宿主 release 集；跑 848x480 数据时必须覆盖，"
    echo "                            例如 .../formal_runtime_calibration_848x480/vins_config.yaml"
    echo "  UMI_CAPTURE_PREVIEW=1      采集时开预览（也是唯一能按 q 优雅停止的途径）"
    echo "  DOCKER2_CAPTURE_IMAGE      采集镜像（默认 720p tag）"
}

new_run_dir() {
    local label="$1"
    local stamp
    stamp="$(date +%Y%m%d_%H%M%S)"
    printf '%s/%s_%s\n' "$REPORT_ROOT" "$stamp" "$label"
}

run_preflight() {
    local mode="$1"
    local output="$2"
    local duration="$3"
    python3 "$ROOT_DIR/scripts/lighthouse_umi_preflight.py" \
        --mode "$mode" \
        --capture-duration-s "$duration" \
        --output "$output"
}

run_capture() {
    local duration="$1"
    local label="$2"
    local output="$3"
    "$ROOT_DIR/scripts/capture_docker2_with_lighthouse.sh" \
        "$duration" "$label" "$output"
}

run_slam() {
    local capture_dir="$1"
    local output="$2"
    local session
    local deadline=$((SECONDS + 180))
    while ! python3 "$ROOT_DIR/scripts/slam_benchmark_environment.py" >/dev/null; do
        if (( SECONDS >= deadline )); then
            echo "等待 180 秒后 SLAM 基准环境仍未恢复，拒绝继续" >&2
            return 4
        fi
        echo "等待采集文件搬运后的 I/O 压力恢复..."
        sleep 5
    done
    session="$(<"$capture_dir/d405_session.txt")"
    python3 "$ROOT_DIR/scripts/test_vins_auto_loop.py" "$session" \
        --config "$CONFIG" \
        --imu-shift-ms 0 \
        --rate 0.5 \
        --expect-loop any \
        --out-dir "$output"
}

verify_calibration() {
    local calibration="$1"
    # 校验文件名约定: 把 .json 换成 .sha256 (lighthouse_d405_aprilgrid_calibration.sha256)。
    # 这是仓库内唯一生产者 calibrate_lighthouse_aprilgrid_session.sh:111
    # (calibration.with_suffix(".sha256")) 与本函数共同遵守的形式, 故此处不做兼容放宽 ——
    # 后缀式 (...json.sha256) 会被判为缺文件, 这是刻意 fail-closed, 用来暴露非本流程产出的
    # 标定工件, 而不是需要修的 bug。若某份标定确实要用, 应走正规流程重新导出。
    local checksum_file="${calibration%.json}.sha256"
    if [[ ! -f "$checksum_file" ]]; then
        echo "缺少冻结外参校验文件: $checksum_file" >&2
        return 2
    fi
    (
        cd "$(dirname -- "$checksum_file")"
        sha256sum -c "$(basename -- "$checksum_file")"
    )
    python3 - "$calibration" <<'PY'
import json
import sys
from pathlib import Path

calibration = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if calibration.get("schema") not in {
    "lighthouse_d405_aprilgrid_handeye_v1",
    "lighthouse_d405_aprilgrid_joint_handeye_v1",
}:
    raise SystemExit("拒绝旧标定：必须使用独立 AprilGrid-Lighthouse 外参")
if calibration.get("result") != "PASS_CANDIDATE":
    raise SystemExit("冻结外参未通过验收")
if calibration.get("slam_supervision") is not False or calibration.get("slam_inputs") != []:
    raise SystemExit("冻结外参的独立性来源不合法")
PY
}

apply_lighthouse_ground_truth() {
    local estimate="$1"
    local tracker="$2"
    local d405_frames="$3"
    local calibration="$4"
    local output="$5"
    local report="$6"
    python3 "$ROOT_DIR/scripts/apply_lighthouse_aprilgrid_calibration.py" \
        --query-times "$estimate" \
        --tracker "$tracker" \
        --d405-frames "$d405_frames" \
        --calibration "$calibration" \
        --body-camera-config "$CONFIG" \
        --target body \
        --output "$output" \
        --report "$report"
}

write_workflow_manifest() {
    local kind="$1"
    local run_dir="$2"
    local calibration="${3:-}"
    python3 - "$kind" "$run_dir" "$calibration" <<'PY'
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

kind, run_dir_arg, calibration_arg = sys.argv[1:]
run_dir = Path(run_dir_arg).resolve()
manifest = {
    "schema": "lighthouse_umi_precision_workflow_v1",
    "kind": kind,
    "created_at": datetime.now().astimezone().isoformat(),
    "run_dir": str(run_dir),
    "slam_supervision": False,
}
if calibration_arg:
    calibration = Path(calibration_arg).resolve()
    manifest["calibration"] = str(calibration)
    manifest["calibration_sha256"] = hashlib.sha256(calibration.read_bytes()).hexdigest()
precision = run_dir / "precision.json"
if precision.is_file():
    manifest["precision"] = json.loads(precision.read_text(encoding="utf-8"))
comparison = run_dir / "comparison" / "comparison.json"
if comparison.is_file():
    manifest["comparison"] = json.loads(comparison.read_text(encoding="utf-8"))
(run_dir / "workflow_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
PY
}

command="${1:-}"
case "$command" in
    preflight)
        duration="${2:-60}"
        mkdir -p "$REPORT_ROOT"
        output="$REPORT_ROOT/preumi_preflight_$(date +%Y%m%d_%H%M%S).json"
        run_preflight preumi "$output" "$duration"
        printf '%s\n' "$output" > "$REPORT_ROOT/LATEST_PREFLIGHT"
        echo "无 UMI 预检完成: $output"
        ;;
    calibrate)
        duration="${2:-50}"
        run_dir="$(new_run_dir calibration)"
        mkdir -p "$run_dir"
        echo "固定 AprilGrid；让刚性连接的 D405+Tracker 按预览提示完成多轴运动。"
        run_preflight full "$run_dir/preflight.json" "$duration"
        python3 "$ROOT_DIR/scripts/aprilgrid_ir_live_gate.py" \
            --timeout 180 \
            >"$run_dir/aprilgrid_visibility_gate.log" 2>&1
        UMI_CAPTURE_PREVIEW=1 APRILGRID_GUIDED_CAPTURE=1 \
            run_capture "$duration" calibration "$run_dir/capture"
        session="$(<"$run_dir/capture/d405_session.txt")"
        calibration_dir="$run_dir/independent_calibration"
        "$ROOT_DIR/scripts/calibrate_lighthouse_aprilgrid_session.sh" \
            "$session" \
            "$run_dir/capture/tracker.csv" \
            "$calibration_dir"
        calibration="$calibration_dir/lighthouse_d405_aprilgrid_calibration.json"
        write_workflow_manifest calibration "$run_dir" "$calibration"
        printf '%s\n' "$calibration" > "$REPORT_ROOT/LATEST_CALIBRATION"
        echo "独立固定标定通过: $calibration"
        ;;
    evaluate)
        calibration="${2:-}"
        if [[ -z "$calibration" || ! -f "$calibration" ]]; then
            usage
            exit 2
        fi
        calibration="$(realpath "$calibration")"
        verify_calibration "$calibration"
        duration="${3:-60}"
        run_dir="$(new_run_dir evaluation)"
        mkdir -p "$run_dir"
        echo "独立测试开始：不要拆动或重新拧紧 Tracker；动作不得复用标定序列。"
        run_preflight full "$run_dir/preflight.json" "$duration"
        run_capture "$duration" evaluation "$run_dir/capture"
        run_slam "$run_dir/capture" "$run_dir/slam"
        session="$(<"$run_dir/capture/d405_session.txt")"
        apply_lighthouse_ground_truth \
            "$run_dir/slam/vio_corrected_stream.csv" \
            "$run_dir/capture/tracker.csv" \
            "$session/d405_frames.csv" \
            "$calibration" \
            "$run_dir/lighthouse_body_ground_truth.csv" \
            "$run_dir/lighthouse_ground_truth_provenance.json"
        set +e
        python3 "$ROOT_DIR/scripts/evaluate_slam_ground_truth.py" \
            --estimate "$run_dir/slam/vio_corrected_stream.csv" \
            --ground-truth "$run_dir/lighthouse_body_ground_truth.csv" \
            --output "$run_dir/precision.json" \
            --plot "$run_dir/precision.png" \
            --report-md "$run_dir/precision.md" \
            --max-ate-rmse-mm 10 \
            --max-ate-p95-mm 10 \
            --min-within-10mm-ratio 0.95 \
            --max-rotation-rmse-deg 2 \
            --min-timestamp-overlap-ratio 0.98
        evaluation_status=$?
        set -e
        write_workflow_manifest evaluation "$run_dir" "$calibration"
        printf '%s\n' "$run_dir" > "$REPORT_ROOT/LATEST_EVALUATION"
        echo "精度报告: $run_dir/precision.md"
        echo "轨迹与误差图: $run_dir/precision.png"
        exit "$evaluation_status"
        ;;
    compare)
        calibration="${2:-}"
        if [[ -z "$calibration" || ! -f "$calibration" ]]; then
            usage
            exit 2
        fi
        calibration="$(realpath "$calibration")"
        verify_calibration "$calibration"
        duration="${3:-60}"
        run_dir="$(new_run_dir docker2_vs_mast3r)"
        mkdir -p "$run_dir"
        echo "同一原始采集将独立运行 Docker2 和 MASt3R-SLAM；Lighthouse 仅在两者结束后评分。"
        run_preflight full "$run_dir/preflight.json" "$duration"
        run_capture "$duration" comparison "$run_dir/capture"
        run_slam "$run_dir/capture" "$run_dir/docker2_slam"
        session="$(<"$run_dir/capture/d405_session.txt")"
        apply_lighthouse_ground_truth \
            "$run_dir/docker2_slam/vio_corrected_stream.csv" \
            "$run_dir/capture/tracker.csv" \
            "$session/d405_frames.csv" \
            "$calibration" \
            "$run_dir/lighthouse_body_ground_truth.csv" \
            "$run_dir/lighthouse_ground_truth_provenance.json"
        "$ROOT_DIR/scripts/mast3r_slam_precision_workflow.sh" compare \
            "$session" \
            "$run_dir/docker2_slam/vio_corrected_stream.csv" \
            "$run_dir/lighthouse_body_ground_truth.csv" \
            "$run_dir/comparison" \
            color
        write_workflow_manifest comparison "$run_dir" "$calibration"
        printf '%s\n' "$run_dir" > "$REPORT_ROOT/LATEST_COMPARISON"
        echo "统一对比报告: $run_dir/comparison/comparison.md"
        ;;
    *)
        usage
        exit 2
        ;;
esac
