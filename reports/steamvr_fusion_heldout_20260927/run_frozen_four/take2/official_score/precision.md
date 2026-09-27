# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 8.458 mm |
| ATE 平均 | 7.520 mm |
| ATE 最小 | 0.801 mm |
| ATE 中位 | 7.335 mm |
| ATE P95 | 13.039 mm |
| ATE 最大 | 13.266 mm |
| 10 mm 内比例 | 67.629% |
| 姿态 RMSE | 0.892° |
| RPE 平移 RMSE | 3.380 mm |
| 终点漂移 | 10.356 mm |
| Sim(3)形状诊断 RMSE | 5.070 mm |
| Sim(3)最优尺度(gt/estimate) | 0.966828 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
