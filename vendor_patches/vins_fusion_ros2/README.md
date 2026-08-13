# VINS-Fusion loop-safety patch archive

These patches preserve the local VINS-Fusion changes that are required by the
product SLAM evidence pipeline. They are stored here because the VINS checkout
tracks the public upstream repository rather than the project's backup remote.

## Provenance

- Source repository: `https://github.com/yangfuyuan/vins_fusion_ros2.git`
- Required base commit: `7148534563df5b8230d428b26242b14e873e6ffb`
- Patch 1 commit: `8de033e66aa5c8c2581cf95126860b322627f1e1`
- Patch 2 commit: `0f084f04a1e671458e2bacd31b22fb6cf754d32e`

## Restore

From a VINS-Fusion checkout at the required base commit:

```bash
git am /path/to/ego_vio_humble/vendor_patches/vins_fusion_ros2/*.patch
```

The restored tree must end at commit-equivalent source state for `0f084f0`.
Both patches were compiled in Release mode before archival. Patch 1 adds
reprojection and spatial-coverage diagnostics for accepted PnP loop edges.
Patch 2 rejects unusable or non-finite pose-graph solutions before they can be
written into the corrected trajectory.

The PnP measurements are diagnostic only: no new spatial threshold is enabled
until independent true-loop and false-loop recordings establish a safe cutoff.
