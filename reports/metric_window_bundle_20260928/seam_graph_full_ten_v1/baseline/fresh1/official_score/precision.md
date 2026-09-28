# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 2.860 mm |
| ATE 平均 | 2.462 mm |
| ATE 最小 | 0.097 mm |
| ATE 中位 | 1.747 mm |
| ATE P95 | 5.990 mm |
| ATE 最大 | 7.809 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.314° |
| RPE 平移 RMSE | 2.541 mm |
| 终点漂移 | 8.013 mm |
| Sim(3)形状诊断 RMSE | 2.860 mm |
| Sim(3)最优尺度(gt/estimate) | 1.000190 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
