# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 7.327 mm |
| ATE 平均 | 6.245 mm |
| ATE 最小 | 0.678 mm |
| ATE 中位 | 5.319 mm |
| ATE P95 | 11.866 mm |
| ATE 最大 | 29.802 mm |
| 10 mm 内比例 | 89.443% |
| 姿态 RMSE | 1.157° |
| RPE 平移 RMSE | 6.772 mm |
| 终点漂移 | 7.788 mm |
| Sim(3)形状诊断 RMSE | 7.281 mm |
| Sim(3)最优尺度(gt/estimate) | 1.004572 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
