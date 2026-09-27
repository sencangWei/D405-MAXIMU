# Minimal observation candidate contract (review before full replay)

Prototype, NOT a production accuracy claim. No MASt3R positions or external GT
enter estimation. Image-pair PnP is used only for visual initialization.

## Geometry

- Share N landmarks across F rectified stereo image pairs. Observations are
  left/right pixel coordinates with a validity mask.
- Gauge: first left-camera pose identity, origin zero. States: other camera-to-
  window rotations R_j and centers p_j (metres), shared landmark XYZ in the first
  camera gauge, and one constant camera-frame gyro bias.
- Project X_Cj=R_j^-1(X_W-p_j); right X is X_Cj-[baseline,0,0]. Reprojection
  residuals use recording-specific left/right intrinsics and stereo baseline.
- Landmark depth remains variable, constrained by all stereo views, not a fixed
  SGBM object-point depth. Initialization uses formal0.07–0.6m valid source depth
  and image-only PnP; never learned-unit translation or scale.

## Gyro

- Adjacent raw gyro preintegration with formal td once, conjugated body→leftIR.
  Delta convention R_i^-1 R_j, not its inverse.
- Measured rate=true rate + body_from_camera.apply(camera_bias). With right-
  tangent preintegration derivative J_b, Delta(b)≈Delta(0)Exp(J_b b), so the
  local factor is Log(Delta(0)^-1 R_i^-1 R_j)-J_b b. Real controls calculate J_b
  by central finite differences of raw trapezoidal integration; the default
  -dt I is only a small-motion approximation. Both models are first-order,
  never exact bias reintegration. Estimate one bias with explicit zero prior; do not hard-fix
  visual rotation to raw gyro.
- Initial diagnostic noise policy: formal gyr_n*sqrt(dt); bias prior standard
  deviation from existing accepted initialization guard0.01rad/s divided by3.
  This is a declared model assumption, not calibrated system uncertainty or a
  weight sweep. Reviewer must challenge correctness before real-data adoption.

## Output / safety

- Return camera centers/rotations, metric endpoint displacement in camera0,
  residual diagnostics, solver status, cheirality, track support. Module does not
  define a learned-unit scale/confidence or graph-factor weight.
- Reject malformed/nonfinite input, no stereo baseline/support, negative-depth
  solution, or failed solve. Report rejection, do not fill a trajectory or delete
  input frames.
- Fixed robust pixel loss policy, no parameter sweep. Hessian sensitivity must
  never masquerade as calibrated accuracy/covariance.
- Synthetic metric/rotation/bias/outlier/failure tests before code; uniformly
  preselected observation-level controls across all ten before full graph batch.
- Pure rotation remains observable with stereo metric depth. Reject disconnected
  support, collinear landmark geometry, invalid initial/solved cheirality; do not
  incorrectly reject a zero-translation window simply for low motion.
- Hold out every fifth track before pose initialization and BA, then check
  subsequent-view reprojections using those withheld source-stereo points.
  This is a geometric diagnostic, not an independent millimetre accuracy claim.
- PnP geometric inliers (existing fixed2px) admit each training observation,
  not a global track blacklist. Source stereo remains and other views retain
  a landmark when one view is rejected. Unsupported landmark variables are
  omitted; require per-node four-point non-collinear geometry (EPNP minimum),
  connected/temporal support and joint-model consistency. Initial20inlier
  per-node admission incorrectly transplanted a pairwise gate into a joint
  window; corrected after review and uniform all-ten validation, no sweep.
  Large source-depth errors can still reject valid temporal matches: this is a
  declared estimator limitation, not a justification to discard a recording.
- Postfit consistency gates: stereo reprojection P95≤2px or95% within2px;
  gyro P95≤5deg; each biascomponent≤0.01rad/s. These reuse existing diagnostic
  tolerances, not a promise of millimetre accuracy. Rejections retain diagnostic
  states; never pass rejected states as graph constraints.

## Phase5 frozen graph integration

- Candidate wrapper only; do not edit native production fusion or frozen reports.
- Reuse exact previous SIFT-LM/raw-gyro validated graph commands and subsequent
  stages. This isolates new information from re-estimation or policy changes.
- Append one endpoint displacement per accepted time-stratified v6 window.
  Do not duplicate correlated subedges or scale fitted-to-MASt3R positions.
- Endpoint vector is in window's first leftIR camera frame. Check all trajectory
  timestamps, session, calibration, input hashes and core acceptance diagnostics.
- Dispatch confidence only for the explicit new factor type. Use fixed1 with
  existing native stereo sigma4mm, a declared uncalibrated model assumption.
  Old confidence, local scale medians, priors, cap, IRLS and scoring stay frozen.
- New factors have no scalar scale, no PnP orientation factor. Native camera
  rotations place metric vectors into the graph world; this retains existing
  orientation uncertainty and is not a raw full visual-inertial global BA.
- First case smoke runs before remaining nine automatically. Record all failures,
  never select a candidate or alter parameters using external scores.
