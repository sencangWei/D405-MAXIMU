# Official SteamVR reference: frozen, not silently applied to legacy scoring

Tracker `LHR-A2A59C7D`, Standing Tracker origin to the existing VINS body.
No Lighthouse or robot trajectory supervised SLAM.

Calibration take3 passed; take4 held out both transform and time offset.
Heldout translation residual median1.403mm, P953.663mm, max6.398mm.
These are relative-motion consistency residuals, not SLAM ATE.

## Timing

`t_tracker_query = t_camera + td + imu_tracker_query_offset`.
Formal camera/IMU td stays -9.109323ms; independently estimated IMU/Tracker
offset stays -3.677539ms. Effective CAMERA-domain lookup is -12.786862ms.
Do not put the effective camera-domain value back into an IMU-domain query.
Published VINS pose stamps remain camera-domain; this does not authorize
applying the compensation to unrelated body-stream hardware timestamps.

New CLI `--imu-tracker-query-offset-ms` composes fixed td once; legacy
`--tracker-query-offset-ms` remains direct camera-domain for compatibility.
The session wrapper now uses the new explicit argument. The formal runtime
configuration and replay IMU shift were not changed.

The session wrapper now requires an accepted official capture manifest with
matching backend, frame, serial, session and Tracker CSV hash before freezing.
Older captures without this provenance are rejected, not silently declared
official. Its own manifest explicitly says it does not run heldout validation;
the separate reference_manifest.json pins take4's heldout check.

Verification:49 focused tests PASS (including execution of the actual freeze
block on correct and mismatched provenance), shell syntax PASS, independent
review no blockers. Full offline session wrapper repeated take3 end-to-end and
returned0, reproducing the exact Tracker-to-body transform and timing value.

## Scope

Manifest paths are relative to the repository root. Hashes pin calibration,
raw capture identity and the independent validation evidence. Reproducing
take3 with the new CLI yielded an identical transform and a timing difference
of only1.78e-15ms (floating point). No legacy libsurvive configuration or SLAM
evaluation consumer has been replaced automatically. A scoring consumer must
check the declared backend/frame and use the camera-vs-IMU time contract.
