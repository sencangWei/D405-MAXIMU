# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 3.228 mm |
| ATE 平均 | 3.006 mm |
| ATE 最小 | 0.523 mm |
| ATE 中位 | 3.123 mm |
| ATE P95 | 4.866 mm |
| ATE 最大 | 12.960 mm |
| 10 mm 内比例 | 99.885% |
| 姿态 RMSE | 1.951° |
| RPE 平移 RMSE | 3.582 mm |
| 终点漂移 | 4.562 mm |
| Sim(3)形状诊断 RMSE | 3.052 mm |
| Sim(3)最优尺度(gt/estimate) | 1.005360 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
