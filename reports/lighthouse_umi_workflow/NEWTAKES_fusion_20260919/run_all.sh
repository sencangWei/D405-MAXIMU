#!/usr/bin/env bash
# 四条 720p take 的融合重跑驱动。
#
# 为什么要重跑: 20260918 那批前端是用带 --calib 循环门控 bug 的启动器跑的,
# 前端静默丢掉 D405 内参(use_calib=False), 症状是 frame1 位移 6.9-14.4mm
# (正常 0.06mm), 于是 [2/8] 双目尺度死在 dispersion 0.797/0.873(阈值 0.5)。
# 该 bug 已修, 所以四条都得整条重跑 —— 现有前端产物一律不可复用。
#
# 顺序执行(一次只跑一个 GPU 任务)。单条失败不中断后面的。
set -u

WF=/home/robot/ego_vio_humble/scripts/mast3r_slam_precision_workflow.sh
REC=/home/robot/umi_ego_vio_data_device2_c48df736/recordings
REP=/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow
OUT="$REP/NEWTAKES_fusion_20260919"

# 必须显式指定生产前端配置。脚本默认的 config/mast3r_slam_d405_offline.yaml 是
# "第三链"(带 22 个 stereo_* 键)那份, 与 §3~§14 全部基准前端不是同一个配置。
# 生产用的是 motion_kf_tight, sha256 1858a7da…(与 235337 run_manifest 记录的
# config_sha256 逐位一致)。
export MAST3R_SLAM_CONFIG=/home/robot/ego_vio_humble/config/mast3r_slam_d405_offline_motion_kf_tight.yaml

# take | D405 会话 | VINS 目录
TAKES=$(cat <<EOF
20260917_233028|d405_720p_rgb_stereo_ir_20260917_233030|20260917_233028_720p_loop1_720p_arm
20260917_235329|d405_720p_rgb_stereo_ir_20260917_235337|20260917_235329_720p_loop1_720p_arm_defaultcfg
20260918_002714|d405_720p_rgb_stereo_ir_20260918_002714|THIRDCHAIN_newtakes_20260918/20260918_002714
20260918_003403|d405_720p_rgb_stereo_ir_20260918_003403|THIRDCHAIN_newtakes_20260918/20260918_003403
EOF
)

: > "$OUT/_driver_status.txt"
while IFS='|' read -r take sess vinsdir; do
    [ -n "$take" ] || continue
    sess_path="$REC/$sess"
    vins_csv="$REP/$vinsdir/slam/vio_corrected_stream.csv"
    vins_json="$REP/$vinsdir/slam/run_acceptance.json"
    odir="$OUT/$take"

    echo "==================== $take ===================="
    for f in "$sess_path/d405_frames.csv" "$vins_csv" "$vins_json"; do
        [ -f "$f" ] || { echo "缺输入: $f" | tee -a "$OUT/_driver_status.txt"; continue 2; }
    done

    mkdir -p "$odir"
    "$WF" fusion "$sess_path" "$vins_csv" "$vins_json" "$odir" \
        > "$odir/fusion_workflow.log" 2>&1
    rc=$?
    echo "$take rc=$rc" | tee -a "$OUT/_driver_status.txt"
    tail -3 "$odir/fusion_workflow.log" | sed 's/^/    /'
done <<< "$TAKES"

echo "全部结束。状态:"; cat "$OUT/_driver_status.txt"
