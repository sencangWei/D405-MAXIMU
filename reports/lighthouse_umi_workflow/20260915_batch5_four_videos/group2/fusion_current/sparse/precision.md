# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 3.657 mm |
| ATE 平均 | 3.056 mm |
| ATE 最小 | 0.322 mm |
| ATE 中位 | 2.555 mm |
| ATE P95 | 7.035 mm |
| ATE 最大 | 10.655 mm |
| 10 mm 内比例 | 99.656% |
| 姿态 RMSE | 2.220° |
| RPE 平移 RMSE | 5.260 mm |
| 终点漂移 | 3.814 mm |
| Sim(3)形状诊断 RMSE | 3.376 mm |
| Sim(3)最优尺度(gt/estimate) | 1.007581 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
