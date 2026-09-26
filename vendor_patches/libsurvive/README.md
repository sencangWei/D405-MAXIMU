# libsurvive patch backup: explicit Lighthouse four-way support gate

- Upstream source: `https://github.com/collabora/libsurvive`
- Tested source revision: `f1e6edd` (`driver_vive: fix infinite loop when closing HIDAPI devices`)
- Patch: `0001-explicit-four-way-lighthouse-support-gate.patch`
- Intended candidate settings for the current two-station layout: `min-lighthouse-count=2`, `min-measurements-per-lighthouse-axis=2`. Existing defaults are unchanged (`min-lighthouse-count=0`).
- Validation evidence is deliberately kept untracked under the workspace `reports/lighthouse_gss_coverage_20260926_valid_v1/`; do not add `reports/` to Git.
- This is an experimental patch, not yet integrated into the installed/product libsurvive build and not yet validated on more independent recordings or against metric ground truth.
