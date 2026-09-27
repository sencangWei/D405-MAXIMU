# Raw gyro PnP diagnostic (not a trajectory repair)

Cases: 10/10; same-method accepted pairs only; external GT never read.

Independent relative gyro has fixed calibration bias; reverse closure can improve through shared constraints and is not proof of absolute trajectory accuracy. VINS is not truth.

| Case | Paired edges | Closure free→gyro mm | Reprojection change px | VINS disagreement change mm |
|---|---:|---:|---:|---:|
| dev1 | 16 | 0.192→0.073 | 0.372 | -0.086 |
| dev2 | 17 | 0.676→0.375 | 0.064 | 0.179 |
| fresh1 | 16 | 0.814→0.395 | 0.297 | 0.321 |
| fresh2 | 13 | 0.304→0.110 | 0.376 | 0.030 |
| fresh3 | 14 | 0.202→0.144 | 0.163 | 0.208 |
| fresh4 | 15 | 0.375→0.168 | 0.483 | 0.090 |
| heldout1 | 19 | 0.558→0.301 | 0.264 | 0.515 |
| heldout2 | 15 | 0.248→0.209 | 0.203 | 0.095 |
| heldout3 | 16 | 0.238→0.145 | 0.555 | 0.462 |
| heldout4 | 15 | 1.553→0.252 | 0.300 | -0.465 |
