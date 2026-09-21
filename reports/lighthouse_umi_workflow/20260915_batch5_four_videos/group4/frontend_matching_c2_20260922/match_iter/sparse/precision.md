# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 4.615 mm |
| ATE 平均 | 4.243 mm |
| ATE 最小 | 0.332 mm |
| ATE 中位 | 4.121 mm |
| ATE P95 | 6.975 mm |
| ATE 最大 | 12.720 mm |
| 10 mm 内比例 | 97.992% |
| 姿态 RMSE | 1.895° |
| RPE 平移 RMSE | 5.168 mm |
| 终点漂移 | 6.672 mm |
| Sim(3)形状诊断 RMSE | 4.614 mm |
| Sim(3)最优尺度(gt/estimate) | 0.999735 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
