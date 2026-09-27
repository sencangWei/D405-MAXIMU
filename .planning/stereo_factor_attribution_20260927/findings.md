# Findings

Frozen fresh four: max [7.900,16.151,6.217,16.181] mm. Prior six all passed.
At take2 final peak raw metric geometry is already bad; graph reduces it. At take4 final peak raw metric geometry is about 6.55 mm under the graph's alignment, final graph about 17.30 mm. Full-rate substage has not yet been isolated.
Accepted long-hop stereo observations disagree with independent internal VINS displacement near both peaks; after-fit stereo residual alone is not an independent reliability test. This is evidence of conflict, not proof that VINS is correct.
Joint solver estimates accelerometer bias, but full-rate solver currently receives gravity without that bias. A real semantic inconsistency, with causal magnitude not yet demonstrated.
Preserve current dirty worktree; cached source/config/inputs frozen for attribution.

## Exact stage attribution
Observational replay fresh2/fresh4 graph CSVs are byte-identical to frozen baseline. Fixed-last-IRLS system RHS decomposition reconstructs the solution within 2.52e-6 and 1.33e-6 (mixed units; position contributions need separate verification). This is not factor-removal ablation.
At source frame581, take2 joint correction [-15.547,-6.596,+0.114] mm; full-rate [+0.411,+0.143,-0.065] mm. Stereo RHS contribution [-11.256,-2.967,-0.334] mm, VINS [-2.936,-2.435,+0.102] mm.
At source frame1071, take4 joint correction [-8.051,-0.208,+6.176] mm; full-rate [-0.101,-0.019,+0.667] mm. Stereo RHS contribution [-7.937,-0.317,+6.205] mm, VINS [-0.071,+0.095,-0.065] mm. Thus bad take4 correction is chiefly the stereo-driven joint solve, not full-rate/bias omission.
All four merged accepted stereo sets have unique endpoint pairs (no exact duplicates). Shared frame incidence is correlated, but treating correlation as the proven root cause is premature.
Next diagnostic: all-ten cached leave-one-edge-out shorter-path cycle checks. Unsupported edges explicitly unobservable; shared images mean alternate path is not statistically independent. No production code changed.

## SIFT contract candidate
Found SIFT accepted fallbacks return before LK's reverse acceptance. Confidence defaults missing reverse disagreement tozero. Allfour have this gap, acceptedcounts [330,261,244,321]. Implemented optional inverse-vector validator only; current production functions/defaults untouched. Candidate harness adds oldLK scalarreverse validation and newvectorclosure to SIFT only. Keeps forward displacement/scales, existing LK, global scales, cachedVINS frozen. Parent+backup+fetchedrestore41testsPASS. Eight relevant component files byte-identical after restoring ownedremote commit e610973da91d1569b1affd8ea0a50c7952ce465e; experimental, not ATEsuccess.
First two completed scores: fresh1 max7.899790→8.306693mm; dev1 6.927232→7.168875mm; dev2 8.769177→8.693835mm. AllstillPASS, but no global improvement assumed.

## Avoid false attribution / additional diagnostic
Bounded fixed-rotation PnP replay mixed results. Replacing freePnP rotation with the graph's refinedattitude is NOT equivalent to independentIMU rotation. Direct gyro integration (tdappliedonce) differs: take2 edge570→590 PnPgyroerror0.479deg vsrefinederror1.384deg; take4 edge834→837 0.022deg vs0.603deg. SomeSIFTlongedges show opposite: take4 edge1030→1070 gyroerror1.076deg vsrefined0.240deg. This suggests refinedattitude can be pulled toward visual/PnP factors; not a validated repair and not proof of IMU calibration failure. Do not deploy an IMU-fixed translation mode based on refinedattitude or onebadedge.

## Runtime hygiene
I/Opressure caused by four obsolete broad rg searches left running after diagnostics. Verified exactPID+cmd and terminated only oursearches175558,192249,194853,200539. PSIiosome avg10 dropped94%→15%; no hardware/capture/userprocess changed. Future searches narrowly scoped to relevant source/mdfiles, never whole reports/home.
# Final common-regression result and independent-gyro diagnostic

The fixed SIFT reverse-validation experiment completed all ten cases:8PASS/2FAIL, exactly the baseline pass count. Fresh2 maximum16.151423→14.382203mm but mean4.163619→4.647543mm; fresh4 maximum16.181037→16.643332mm, mean6.338019→6.573127mm. No promotion. All scored sample counts and SE3/no-scale alignment preserved, including fresh4's original1142 matched samples. All source reports stayed hash-identical; no GT entered estimation.

Independent raw-gyro PnP sidecars completed allten,166uniformly time-stratified accepted-source edges. Three controls: free PnP, raw-MASt3R-fixed PnP, raw-calibrated-gyro-fixed PnP. First PnP correspondence+inlier hashes match across controls. Accepted gyro-fixed rotations match ext^-1*dRgyro^-1*ext to1e-8deg. Reverse closure median decreases in allten same-method matched groups, but reprojection increases in allten and paired VINS disagreement improves in onlytwo group medians. VINS is not truth; hard common constraints can create internally consistent bias. These sidecars do NOT establish an accurate new trajectory or justify gyro-fixed rollout.

Physical coupling evidence, not proof of sole cause: fresh4 long edge1035→1075 free PnP rotation differs from raw gyro by0.720deg; VINS camera-displacement disagreement10.827→6.271mm with gyro-fixed rotation. Another long edge920→1080 gives12.078→7.453mm. Fresh2 edge590→610 free replay rejects, raw-MASt3R-fixed has5.746deg gyro disagreement and32.338mm VINS disagreement, raw-gyro-fixed2.616mm VINS disagreement. This rules out treating refined/visual attitude as independent IMU truth; it does not imply VINS or gyro is exact. Several reverse SIFT solves still fail even when rotation is fixed.

Production remains frozen. Further change needs an observation model supported across cases, not a parameter sweep or closure-only selector. Checking whether factory IR distortion/rectification is handled correctly before attributing remaining metric bias to noisy depth or model training.

Geometry-contract audit complete: primary reports from allten have zero left/right Brown-Conrady coefficients. Allten dataset manifests explicitly record image_preprocessing.crop_bottom_px=0 and mask_fixed_self_occlusion=false, lossless_png_mono8. For fresh4 frame1071 (source frame1101), independent direct DB3-vs-prepared comparisons give zero differing pixels in BOTH IR images,1280x720, pair skew0ms. This sample rules out replay crop/mask contamination at the tested peak point; it is NOT an exhaustive all-frame pixel equality proof. No current evidence of ignored nonzero distortion. Future nonzero coefficients would need an explicit guard/rectification path, but changing that now cannot repair these zero-distortion cases.

Verified owned-remote updated backup08211029ae468153351080728ea0fe8264c4a836 by fresh fetch into clean restore, nine component files byte-identical,41targeted testsPASS and diagnostic py_compilePASS. No force push, no reports-wide staging. The fetch's local shallow remote-tracking update printed “forced update”; actual PUSH was normal fast-forward e610973→08211029, not a force push.
