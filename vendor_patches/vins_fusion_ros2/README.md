# VINS-Fusion loop-safety patch archive

These patches preserve the local VINS-Fusion changes that are required by the
product SLAM evidence pipeline. They are stored here because the VINS checkout
tracks the public upstream repository rather than the project's backup remote.

## Provenance

- Source repository: `https://github.com/yangfuyuan/vins_fusion_ros2.git`
- Required base commit: `7148534563df5b8230d428b26242b14e873e6ffb`
- Patch 1 commit: `8de033e66aa5c8c2581cf95126860b322627f1e1`
- Patch 2 commit: `0f084f04a1e671458e2bacd31b22fb6cf754d32e`
- Patch 3 commit: `b3b9fd4d9ffcd5ba30b7fd417e3207785e91c566`

## Restore

From a VINS-Fusion checkout at the required base commit:

```bash
git am /path/to/ego_vio_humble/vendor_patches/vins_fusion_ros2/*.patch
```

The restored tree must end at commit-equivalent source state for `b3b9fd4`.
All patches were compiled in Release mode before archival. Patch 1 adds
reprojection and spatial-coverage diagnostics for accepted PnP loop edges.
Patch 2 rejects unusable or non-finite pose-graph solutions before they can be
written into the corrected trajectory. Patch 3 adds a configurable PnP spatial
support gate, validates its range before ROS initialization, and leaves it
disabled by default until the evidence gate qualifies a frozen threshold.

The archived default remains diagnostic-only (`min_loop_spatial_support: 0.0`):
no threshold may be enabled until independent true-loop and false-loop recordings
establish a release-qualified cutoff.
