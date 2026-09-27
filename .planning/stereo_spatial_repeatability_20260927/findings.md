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
