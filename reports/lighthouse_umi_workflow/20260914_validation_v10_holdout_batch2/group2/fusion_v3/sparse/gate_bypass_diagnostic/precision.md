# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 6.326 mm |
| ATE 平均 | 5.469 mm |
| ATE 最小 | 0.604 mm |
| ATE 中位 | 5.011 mm |
| ATE P95 | 11.769 mm |
| ATE 最大 | 18.440 mm |
| 10 mm 内比例 | 90.168% |
| 姿态 RMSE | 1.611° |
| RPE 平移 RMSE | 7.221 mm |
| 终点漂移 | 7.165 mm |
| Sim(3)形状诊断 RMSE | 5.668 mm |
| Sim(3)最优尺度(gt/estimate) | 1.015482 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
