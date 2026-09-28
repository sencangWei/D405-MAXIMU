# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 3.706 mm |
| ATE 平均 | 3.339 mm |
| ATE 最小 | 0.160 mm |
| ATE 中位 | 2.823 mm |
| ATE P95 | 7.043 mm |
| ATE 最大 | 8.265 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.546° |
| RPE 平移 RMSE | 3.074 mm |
| 终点漂移 | 8.948 mm |
| Sim(3)形状诊断 RMSE | 3.687 mm |
| Sim(3)最优尺度(gt/estimate) | 1.001730 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
