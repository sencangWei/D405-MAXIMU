# Official-backend AprilGrid take 1 — 2026-09-27

Camera preview closed after normal duration, not a crash: actual40.001993688s,
RGB1200, eachIR1199, IMU16000, zero drops, camera acceptancePASS.
OfficialTracker4793/4793 normal rows, maxgap11.637476ms, exposurecoverage100%.
Board extraction1199/1199, reprojectionmedian0.187px/P950.329px.

Motion began only after approximately22s. Five-second bins for Tracker
position range/rotation excursion:0–5s0.97mm/0.06deg;
5–10s0.95mm/0.04deg;10–15s1.43mm/0.03deg;15–20s1.10mm/0.03deg;
20–25s145.2mm/7.95deg;25–30s244.42mm/16.34deg;
30–35s147.89mm/4.71deg;35–40s149.07mm/24.28deg.

Timing-estimator bug isolated:10*MAD clipping used a quiet-state noise floor
as the dynamic saturation limit.46.54%IMU/22.22%Tracker samples saturated.
Same active physical signals correlate0.9912, clipped signals0.4147.
Minimal fix retains affineMADnormalization, removes nonlinear clipping.
Separate explicit Tracker spike interpolation/ratio gate is unchanged.
Known6.5ms synthetic static-hold test RED beforefix, GREEN afterfix;
25targetedtestsPASS including existing isolated-quaternion-spike test.

Old rejected td report preserved. Fresh independent td report:
official_board_candidate/lighthouse_imu_time_offset_unclipped.json,
queryoffset−3.915091ms, correlation0.996611,PASS_CANDIDATE.
This is not the Docker2 camera/IMU td and does not replace its formal configuration.

External calibration remains DIAGNOSTIC_CANDIDATE, not installed/frozen:
combinedmotion residualP952.4306mm/max5.1702mm, but global relative-rotation
P956.4877deg below10deg gate; split halves differ12.4364mm/2.4517deg.
Needs another prepared moving take with adequate multi-axis excitation.
Independent dynamic verification and camera/IMU timestamp contract still required
before producing official-backend scoringGT. No SLAM supervision used.

Review of the bounded normalization fix found no blocker (existing outlier test
still passes). Separately it identified an existing HIGH timing-domain risk:
the session script passes raw IMU/Tracker queryoffset into interpolation at
camera exposure timestamps, without explicitly composing formal camera/IMUtd.
Do not freeze or deploy the diagnostic transform above. Before the next solve,
justify/convert this timing contract with a sign-tested camera/IMU mapping;
do not change the formal Docker2−0.009109323s configuration or add double shifts.
