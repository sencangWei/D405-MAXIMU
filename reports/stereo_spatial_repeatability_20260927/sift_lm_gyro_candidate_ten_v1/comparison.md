# SIFT free-PnP LM with raw-gyro validation

Cached LK, global scales and all other modules unchanged. GT evaluation only.

Scope: refined SIFT pose/scale and gyro-referenced rotation residual also change derived confidence under the unchanged formula. This is not a gate-only ablation.

|Case|Baseline max mm|Candidate max mm|Mean mm|P95 mm|Result|
|---|---:|---:|---:|---:|---|
|dev1|6.927|6.458|2.735|5.404|PASS|
|dev2|8.769|8.271|3.721|6.381|PASS|
|heldout1|6.089|5.921|2.473|4.210|PASS|
|heldout2|6.062|5.974|3.409|5.070|PASS|
|heldout3|6.746|6.831|3.765|5.833|PASS|
|heldout4|9.024|8.913|3.373|5.032|PASS|
|fresh1|7.900|7.809|2.462|5.990|PASS|
|fresh2|16.151|11.323|3.529|7.727|FAIL|
|fresh3|6.217|6.310|1.545|3.670|PASS|
|fresh4|16.181|15.333|5.979|10.118|FAIL|
