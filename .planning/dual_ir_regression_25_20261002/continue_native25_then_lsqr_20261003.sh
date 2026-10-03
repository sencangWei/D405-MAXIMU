#!/usr/bin/env bash
# SUPERSEDED by the user's failure-first fast10 instruction. Do not launch.
# Preserved as history; its session19310 was terminated143 before any solver.
# One-shot continuation bound to the live 2026-10-03 B-chain PID.
# Not a generic restart script; rerunning refuses existing outputs.
set -eo pipefail
source /opt/ros/humble/setup.bash
source /home/robot/ros2_ws/install/setup.bash
parent_start="$(ps -p 2450656 -o lstart=)" || exit 1
test -n "$parent_start"
for destination in  .planning/dual_ir_regression_25_20261002/independent_ir_corpus_full25_merged_v1.json  .planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_full25_merged_v1.json  .planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_ind2_v2  .planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_multiday3_v2  .planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_remaining21_v2  .planning/dual_ir_regression_25_20261002/lsqr_telemetry_ind2_v2.json  .planning/dual_ir_regression_25_20261002/lsqr_telemetry_multiday3_v2.json  .planning/dual_ir_regression_25_20261002/lsqr_telemetry_remaining21_v2.json
do
 test ! -e "$destination"
 test ! -L "$destination"
done
echo 'Continuation waiting for verified source+paired chain PID 2450656 to finish; no solver launched yet.'
while current_start="$(ps -p 2450656 -o lstart=)" && [ "$current_start" = "$parent_start" ]
do
 sleep 30
done
python3 -u scripts/merge_independent_ir_corpus_results.py  --manifest config/dual_ir_regression_25_20261002.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_paired_ind2_v1/summary.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_paired_multiday3_v1/summary.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_paired_remaining21_v1/summary.json  --output .planning/dual_ir_regression_25_20261002/independent_ir_corpus_full25_merged_v1.json
common=(--manifest config/dual_ir_regression_25_20261002.json
 --baseline .planning/dual_ir_regression_25_20261002/batch_adapters_v2
 --constant-gauge .planning/dual_ir_regression_25_20261002/constant_gauge_batch_v3
 --combined-reference .planning/dual_ir_regression_25_20261002/gauge_physical_combined_batch_v1)
for shard in ind2 multiday3 remaining21
do
 source_stage=".planning/dual_ir_regression_25_20261002/independent_ir_corpus_native_${shard}_v1"
 ids_text="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["status"] in {"NATIVE_SOURCES_COMPLETE","NATIVE_SOURCES_WITH_FAILURES"}; ids=[r["id"] for r in d["records"]]; assert ids and len(ids)==len(set(ids)); print("\n".join(ids))' "$source_stage/preflight_report.json")" || exit 1
 test -n "$ids_text"
 mapfile -t ids <<< "$ids_text"
 dataset_args=()
 for record_id in "${ids[@]}"
 do
  [[ "$record_id" =~ ^[0-9]{8}_[a-z0-9]+$ ]] || exit 1
  dataset_args+=(--dataset "$record_id")
 done
 diagnostic_rc=0
 python3 -u scripts/audit_independent_ir_solver_convergence.py   --telemetry-output ".planning/dual_ir_regression_25_20261002/lsqr_telemetry_${shard}_v2.json"   "${common[@]}" --source-stage "$source_stage"   --output ".planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_${shard}_v2"   "${dataset_args[@]}" || diagnostic_rc=$?
 if [ "$diagnostic_rc" -ne 0 ] && [ "$diagnostic_rc" -ne 3 ]; then exit "$diagnostic_rc"; fi
done
python3 -u scripts/merge_independent_ir_corpus_results.py  --manifest config/dual_ir_regression_25_20261002.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_ind2_v2/summary.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_multiday3_v2/summary.json  --summary .planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_remaining21_v2/summary.json  --output .planning/dual_ir_regression_25_20261002/independent_ir_corpus_lsqr_full25_merged_v1.json
python3 -c 'import json; from pathlib import Path; p=Path(".planning/dual_ir_regression_25_20261002"); a=json.loads((p/"independent_ir_corpus_full25_merged_v1.json").read_text()); b=json.loads((p/"independent_ir_corpus_lsqr_full25_merged_v1.json").read_text()); assert a["record_count"]==b["record_count"]==25; aa={r["id"]:r for r in a["results"]}; bb={r["id"]:r for r in b["results"]}; assert aa.keys()==bb.keys(); checked=0
for rid,x in aa.items():
 y=bb[rid]; assert x["status"]==y["status"],rid
 for v,z in x.get("variants",{}).items():
  assert z["estimate_sha256"]==y["variants"][v]["estimate_sha256"],(rid,v,"estimate changed")
  for k in ("result","samples","ate_translation_mean_m","ate_translation_p95_m","ate_translation_max_m","ate_translation_rmse_m"):
   assert z["score"].get(k)==y["variants"][v]["score"].get(k),(rid,v,k)
  checked+=1
print(json.dumps({"status":"TELEMETRY_REPLAY_IDENTICAL","record_count":25,"checked_estimates":checked}))'
