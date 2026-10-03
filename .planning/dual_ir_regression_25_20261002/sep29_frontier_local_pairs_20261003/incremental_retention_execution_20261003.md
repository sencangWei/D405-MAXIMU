# Incremental retention: user correction and fresh trajectory check

Partial improvement is not precision acceptance, but also is not a reason to
discard a direction. Keep each source-bound candidate and its outputs. Retain
the incremental development candidate when most fixed failing records improve
and all five historically passing controls still have max ATE <= 10 mm with
comparable complete coverage. Keep explicit regressions and technical failures
in the denominator. Do not promote on one recording or a local geometry proxy.

The existing fixed ten-record cohort and full25/new-capture completion standard
remain unchanged. Ground truth is used only after the estimate is frozen for
evaluation, never by the frontend, scale solver, fusion, or production selector.

The joint metric candidate remains retained (ROOT 117b080 + evidence 0f8fff2;
actual MASt3R toolchain 58c9f61), not rolled back. Its ind2 LEFT full frontend
finished successfully: 1199 actual tracked poses / 1199 input frames, 86 actual
joint metric solves; trajectory SHA256
606e0565a6f781b4303e011c570bb4e9fa7507aac5984d47c18d1948f1ebd78e.
This is not an ATE or precision PASS. The RIGHT run is still in progress at
this checkpoint. The take02 RIGHT prefix failure is retained as coverage
failure, not evidence that the metric direction is useless.

Review found that historical frontend commits differ from current 58c9f61.
Therefore frozen historical scores are historical references only. Fresh
default-off controls use the identical current code/data/calibration/model and
effective config, differing only in tracking.metric_relative_joint=false.
They export actual full-frame poses with --require-complete; interpolated
missing tails are not accepted as observations.

Fresh downstream evaluation must regenerate the four onboard LEFT stereo
reports from original raw DB calibration, derive RIGHT reports with explicit
LEFT-derived geometry labels, and regenerate both IMU metric trajectories.
Then replay the unchanged symmetric -> constant gauge -> physical stereo
constant gauge backend and unchanged frozen reference scorer. No old estimate,
score, or source SHA is relabeled as fresh. This path does not include the
separate native recovery / missing-pair appendix.

Execution update: RIGHT v1 later returned exit143; both native producer PIDs
are gone and no full native export exists. The caller/producer artifact remains
preserved; it is an interrupted technical run, not a precision result. A first
default-off LEFT control failed with a confirmed CUDA OOM while sharing the GPU
with the growing RIGHT graph. Serial control LEFT v2 then finished successfully
with 1199/1199 actual poses. Subsequent controls, fresh scale/fusion/scoring,
and a fresh RIGHT metric replay are queued serially in
run_ind2_fresh_serial_validation_v1.sh. No failed output is overwritten and no
algorithm objective, weight, cap, or precision threshold is changed.

Independent RK3576 deployment remains live-blocked by No route to host and the
missing actual ARM candidate/native runtime. No QR, network, udev, package,
recording deletion, reboot, or robot-motion changes were made.
