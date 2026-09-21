#!/usr/bin/env bash
# C2: 幸存 matching 臂的**全链**对照。
#
# 逐字走现役 `fusion)` 子命令（前端 -> [2/8..6/8] 双红外尺度 -> [7/8] -> [8/9] -> [9/9] -> eval），
# 只用 MAST3R_SLAM_CONFIG 换前端配置（resolve_candidate_config 里该变量优先级最高）。
# 产物落 <cell>/frontend_matching_c2_20260922/<arm>/sparse/，**不碰** fusion_v2 对照。
# 对照 = 盘上现成 <cell>/fusion_v2/sparse。
#
# 用法: run_c2.sh <cell相对路径> <arm> [输出根名, 默认 frontend_matching_c2_20260922]
set -uo pipefail
CELL="$1"; ARM="$2"
OUTROOT="${3:-frontend_matching_c2_20260922}"
ROOT=/home/robot/ego_vio_humble
WF="$ROOT/reports/lighthouse_umi_workflow"
PY=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python

G="$WF/$CELL"
OUT="$G/$OUTROOT/$ARM/sparse"
SES=$(cd "$G" && $PY -c "import json;from pathlib import Path;print(Path(json.load(open('lighthouse_ground_truth_provenance.json'))['clock_mapping']['d405_frames']).parent)")
VINS_DIR=$($PY -c "
import sys; sys.path.insert(0,'$ROOT/reports/mast3r_g2_validation_20260919/rerun_tail_v2_20260920')
from pathlib import Path
from vins_dir import pick_vins_dir
r = pick_vins_dir(Path('$G')); print(r[0] if r else '')")
[[ -n "$VINS_DIR" ]] || { echo "❌ 该 cell 无 PASS VINS"; exit 6; }
VINS_TRAJ="$VINS_DIR"; VINS_REPORT="$(dirname "$VINS_DIR")/run_acceptance.json"
GT="$G/lighthouse_body_ground_truth.csv"

CFG="$ROOT/config/mast3r_slam_d405_offline_${ARM}.yaml"
[[ -f "$CFG" ]] || { echo "❌ 无此 config: $CFG"; exit 2; }
# ⚠ 日志不能写 "$OUT/../fusion.log"：bash 不预归一化路径，OS 逐段解析，
#   而 `sparse` 此刻还不存在 ⇒ `..` 直接 ENOENT，全链在 0s 挂掉。
mkdir -p "$OUT"
LOG="$(dirname "$OUT")/fusion.log"

echo "cell=$CELL arm=$ARM  session=$SES  vins=$(basename "$(dirname "$VINS_DIR")")"
started=$SECONDS
MAST3R_SLAM_CONFIG="$CFG" bash "$ROOT/scripts/mast3r_slam_precision_workflow.sh" \
    fusion "$SES" "$VINS_TRAJ" "$VINS_REPORT" "$OUT" \
    > "$LOG" 2>&1
rc=$?
echo "[fusion] rc=$rc 用时 $((SECONDS-started))s"
[[ $rc -eq 0 ]] || { echo "❌ 全链 rc=$rc"; tail -20 "$LOG"; exit 7; }

"$PY" "$ROOT/scripts/evaluate_slam_ground_truth.py" \
    --estimate "$OUT/trajectory_fused.csv" --ground-truth "$GT" \
    --output "$OUT/precision.json" --plot "$OUT/precision.png" --report-md "$OUT/precision.md" \
    > "$(dirname "$OUT")/eval.log" 2>&1 || true

$PY - "$OUT" "$G/fusion_v2/sparse" <<'PY'
import json, sys
from pathlib import Path
arm = Path(sys.argv[1]); ref = Path(sys.argv[2])
def row(p, label):
    f = p / "precision.json"
    if not f.exists(): return f"  {label:<8} 无 precision.json"
    d = json.loads(f.read_text())
    return (f"  {label:<8} max={d['ate_translation_max_m']*1000:7.3f} p95={d['ate_translation_p95_m']*1000:6.3f} "
            f"rmse={d['ate_translation_rmse_m']*1000:6.3f} w10={d['ate_translation_within_10mm_ratio']*100:5.1f} "
            f"rot={d['ate_rotation_rmse_deg']:5.3f}  {d['result']}  est={Path(d.get('estimate','?')).name}")
print(row(ref, "对照")) ; print(row(arm, "本臂"))
f1, f2 = ref/"precision.json", arm/"precision.json"
if f1.exists() and f2.exists():
    a, b = json.loads(f1.read_text()), json.loads(f2.read_text())
    for k in ("ate_translation_max_m","ate_translation_p95_m","ate_translation_rmse_m"):
        va, vb = a[k]*1000, b[k]*1000
        print(f"    Δ{k:<28} {vb-va:+8.3f} mm  ({'改善' if vb<va else '变差'})")
    va, vb = a["ate_rotation_rmse_deg"], b["ate_rotation_rmse_deg"]
    print(f"    Δ{'ate_rotation_rmse_deg':<28} {vb-va:+8.3f} °   ({'改善' if vb<va else '变差'})")
PY
echo "✅ $CELL/$ARM 完成"