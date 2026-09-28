# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 5.884 mm |
| ATE 平均 | 5.554 mm |
| ATE 最小 | 1.067 mm |
| ATE 中位 | 5.775 mm |
| ATE P95 | 8.715 mm |
| ATE 最大 | 13.801 mm |
| 10 mm 内比例 | 97.723% |
| 姿态 RMSE | 1.529° |
| RPE 平移 RMSE | 3.364 mm |
| 终点漂移 | 12.078 mm |
| Sim(3)形状诊断 RMSE | 5.373 mm |
| Sim(3)最优尺度(gt/estimate) | 1.011684 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
