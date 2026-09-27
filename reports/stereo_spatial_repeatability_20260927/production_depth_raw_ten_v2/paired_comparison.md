# Raw gyro PnP diagnostic (not a trajectory repair)

Cases: 10/10; same-method accepted pairs only; external GT never read.

Independent relative gyro has fixed calibration bias; reverse closure can improve through shared constraints and is not proof of absolute trajectory accuracy. VINS is not truth.

| Case | Paired edges | Closure free→gyro mm | Reprojection change px | VINS disagreement change mm |
|---|---:|---:|---:|---:|
| dev1 | 17 | 0.361→0.119 | 0.243 | -0.243 |
| dev2 | 17 | 0.721→0.303 | 0.077 | -0.074 |
| fresh1 | 16 | 0.578→0.206 | 0.287 | 0.159 |
| fresh2 | 15 | 0.406→0.061 | 0.117 | -0.407 |
| fresh3 | 14 | 0.271→0.075 | 0.185 | 0.161 |
| fresh4 | 17 | 0.538→0.146 | 0.228 | 0.018 |
| heldout1 | 20 | 0.427→0.198 | 0.236 | -0.497 |
| heldout2 | 16 | 0.334→0.094 | 0.216 | -0.159 |
| heldout3 | 17 | 0.319→0.144 | 0.387 | -0.060 |
| heldout4 | 16 | 2.670→0.353 | 0.127 | -0.363 |
