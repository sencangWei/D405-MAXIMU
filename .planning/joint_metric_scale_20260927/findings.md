# Evidence

- Newheldout2 IMUscale .509988934, stereo .486308615 (IMU+4.869%).
- Pregraph selectedscale .498008044 is equal-weightgeometricmean; graph has no
  scalar metric state, only qpositions/velocities/gravity/accelbias.
- Afterstaticfix officialSE3 max11.707mm, RMSE7.420mm. Post-hocdiagnosticSim3
  .96862158 givesRMSE3.765/max9.556mm. This motivates independent metric state;
  it doesnotauthorize applying thatGT-derivedscale to realSLAM.
- Otherfive fresh/devcases allPASS; IMUvsstereo difference1.48–7.43% and mixed
  signs. An IMU/stereodisagreement thresholdalone cannot identifyfailedtake2.
- NewdirectionC must use existing metric observation equations, not choose a
  fixedstereo mode or tune .25 stage8 weight alreadyclosedbyhistoricalevidence.
