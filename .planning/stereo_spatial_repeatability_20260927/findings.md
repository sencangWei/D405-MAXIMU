# Findings

Previous SIFT reverse candidate allten8PASS/2FAIL; rejected, production unchanged.
Raw-gyro fixed-PnP diagnostic allten/166edges reduced reverse closure but worsened
reprojection medians in allten and paired VINS disagreement in8/10. Hard fixing
rotation is not a supported universal repair. Factory IR distortion coefficients
allzero, prepared crop0/maskfalse; one take4 dual-IR PNG-vs-DB3 sample is exact.

Current graph uses stereo_sigma_m=0.004 isotropically for all three displacement
axes, times scalar confidence and IRLS weight. It does not consume a directional
PnP/triangulation uncertainty. This is an assumption, not yet a proven bug.
SIFT/LK combined result preserves forward displacement/PnP rotation (only scalar
scale is averaged), so its actual forward RANSAC point set can be matched by
the -R^-1*t displacement. Plan freezes all166edge sampling rules from prior probe.

Spatial delete-one-tile refit measures feature-support sensitivity, including
coherent depth/image errors; it is not an absolute error bar. LM-all-inliers
control separates ordinary refinement shift from spatial omission sensitivity.

Completed spatial10cases/166edges: failurefresh2max1.270mm versus passingfresh1
max6.166mm; failurefresh4max4.334mm. Reject a universal spatial-sensitivity
weight repair. Remove newsidecar and all prior free/fixed replay fields equal
raw_gyro_pnp_v1 exactly; original report SHA256 unchanged.

Completed held-out target depth10cases/166edges: forwardPnP source3D is fixed,
targetleftUV is fitted, targetstereo disparity is unused. Recompute targetSGBM
on exactforwardPnPinliers; LRtolerance1px, originalrange0.07..1.5m, R*XYZ+t.
Check availability/LR/range separately. Edgewise depth-absolute-median aggregate:
fresh2 3.174mm / disparity0.267px; fresh4 4.981mm / disparity0.259px;
passingfresh1 5.293mm / disparity0.331px. Median predicted/measured depth ratios
near1.0. Not a common failing-case discriminator. Shared stereo observation
model is not independent truth; these numbers are not SLAM trajectory error.
No productionweights, acceptance rules, outputs or calibration changed.

Diagnostics do not establish that a joint two-time stereo/soft-gyro solve would
improve absolute SLAM. Request independent architecture critique before another
candidate; a new observation model requires one frozen all-case falsification,
not scalarweight sweeps or GT-guided frame selection.

One isolated attitude-world-alignment candidate actually scored on allten:
8PASS2FAIL, no newpasses. fresh2max16.1514227→16.1527936mm;
fresh4max16.1810366→16.2559919mm. Candidate REJECTED, production unchanged.
Allscore samplecounts/SE3alignment unchanged; auto visualsigma0.020m unchanged;
allcachedinputhashes unchanged. Do not mistake projectionbasis difference14.7mm
for a predicted14.7mm improvement in the actual graph; observed effect<0.075mm.

Originalcachedmeasurement scoring (GT evaluation only, not optimization) all166:
fresh2worstedge590→610 error15.684mm, originallySIFT78/162inliers and reproj
median1.995px/P953.641px, visualrotationdifference4.488deg; newfree replay rejects
SIFTrotationdisagrees after LK insufficientdepthpoints. fresh4worstedge1035→1075
10.767mm, also acceptedreplay; passingheldout4worstedge12.729mm. These arelocal
motionerror evaluations, not wholetrajectoryATE. Initialv1onlynewacceptedfree
subset incorrectly excludes3fresh2productionedges; supersede bycachedv2all166.

Four fresh2IRimages atfirst590/source620 andsecond610/source640 exactlymatch
preparedPNG vsoriginalDB3, zero pixelchanges/skew. Excludesimagecontentmismatch
for thispair only. CandidateRANSACseed/input-order solution sensitivity is an
unprovenhypothesis; evaluate all166uniformly, fixedfullinputs, tenfixedseeds.
