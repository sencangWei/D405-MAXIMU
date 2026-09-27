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
  omitted; require20 observations per frame and connected/non-collinear support.
  Large source-depth errors can still reject valid temporal matches: this is a
  declared estimator limitation, not a justification to discard a recording.
- Postfit consistency gates: stereo reprojection P95≤2px or95% within2px;
  gyro P95≤5deg; each biascomponent≤0.01rad/s. These reuse existing diagnostic
  tolerances, not a promise of millimetre accuracy. Rejections retain diagnostic
  states; never pass rejected states as graph constraints.
