# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 7.070 mm |
| ATE 平均 | 6.066 mm |
| ATE 最小 | 0.641 mm |
| ATE 中位 | 5.389 mm |
| ATE P95 | 11.671 mm |
| ATE 最大 | 18.607 mm |
| 10 mm 内比例 | 84.452% |
| 姿态 RMSE | 2.678° |
| RPE 平移 RMSE | 4.622 mm |
| 终点漂移 | 11.285 mm |
| Sim(3)形状诊断 RMSE | 5.591 mm |
| Sim(3)最优尺度(gt/estimate) | 0.981239 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
