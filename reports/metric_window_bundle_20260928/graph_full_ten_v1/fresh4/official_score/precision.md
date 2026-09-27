# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 6.026 mm |
| ATE 平均 | 5.670 mm |
| ATE 最小 | 0.946 mm |
| ATE 中位 | 5.962 mm |
| ATE P95 | 9.060 mm |
| ATE 最大 | 14.016 mm |
| 10 mm 内比例 | 97.373% |
| 姿态 RMSE | 1.515° |
| RPE 平移 RMSE | 3.451 mm |
| 终点漂移 | 12.426 mm |
| Sim(3)形状诊断 RMSE | 5.453 mm |
| Sim(3)最优尺度(gt/estimate) | 1.012502 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
