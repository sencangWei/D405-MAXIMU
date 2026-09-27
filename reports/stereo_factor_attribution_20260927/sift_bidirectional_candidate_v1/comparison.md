# Fixed bidirectional SIFT validation comparison

Completed: 10/10; PASS: 8. Production unchanged.

SE(3), no scale fit, original scored sample counts preserved; GT scoring-only.

| Case | Baseline max mm | Candidate max mm | Mean mm | P95 mm | Result |
|---|---:|---:|---:|---:|---|
| dev1 | 6.927 | 7.169 | 2.985 | 5.709 | PASS |
| dev2 | 8.769 | 8.694 | 3.844 | 6.334 | PASS |
| heldout1 | 6.089 | 6.070 | 2.532 | 4.292 | PASS |
| heldout2 | 6.062 | 6.026 | 3.626 | 5.227 | PASS |
| heldout3 | 6.746 | 7.038 | 4.031 | 6.255 | PASS |
| heldout4 | 9.024 | 9.033 | 3.493 | 5.164 | PASS |
| fresh1 | 7.900 | 8.307 | 2.537 | 6.124 | PASS |
| fresh2 | 16.151 | 14.382 | 4.648 | 8.424 | FAIL |
| fresh3 | 6.217 | 6.174 | 1.635 | 3.855 | PASS |
| fresh4 | 16.181 | 16.643 | 6.573 | 11.759 | FAIL |
