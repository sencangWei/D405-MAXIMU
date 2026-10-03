#!/usr/bin/env bash
# One-shot job bound to existing B-chain. Future optimization loops use fast10,
# reusing native caches; only final acceptance runs the original full25 corpus.
set -eo pipefail
source /opt/ros/humble/setup.bash
source /home/robot/ros2_ws/install/setup.bash
parent_start="$(ps -p 2450656 -o lstart=)" || exit 1
test -n "$parent_start"
test ! -e .planning/dual_ir_regression_25_20261002/independent_ir_corpus_full25_merged_v1.json
test ! -L .planning/dual_ir_regression_25_20261002/independent_ir_corpus_full25_merged_v1.json
test ! -e .planning/dual_ir_regression_25_20261002/independent_ir_fast10_lsqr_v1
test ! -L .planning/dual_ir_regression_25_20261002/independent_ir_fast10_lsqr_v1
echo 'Fast10 continuation waiting for existing source+paired B-chain PID 2450656; no fresh image extraction or solver launched yet.'
while current_start="$(ps -p 2450656 -o lstart=)" && [ "$current_start" = "$parent_start" ]
do
 sleep 30
done
python3 -u scripts/merge_independent_ir_corpus_results.py  --manifest config/dual_ir_regression_25_20261002.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_paired_ind2_v1/summary.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_paired_multiday3_v1/summary.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_paired_remaining21_v1/summary.json  --output .planning/dual_ir_regression_25_20261002/independent_ir_corpus_full25_merged_v1.json
python3 -u scripts/run_independent_ir_fast_regression.py  --config config/dual_ir_fast_regression_10_20261003.json  --output-root .planning/dual_ir_regression_25_20261002/independent_ir_fast10_lsqr_v1
