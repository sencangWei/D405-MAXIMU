#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$ROOT_DIR/scripts/mast3r_slam_precision_workflow.sh"
TOOL_DIR="${MAST3R_SLAM_DIR:-/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM}"
PYTHON="$TOOL_DIR/.venv/bin/python"

if [[ $# -ne 4 ]]; then
    echo "用法: $0 <D405会话目录> <VINS轨迹.csv> <VINS验收报告.json> <输出目录>" >&2
    exit 2
fi

session="$(realpath "$1")"
vins_trajectory="$(realpath "$2")"
vins_report="$(realpath "$3")"
output="$(realpath -m "$4")"
sparse="$output/sparse"
tight="$output/tight"
mkdir -p "$output"

echo "[1/4] 稀疏视觉候选"
set +e
MAST3R_SLAM_CONFIG="$ROOT_DIR/config/mast3r_slam_d405_offline.yaml" \
    "$WORKFLOW" fusion "$session" "$vins_trajectory" "$vins_report" "$sparse"
sparse_candidate_status=$?
set -e
if (( sparse_candidate_status != 0 )); then
    echo "sparse候选未通过内部质量门，继续尝试独立的tight候选" >&2
fi

echo "[2/4] 密运动关键帧候选"
tight_candidate_reason="tight_candidate_failed_internal_quality"
if [[ -f "$sparse/mast3r/graph_fusion_report.json" ]] && \
    "$PYTHON" - "$sparse/mast3r/graph_fusion_report.json" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
scale_difference = float(report["metric_scale_consistency"]["relative_difference"])
observable = scale_difference <= 0.15
raise SystemExit(0 if observable else 4)
PY
then
    set +e
    MAST3R_SLAM_CONFIG="$ROOT_DIR/config/mast3r_slam_d405_offline_motion_kf_tight.yaml" \
        "$WORKFLOW" fusion "$session" "$vins_trajectory" "$vins_report" "$tight"
    tight_candidate_status=$?
    set -e
else
    tight_candidate_status=4
    tight_candidate_reason="tight_candidate_skipped_unobservable_metric_scale"
    echo "双目/IMU米制尺度不可观，跳过不会被选择的tight候选" >&2
fi

echo "[3/4] UMI内部几何一致性候选选择"
if (( sparse_candidate_status == 0 && tight_candidate_status == 0 )); then
    "$PYTHON" "$ROOT_DIR/scripts/select_mast3r_fusion_candidate.py" \
        --sparse-graph-report "$sparse/mast3r/graph_fusion_report.json" \
        --tight-graph-report "$tight/mast3r/graph_fusion_report.json" \
        --sparse-fusion-report "$sparse/fusion_report.json" \
        --tight-fusion-report "$tight/fusion_report.json" \
        --sparse-trajectory "$sparse/trajectory_fused_unsmoothed.csv" \
        --tight-trajectory "$tight/trajectory_fused_unsmoothed.csv" \
        --min-tight-stereo-improvement 0 \
        --output "$output/trajectory_selected_unsmoothed.csv" \
        --report "$output/selection_report.json"
elif (( sparse_candidate_status == 0 )); then
    echo "tight候选未通过内部质量门控，自动回退到已通过的sparse候选" >&2
    cp "$sparse/trajectory_fused_unsmoothed.csv" \
        "$output/trajectory_selected_unsmoothed.csv"
    "$PYTHON" - \
        "$sparse/mast3r/graph_fusion_report.json" \
        "$sparse/fusion_report.json" \
        "$sparse/trajectory_fused_unsmoothed.csv" \
        "$output/trajectory_selected_unsmoothed.csv" \
        "$output/selection_report.json" \
        "$tight_candidate_status" \
        "$tight_candidate_reason" <<'PY'
import json
import sys
from pathlib import Path

(
    sparse_graph,
    sparse_fusion,
    sparse_trajectory,
    output,
    report_path,
    tight_status,
    fallback_reason,
) = sys.argv[1:]
report = {
    "schema": "umi_mast3r_fusion_candidate_selection_v1",
    "result": "PASS",
    "slam_supervision": False,
    "selection_policy": "use the internally valid sparse candidate when tight fails quality gates",
    "selected_candidate": "sparse",
    "evidence": {
        "fallback_reason": fallback_reason,
        "tight_candidate_exit_status": int(tight_status),
    },
    "inputs": {
        "sparse_graph_report": str(Path(sparse_graph).resolve()),
        "sparse_fusion_report": str(Path(sparse_fusion).resolve()),
        "sparse_trajectory": str(Path(sparse_trajectory).resolve()),
    },
    "output": str(Path(output).resolve()),
}
Path(report_path).write_text(
    json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
)
PY
elif (( tight_candidate_status == 0 )); then
    echo "sparse候选被内部质量门拒绝，使用已通过的tight候选" >&2
    cp "$tight/trajectory_fused_unsmoothed.csv" \
        "$output/trajectory_selected_unsmoothed.csv"
    "$PYTHON" - \
        "$tight/mast3r/graph_fusion_report.json" \
        "$tight/fusion_report.json" \
        "$tight/trajectory_fused_unsmoothed.csv" \
        "$output/trajectory_selected_unsmoothed.csv" \
        "$output/selection_report.json" \
        "$sparse_candidate_status" <<'PY'
import json
import sys
from pathlib import Path

(
    tight_graph,
    tight_fusion,
    tight_trajectory,
    output,
    report_path,
    sparse_status,
) = sys.argv[1:]
report = {
    "schema": "umi_mast3r_fusion_candidate_selection_v1",
    "result": "PASS",
    "slam_supervision": False,
    "external_ground_truth_used": False,
    "selection_policy": "use the internally valid tight candidate when sparse fails quality gates",
    "selected_candidate": "tight",
    "evidence": {
        "fallback_reason": "sparse_candidate_failed_internal_quality",
        "sparse_candidate_exit_status": int(sparse_status),
    },
    "inputs": {
        "tight_graph_report": str(Path(tight_graph).resolve()),
        "tight_fusion_report": str(Path(tight_fusion).resolve()),
        "tight_trajectory": str(Path(tight_trajectory).resolve()),
    },
    "output": str(Path(output).resolve()),
}
Path(report_path).write_text(
    json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
)
PY
else
    echo "both_candidates_failed_internal_quality" >&2
    exit 3
fi

selected_candidate="$({ "$PYTHON" - "$output/selection_report.json" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["selected_candidate"])
PY
} )"
selected_root="$sparse"
if [[ "$selected_candidate" == "tight" ]]; then
    selected_root="$tight"
fi

echo "[4/4] 已选候选质量门控与最终零相位平滑"
"$PYTHON" "$ROOT_DIR/scripts/assess_mast3r_fusion_input_quality.py" \
    --graph-report "$selected_root/mast3r/graph_fusion_report.json" \
    --fusion-report "$selected_root/fusion_report.json" \
    --output "$output/input_quality_report.json"
"$PYTHON" "$ROOT_DIR/scripts/smooth_pose_trajectory.py" \
    --input "$output/trajectory_selected_unsmoothed.csv" \
    --output "$output/trajectory_fused.csv" \
    --method gaussian \
    --gaussian-sigma-s 0.025 \
    --report "$output/smoothing_report.json"

echo "自适应融合完成: $output/trajectory_fused.csv"
echo "选择依据仅来自 UMI 双目、IMU 与两套机载轨迹的一致性。"
